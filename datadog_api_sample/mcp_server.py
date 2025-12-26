# mcp_server.py
import os
from fastapi import FastAPI, Header, HTTPException
from datadog_api_client.v2.api.logs_api import LogsApi

from datadog_config import DatadogConfig, make_client
from datadog_api import current_error_services, increasing_error_services

from models.current_error_services_request import CurrentErrorServicesRequest
from models.increasing_error_services_request import IncreasingErrorServicesRequest

app = FastAPI(title="Datadog MCP Server", version="0.1.0")

def _check_api_key(x_api_key: str | None):
    expected = os.getenv("MCP_API_KEY", "")
    if expected:
        if not x_api_key or x_api_key != expected:
            raise HTTPException(status_code=401, detail="Invalid MCP API key")

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/tools/current-error-services")
def tool_current_error_services(
    req: CurrentErrorServicesRequest,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    _check_api_key(x_api_key)

    cfg = DatadogConfig()
    with make_client(cfg) as client:
        api = LogsApi(client)
        return current_error_services(
            api=api,
            cluster=req.cluster,
            status=req.status,
            namespace=req.namespace,
            window_minutes=req.window_minutes,
            limit=req.limit,
        )


@app.post("/tools/increasing-error-services")
def tool_increasing_error_services(
    req: IncreasingErrorServicesRequest,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    _check_api_key(x_api_key)

    cfg = DatadogConfig()
    with make_client(cfg) as client:
        api = LogsApi(client)
        return increasing_error_services(
            api=api,
            cluster=req.cluster,
            status=req.status,
            namespace=req.namespace,
            window_minutes=req.window_minutes,
            limit=req.limit,
            min_delta=req.min_delta,
            min_ratio=req.min_ratio,
        )

