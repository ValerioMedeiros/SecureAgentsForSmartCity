import os
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from ..infra.fiware_mcp_client import get_entity, update_attribute
from ..infra.logging_utils import configure_logger

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
    token = call.token or request.headers.get("Authorization", "").replace(
        "Bearer ", ""
    )
    if token != USER_TOKEN:
        logger.warning("Unauthorized MCP call", extra={"traceId": trace_id})
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        if call.method == "getTrafficSignalState":
            result = await get_entity(call.params["entity_id"])

        elif call.method == "setPriorityCorridor":
            result = await update_attribute(
                entity_id=call.params["entity_id"],
                attribute="priorityCorridor",
                value=call.params["value"],
            )

        elif call.method == "notifyTrafficAgents":
            # Notification is simulated via logging for auditability.
            # Replace with sendWhatsAppAlert tool call when WhatsApp integration is added.
            logger.info(
                "Notify traffic agents",
                extra={
                    "traceId": trace_id,
                    "extra_fields": {"message": call.params.get("message", "")},
                },
            )
            result = {"status": "notified"}

        else:
            raise HTTPException(status_code=400, detail=f"Unknown method: {call.method}")

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("MCP tool error", extra={"traceId": trace_id})
        raise HTTPException(status_code=500, detail=str(exc))

    logger.info(
        "MCP call executed",
        extra={"traceId": trace_id, "extra_fields": {"method": call.method}},
    )
    return {"result": result}
