import os
from typing import Any, Dict, Optional

from fastapi import Body, FastAPI, HTTPException, Request
from pydantic import BaseModel

from logging_utils import configure_logger
from ngsi_client import get_traffic_signal, update_priority_corridor
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="MCP Server")
logger = configure_logger("mcp_server")

USER_TOKEN = os.getenv("USER_TOKEN", "user-token")


class McpCall(BaseModel):
    method: str
    params: Dict[str, Any]
    traceId: str
    token: Optional[str] = None


@app.post("/mcp")
async def handle_mcp(call: McpCall, request: Request):
    trace_id = call.traceId
    token = call.token or request.headers.get("Authorization", "").replace("Bearer ", "")
    if token != USER_TOKEN:
        logger.warning("Unauthorized MCP call", extra={"traceId": trace_id})
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        if call.method == "getTrafficSignalState":
            result = get_traffic_signal(call.params["entity_id"], trace_id, token)
        elif call.method == "setPriorityCorridor":
            result = update_priority_corridor(call.params["entity_id"], call.params["value"], trace_id, token)
        elif call.method == "notifyTrafficAgents":
            # Notification is simulated via logging for auditability.
            logger.info(
                "Notify traffic agents",
                extra={"traceId": trace_id, "extra_fields": {"message": call.params.get("message", "")}},
            )
            result = {"status": "notified"}
        else:
            raise HTTPException(status_code=400, detail="Unknown method")
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - simple demo error path
        logger.exception("MCP tool error", extra={"traceId": trace_id})
        raise HTTPException(status_code=500, detail=str(exc))

    logger.info("MCP call executed", extra={"traceId": trace_id, "extra_fields": {"method": call.method}})
    return {"result": result}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("MCP_PORT", 8000)))
