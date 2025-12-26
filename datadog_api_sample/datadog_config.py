import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from datadog_api_client import ApiClient, Configuration

@dataclass(frozen=True)
class DatadogConfig:
    """
    환경변수 규칙
    - DD_SITE: datadoghq.com / datadoghq.eu / us5.datadoghq.com 등 (권장)
    - 또는 DD_API_HOST: https://api.datadoghq.com 같은 풀 URL도 허용(하위 호환)
    - DD_API_KEY / DD_APP_KEY: 필수
    """
    # 우선순위: DD_SITE(도메인) -> DD_API_HOST(풀 URL or 도메인)
    site: Optional[str] = os.getenv("DD_SITE") or os.getenv("DD_API_HOST")
    api_key: Optional[str] = os.getenv("DD_API_KEY")
    app_key: Optional[str] = os.getenv("DD_APP_KEY")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat()


def _normalize_host(site: str) -> str:
    """
    site 입력이 아래 어떤 형태든 안전하게 Datadog API host URL로 정규화
    - "datadoghq.com"           -> "https://api.datadoghq.com"
    - "us5.datadoghq.com"       -> "https://api.us5.datadoghq.com"
    - "https://api.datadoghq.com" -> 그대로 유지
    - "https://api.us5.datadoghq.com" -> 그대로 유지
    """
    s = (site or "").strip()
    if not s:
        # 기본값 (미설정 시)
        return "https://api.datadoghq.com"

    # full url이 들어오면 그대로 사용
    if s.startswith("http://") or s.startswith("https://"):
        return s.rstrip("/")

    # site 도메인만 들어오면 api.<site>로 구성
    # (사용자가 실수로 "api.datadoghq.com"을 넣는 경우도 처리)
    if s.startswith("api."):
        return f"https://{s}".rstrip("/")

    return f"https://api.{s}".rstrip("/")


def make_client(cfg: DatadogConfig) -> ApiClient:
    # 필수 env 검증 (여기서 바로 실패시키는 게 디버깅에 유리합니다)
    if not cfg.api_key:
        raise RuntimeError("DD_API_KEY 환경변수가 비어있습니다.")
    if not cfg.app_key:
        raise RuntimeError("DD_APP_KEY 환경변수가 비어있습니다.")

    host = _normalize_host(cfg.site or "datadoghq.com")

    configuration = Configuration(
        host=host,
        api_key={"apiKeyAuth": cfg.api_key, "appKeyAuth": cfg.app_key},
    )
    return ApiClient(configuration)
