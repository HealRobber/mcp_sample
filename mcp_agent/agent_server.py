import os
import traceback
from typing import Any, Dict, Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from agent_core import run_agent

AGENT_API_KEY = os.getenv("AGENT_API_KEY", "")  # optional

app = FastAPI(title="MCP Agent API", version="0.1.2")

class AskRequest(BaseModel):
    question: str = Field(..., min_length=1)
    include_trace: bool = False

class AskResponse(BaseModel):
    result: Dict[str, Any]
    trace: Optional[list] = None

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest, x_api_key: Optional[str] = Header(default=None)):
    if AGENT_API_KEY:
        if not x_api_key or x_api_key != AGENT_API_KEY:
            raise HTTPException(status_code=401, detail="Invalid API key")

    try:
        result, trace = run_agent(req.question)
        if req.include_trace:
            return {"result": result, "trace": trace}
        return {"result": result}
    except Exception as e:
        tb = traceback.format_exc(limit=30)
        raise HTTPException(
            status_code=502,
            detail={
                "error": str(e),
                "where": "agent_server.ask -> run_agent",
                "traceback": tb,
            },
        )
