from datetime import datetime, timedelta
from typing import Any, Dict, List

from datadog_api_client.v2.api.logs_api import LogsApi
from datadog_api_client.v2.model.logs_query_filter import LogsQueryFilter
from datadog_api_client.v2.model.logs_sort import LogsSort
from datadog_api_client.v2.model.logs_list_request import LogsListRequest

from datadog_config import DatadogConfig, utc_now, iso, make_client

# ----------------------------
# Core: Logs Aggregate (Top N by group)
# ----------------------------

def aggregate_top(
    api: LogsApi,
    query: str,
    time_from: datetime,
    time_to: datetime,
    facet: str,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """
    facet 기준으로 count 집계를 뽑습니다.
    - dict body 사용: datadog_api_client 버전/enum 차이로 깨지지 않게 함
    - 반환: [{"key": <facet_value>, "count": <int>}, ...]
    """
    body = {
        "filter": {
            "from": iso(time_from),
            "to": iso(time_to),
            "query": query,
        },
        "compute": [{"aggregation": "count"}],
        "group_by": [
            {
                "facet": facet,
                "limit": limit,
                "sort": {"type": "measure", "aggregation": "count", "order": "desc"},
            }
        ],
    }

    resp = api.aggregate_logs(body=body)

    buckets: List[Dict[str, Any]] = []
    for b in (resp.data.buckets or []):
        key = (b.by or {}).get(facet)
        cnt = (b.computes or {}).get("c0", 0)
        if key:
            buckets.append({"key": key, "count": int(cnt)})

    return buckets


def aggregate_top_services(
    api: LogsApi,
    query: str,
    time_from: datetime,
    time_to: datetime,
    limit: int = 10,
    service_facet: str = "service",
) -> List[Dict[str, Any]]:
    """
    Returns list of {service, count} sorted by count desc.
    """
    rows = aggregate_top(
        api=api,
        query=query,
        time_from=time_from,
        time_to=time_to,
        facet=service_facet,
        limit=limit,
    )
    return [{"service": r["key"], "count": r["count"]} for r in rows]


# ----------------------------
# Core: Logs Samples (evidence)
# ----------------------------

def _extract_message(item) -> str:
    """
    LogsListResponse에서 message 후보를 최대한 안전하게 추출합니다.
    환경마다 message 경로가 달라서 방어적으로 구현합니다.
    """
    if not item or not getattr(item, "attributes", None):
        return "(no attributes)"

    attrs = item.attributes

    # 1) 가장 흔한 경로: attributes.message
    msg = getattr(attrs, "message", None)
    if msg:
        return str(msg)

    # 2) 일부는 attributes.attributes["message"]
    nested = getattr(attrs, "attributes", None)
    if isinstance(nested, dict):
        if "message" in nested and nested["message"]:
            return str(nested["message"])
        # 다른 흔한 키들도 후보로
        for k in ("msg", "log", "error", "exception"):
            if k in nested and nested[k]:
                return str(nested[k])

    return "(message field not found)"


def sample_logs_for_service(
    api: LogsApi,
    base_query: str,
    service_name: str,
    time_from: datetime,
    time_to: datetime,
    limit: int = 2,
) -> List[str]:
    """
    Return short message snippets for evidence.
    """
    q = f"{base_query} service:{service_name}"

    req = LogsListRequest(
        filter=LogsQueryFilter(
            _from=iso(time_from),
            to=iso(time_to),
            query=q,
        ),
        sort=LogsSort.TIMESTAMP_DESCENDING,
        page={"limit": limit},
    )

    resp = api.list_logs(body=req)

    out: List[str] = []
    for item in (resp.data or []):
        out.append(_extract_message(item)[:300])

    return out



def build_log_query(
    cluster: str,
    status: str = "error",
    namespace: str | None = None,
) -> str:
    parts = []
    parts.append(f"kube_cluster_name:{cluster}")

    if status:
        parts.append(f"status:{status}")

    if namespace:
        parts.append(f"kube_namespace:{namespace}")

    # 서비스가 없으면 서비스 facet group_by가 비어 보일 수 있어 service:*를 유지하는 것도 가능
    parts.append("service:*")

    return " ".join(parts)



# ----------------------------
# Use-case 1: Current error services
# ----------------------------

def current_error_services(
    api: LogsApi,
    cluster: str,
    status: str = "error",
    namespace: str | None = None,
    window_minutes: int = 15,
    limit: int = 10,
) -> Dict[str, Any]:

    now = utc_now()
    t_from = now - timedelta(minutes=window_minutes)

    # ✅ 기준 베이스 쿼리 고정
    base = build_log_query(cluster=cluster, status=status, namespace=namespace)

    top = aggregate_top_services(api, base, t_from, now, limit=limit)

    for row in top:
        row["samples"] = sample_logs_for_service(api, base, row["service"], t_from, now, limit=2)

    return {
        "summary": f"최근 {window_minutes}분 동안 에러가 발생한 서비스 Top {limit}",
        "window": {"from": iso(t_from), "to": iso(now)},
        "query": base,
        "services": top,
    }


# ----------------------------
# Use-case 2: Increasing error services (current vs previous window)
# ----------------------------

def increasing_error_services(
    api: LogsApi,
    cluster: str,
    status: str = "error",
    namespace: str | None = None,
    window_minutes: int = 15,
    limit: int = 10,
    min_delta: int = 10,
    min_ratio: float = 2.0,
) -> Dict[str, Any]:

    now = utc_now()
    cur_from = now - timedelta(minutes=window_minutes)
    prev_to = cur_from
    prev_from = prev_to - timedelta(minutes=window_minutes)

    base = build_log_query(cluster=cluster, status=status, namespace=namespace)

    # 집계는 넉넉히 받아서(예: 1000) MCP/서버에서 계산하는 게 안정적입니다.
    cur = aggregate_top_services(api, base, cur_from, now, limit=1000)
    prev = aggregate_top_services(api, base, prev_from, prev_to, limit=1000)

    prev_map = {r["service"]: r["count"] for r in prev}

    rows: List[Dict[str, Any]] = []
    for r in cur:
        s = r["service"]
        cur_c = r["count"]
        prev_c = prev_map.get(s, 0)

        delta = cur_c - prev_c
        ratio = (cur_c / max(prev_c, 1)) if cur_c > 0 else 0.0

        if delta >= min_delta and ratio >= min_ratio:
            rows.append(
                {
                    "service": s,
                    "previous": prev_c,
                    "current": cur_c,
                    "delta": delta,
                    "ratio": round(ratio, 2),
                }
            )

    rows.sort(key=lambda x: (x["ratio"], x["delta"]), reverse=True)
    rows = rows[:limit]

    for row in rows:
        row["samples"] = sample_logs_for_service(api, base, row["service"], cur_from, now, limit=2)

    return {
        "summary": f"최근 {window_minutes}분 동안 에러가 증가한 서비스 Top {limit}",
        "comparison": {
            "current": {"from": iso(cur_from), "to": iso(now)},
            "previous": {"from": iso(prev_from), "to": iso(prev_to)},
        },
        "threshold": {"min_delta": min_delta, "min_ratio": min_ratio},
        "query": base,
        "services": rows,
    }


# ----------------------------
# Example run
# ----------------------------

if __name__ == "__main__":
    cfg = DatadogConfig()
    with make_client(cfg) as client:
        api = LogsApi(client)

        print(current_error_services(api, cluster="marios-stg-eks", window_minutes=15, limit=10))
        print(
            increasing_error_services(
                api,
                cluster="marios-stg-eks",
                window_minutes=15,
                limit=10,
                min_delta=10,
                min_ratio=2.0,
            )
        )