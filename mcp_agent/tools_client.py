import os
import requests
from typing import Any, Dict, Optional

MCP_URL = os.getenv("MCP_URL", "http://datadog_api:8080").rstrip("/")
MCP_API_KEY = os.getenv("MCP_API_KEY", "")

def _headers() -> Dict[str, str]:
    h = {"Content-Type": "application/json"}
    if MCP_API_KEY:
        h["X-API-Key"] = MCP_API_KEY
    return h

def current_error_services(
    cluster: str,
    window_minutes: int = 15,
    limit: int = 10,
    status: str = "error",
    namespace: Optional[str] = None,
) -> Dict[str, Any]:
    payload = {
        "cluster": cluster,
        "window_minutes": window_minutes,
        "limit": limit,
        "status": status,
        "namespace": namespace,
    }
    r = requests.post(f"{MCP_URL}/tools/current-error-services", headers=_headers(), json=payload, timeout=30)
    r.raise_for_status()
    return r.json()

def increasing_error_services(
    cluster: str,
    window_minutes: int = 15,
    limit: int = 10,
    min_delta: int = 10,
    min_ratio: float = 2.0,
    status: str = "error",
    namespace: Optional[str] = None,
) -> Dict[str, Any]:
    payload = {
        "cluster": cluster,
        "window_minutes": window_minutes,
        "limit": limit,
        "min_delta": min_delta,
        "min_ratio": min_ratio,
        "status": status,
        "namespace": namespace,
    }
    r = requests.post(f"{MCP_URL}/tools/increasing-error-services", headers=_headers(), json=payload, timeout=30)
    r.raise_for_status()
    return r.json()
