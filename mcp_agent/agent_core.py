import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

from prompts import TOOL_SPEC
from tools_client import current_error_services, increasing_error_services

# Load .env early (safe even in container)
load_dotenv()

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")

# Safety limits (server-side enforcement)
MAX_TOOL_CALLS = int(os.getenv("AGENT_MAX_TOOL_CALLS", "2"))

# ✅ window limits: default up to 2 weeks (20160 minutes)
WINDOW_MIN = int(os.getenv("AGENT_WINDOW_MIN", "5"))
WINDOW_MAX = int(os.getenv("AGENT_WINDOW_MAX", "20160"))

LIMIT_MIN = int(os.getenv("AGENT_LIMIT_MIN", "1"))
LIMIT_MAX = int(os.getenv("AGENT_LIMIT_MAX", "20"))

# ✅ Language enforcement retries
MAX_LANG_RETRY = int(os.getenv("AGENT_LANG_RETRY", "3"))

# ✅ Force Korean-only & JSON-only output
LANG_SPEC = """
당신은 SRE/AIOps 보조 에이전트입니다.

[필수 규칙]
- 모든 자연어 출력은 반드시 한국어로만 작성하십시오. (중국어/영어/일본어 금지)
- 반드시 JSON만 출력하십시오. JSON 외 텍스트(설명, 마크다운, 코드블록) 금지.
- JSON 키는 TOOL_SPEC에 정의된 영문 키를 그대로 사용하십시오.
- title/summary/findings/next_actions의 모든 문자열은 한국어로만 작성하십시오.
- next_actions는 3~5개로 작성하고, 각 항목은 실행 가능한 짧은 한국어 문장으로 작성하십시오.
- 시스템 오류/제약 안내가 필요하면 한국어로만 작성하십시오.
- 비한국어(특히 중국어/한자)가 포함되면 즉시 잘못된 출력입니다. 절대로 포함하지 마십시오.
""".strip()


def clamp_int(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def clamp_float(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def llm_chat(messages: List[Dict[str, str]]) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {"temperature": 0.2},
    }
    r = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=90)
    r.raise_for_status()
    data = r.json()
    return data["message"]["content"]


# ----------------------------
# JSON extraction (robust)
# ----------------------------

def extract_first_json_object(text: str) -> Optional[Dict[str, Any]]:
    """
    LLM이 JSON 앞뒤에 잡문을 붙여도, 첫 번째 JSON 객체만 안정적으로 추출합니다.
    - 단순 정규식 {.*}는 깨지기 쉬워서, brace counter 방식으로 추출합니다.
    """
    if not text:
        return None

    start = text.find("{")
    if start < 0:
        return None

    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start:i + 1]
                try:
                    return json.loads(candidate)
                except Exception:
                    return None
    return None


# ----------------------------
# Language enforcement helpers
# ----------------------------

def contains_cjk(text: str) -> bool:
    # Chinese Han characters (CJK Unified Ideographs)
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def sanitize_korean_only(obj: Dict[str, Any]) -> Dict[str, Any]:
    """
    최종 방어막:
    - final JSON 내부에 한자(중국어)가 남아 있으면 해당 문자열을 한국어 안내 문구로 치환
    """
    def _fix_str(s: str) -> str:
        if not isinstance(s, str):
            return s
        if contains_cjk(s):
            return "한국어로만 출력해야 하나, 모델 출력에 비한국어 문구가 포함되어 일부 내용을 생략했습니다."
        return s

    def _walk(x: Any) -> Any:
        if isinstance(x, dict):
            return {k: _walk(v) for k, v in x.items()}
        if isinstance(x, list):
            return [_walk(v) for v in x]
        if isinstance(x, str):
            return _fix_str(x)
        return x

    return _walk(obj)


# ----------------------------
# Question parsing (deterministic)
# ----------------------------

def extract_namespace_from_question(question: str) -> Optional[str]:
    # 예: "dtslm 네임스페이스", "dtslm namespace"
    m = re.search(r"\b([a-z0-9][a-z0-9\-]*)\s*(네임스페이스|namespace)\b", question, flags=re.IGNORECASE)
    if not m:
        return None
    return m.group(1).strip()


def extract_cluster_from_question(question: str) -> Optional[str]:
    # 예: "marios-prd-eks 클러스터", "marios-stg-eks cluster"
    m = re.search(r"\b([a-z0-9][a-z0-9\-]*)\s*(클러스터|cluster)\b", question, flags=re.IGNORECASE)
    if not m:
        return None
    return m.group(1).strip()


def parse_window_minutes_ko(question: str) -> Optional[int]:
    """
    예: "최근 2주일", "최근 14일", "최근 48시간", "최근 30분"
    공백이 섞여도 동작하도록 처리합니다.
    """
    q = question.replace(" ", "")

    # "최근2주일간", "최근2주", "최근14일간", "최근48시간", "최근30분"
    m = re.search(r"최근(\d+)(주일|주|일|시간|분)", q)
    if not m:
        return None

    n = int(m.group(1))
    unit = m.group(2)

    if unit in ("주일", "주"):
        return n * 7 * 24 * 60
    if unit == "일":
        return n * 24 * 60
    if unit == "시간":
        return n * 60
    if unit == "분":
        return n
    return None


def choose_status_from_question(question: str) -> str:
    """
    지금은 status를 거의 'error'로 고정하고 싶다는 사용자님 요구에 맞춰:
    - 기본: error
    - 사용자가 명시적으로 warn/info/debug를 말한 경우만 반영(선택)
    """
    q = question.lower()
    if "warn" in q or "warning" in q or "경고" in question:
        return "warn"
    if "info" in q or "정보" in question:
        return "info"
    if "debug" in q or "디버그" in question:
        return "debug"
    return "error"


# ----------------------------
# Tool call validation/normalize
# ----------------------------

def validate_and_normalize_call(call: Dict[str, Any]) -> Dict[str, Any]:
    if call.get("action") != "tool_call":
        raise ValueError("Not a tool_call action")

    tool = call.get("tool")
    args = call.get("args") or {}

    if tool not in ("current_error_services", "increasing_error_services"):
        raise ValueError(f"Tool not allowed: {tool}")

    # cluster MUST be explicitly specified (no guessing)
    cluster = args.get("cluster")
    if not isinstance(cluster, str) or not cluster.strip():
        raise ValueError("cluster must be specified explicitly")
    args["cluster"] = cluster.strip()

    # status: optional, default "error"
    status = args.get("status", "error")
    if status is None:
        status = "error"
    if not isinstance(status, str):
        raise ValueError("status must be a string")
    status = status.strip() or "error"
    args["status"] = status

    # namespace: optional (없으면 None)
    namespace = args.get("namespace", None)
    if namespace is None:
        args["namespace"] = None
    else:
        if not isinstance(namespace, str):
            raise ValueError("namespace must be a string or null")
        namespace = namespace.strip()
        args["namespace"] = namespace if namespace else None

    window = int(args.get("window_minutes", 15))
    limit = int(args.get("limit", 10))
    args["window_minutes"] = clamp_int(window, WINDOW_MIN, WINDOW_MAX)
    args["limit"] = clamp_int(limit, LIMIT_MIN, LIMIT_MAX)

    if tool == "increasing_error_services":
        args["min_delta"] = clamp_int(int(args.get("min_delta", 10)), 1, 999999)
        args["min_ratio"] = clamp_float(float(args.get("min_ratio", 2.0)), 1.0, 100.0)

    call["args"] = args
    return call


# ----------------------------
# Tool runner
# ----------------------------

def run_tool(tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    if tool == "current_error_services":
        return current_error_services(**args)
    if tool == "increasing_error_services":
        return increasing_error_services(**args)
    raise ValueError("Unknown tool")


def compact_tool_result(tool: str, result: Dict[str, Any]) -> Dict[str, Any]:
    services = result.get("services") or []
    top = services[:5]
    out = []
    for s in top:
        row = {k: s.get(k) for k in ("service", "count", "previous", "current", "delta", "ratio")}
        samples = s.get("samples") or []
        if samples:
            row["sample"] = str(samples[0])[:220]
        out.append(row)

    return {
        "tool": tool,
        "summary": result.get("summary"),
        "window": result.get("window") or result.get("comparison"),
        "services_total": len(services),
        "services_top5": out,
    }


# ----------------------------
# Agent main loop
# ----------------------------

def run_agent(question: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Returns: (final_json, trace)
      - final_json: the JSON object produced as action=final
      - trace: list of steps (tool_call + tool_result summary)
    """
    messages: List[Dict[str, str]] = [
        {"role": "system", "content": LANG_SPEC},
        {"role": "system", "content": TOOL_SPEC},
        {"role": "user", "content": question},
    ]

    tool_calls = 0
    lang_retry = 0
    trace: List[Dict[str, Any]] = []

    # 질문 기반 deterministic hints
    hinted_cluster = extract_cluster_from_question(question)
    hinted_namespace = extract_namespace_from_question(question)
    hinted_window = parse_window_minutes_ko(question)
    hinted_status = choose_status_from_question(question)

    while True:
        text = llm_chat(messages)

        # 1차: raw에 한자가 섞이면 즉시 재요청(최대 2회)
        raw_retry = 0
        while contains_cjk(text) and raw_retry < 2:
            trace.append({"type": "llm_raw_rejected_language", "content": text, "retry": raw_retry + 1})
            messages.append({"role": "assistant", "content": text})
            messages.append({
                "role": "user",
                "content": (
                    "방금 출력에 중국어/한자 문자가 포함되었습니다.\n"
                    "- 중국어/한자/영어를 절대 사용하지 마십시오.\n"
                    "- 한국어로만, 그리고 JSON만 출력하십시오.\n"
                    "- 반드시 JSON 객체 1개만 출력하십시오."
                ),
            })
            text = llm_chat(messages)
            raw_retry += 1

        obj = extract_first_json_object(text)

        if not obj:
            final = {
                "action": "final",
                "title": "LLM 출력 오류",
                "summary": "LLM이 유효한 JSON을 반환하지 않았습니다. 모델/프롬프트 규칙을 확인하십시오.",
                "findings": [],
                "next_actions": [
                    "OLLAMA_MODEL과 TOOL_SPEC 규칙을 확인합니다.",
                    "LLM이 JSON만 출력하도록 system prompt를 강화합니다.",
                    "동일 질문으로 재시도해 출력 형식을 확인합니다.",
                ],
            }
            trace.append({"type": "llm_raw", "content": text})
            return final, trace

        action = obj.get("action")

        if action == "final":
            dump = json.dumps(obj, ensure_ascii=False)

            # final 단계 한자 포함 시 재생성/정화
            if contains_cjk(dump):
                lang_retry += 1
                trace.append({"type": "final_rejected_language", "content": obj, "retry": lang_retry})

                if lang_retry >= MAX_LANG_RETRY:
                    sanitized = sanitize_korean_only(obj)
                    trace.append({"type": "final_sanitized", "content": sanitized})
                    return sanitized, trace

                messages.append({"role": "assistant", "content": dump})
                messages.append({
                    "role": "user",
                    "content": (
                        "방금 출력에 중국어/한자 문자가 포함되었습니다.\n"
                        "- 중국어/한자/영어를 절대 사용하지 마십시오.\n"
                        "- 한국어로만 다시 작성하십시오.\n"
                        "- 반드시 action=final JSON 1개만 출력하십시오.\n"
                        "- 특히 findings/next_actions에 비한국어 문자가 들어가면 안 됩니다."
                    ),
                })
                continue

            trace.append({"type": "final", "content": obj})
            return obj, trace

        if action == "tool_call":
            if tool_calls >= MAX_TOOL_CALLS:
                messages.append({"role": "assistant", "content": json.dumps(obj, ensure_ascii=False)})
                messages.append({"role": "user", "content": "Tool call limit reached. Output action=final JSON now (Korean-only, JSON-only)."})
                trace.append({"type": "tool_call_rejected", "reason": "limit_reached", "call": obj})
                continue

            # ✅ LLM이 빼먹을 때를 대비해 질문 기반으로 args를 보정 주입
            obj.setdefault("args", {})
            args_obj = obj["args"] or {}
            if hinted_cluster and (not isinstance(args_obj.get("cluster"), str) or not args_obj.get("cluster", "").strip()):
                args_obj["cluster"] = hinted_cluster
            if hinted_namespace and args_obj.get("namespace") in (None, "", "null"):
                args_obj["namespace"] = hinted_namespace
            if hinted_window:
                args_obj["window_minutes"] = hinted_window
            if hinted_status and (args_obj.get("status") is None):
                args_obj["status"] = hinted_status
            obj["args"] = args_obj

            try:
                call = validate_and_normalize_call(obj)
            except ValueError as e:
                messages.append({"role": "assistant", "content": json.dumps(obj, ensure_ascii=False)})
                messages.append({
                    "role": "user",
                    "content": f"Tool call is invalid: {str(e)}. 사용자에게 필요한 추가 정보를 한국어로 질문하고 action=final JSON으로 종료하십시오.",
                })
                trace.append({"type": "tool_call_invalid", "error": str(e), "call": obj})
                continue

            tool = call["tool"]
            args = call["args"]

            tool_calls += 1
            result = run_tool(tool, args)
            compact = compact_tool_result(tool, result)

            trace.append({"type": "tool_call", "call": call})
            trace.append({"type": "tool_result", "result": compact})

            messages.append({"role": "assistant", "content": json.dumps(call, ensure_ascii=False)})
            messages.append({
                "role": "user",
                "content": "Tool result:\n"
                           + json.dumps(compact, ensure_ascii=False, indent=2)
                           + "\n\n다음 단계 결정: 추가 드릴다운이 도움이 되면 또 다른 tool_call을 하십시오. 아니면 action=final로 한국어 JSON만 출력하십시오.",
            })
            continue

        final = {
            "action": "final",
            "title": "알 수 없는 action",
            "summary": f"LLM이 알 수 없는 action을 반환했습니다: {action}",
            "findings": [obj],
            "next_actions": [
                "TOOL_SPEC의 action 정의를 점검합니다.",
                "모델이 JSON 규칙을 준수하는지 확인합니다.",
                "동일 질문으로 재시도하여 출력 패턴을 확인합니다.",
            ],
        }
        trace.append({"type": "unknown_action", "content": obj})
        return final, trace
