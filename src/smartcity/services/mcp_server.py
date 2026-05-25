import os
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

from ..infra.audit import record_event
from ..infra.fiware_mcp_client import update_attribute
from ..infra.logging_utils import configure_logger
from ..infra.metrics import (
    ERRORS_TOTAL,
    MCP_CALLS_TOTAL,
    render_latest,
    stage_timer,
)
from ..infra.ngsi_client import get_traffic_signal, update_priority_corridor
from ..infra.pump_mcp_client import get_pump_status, turn_on_pump, turn_off_pump

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
        MCP_CALLS_TOTAL.labels(method=call.method, status="401").inc()
        ERRORS_TOTAL.labels(component="mcp_server", kind="unauthorized").inc()
        logger.warning("Unauthorized MCP call", extra={"traceId": trace_id})
        record_event(
            component="mcp_server",
            event_type="MCP_UNAUTHORIZED",
            trace_id=trace_id,
            actor="mcp_server",
            outcome="unauthorized",
            payload={"method": call.method, "params": call.params},
        )
        raise HTTPException(status_code=401, detail="Invalid token")

    status_label = "200"
    try:
        with stage_timer("mcp_call_server", "mcp_server") as timing:
            if call.method == "getTrafficSignalState":
                result = get_traffic_signal(call.params["entity_id"], trace_id, token)

            elif call.method == "setPriorityCorridor":
                result = update_priority_corridor(
                    call.params["entity_id"], call.params["value"], trace_id, token
                )

            elif call.method == "notifyTrafficAgents":
                logger.info(
                    "Notify traffic agents",
                    extra={
                        "traceId": trace_id,
                        "extra_fields": {"message": call.params.get("message", "")},
                    },
                )
                result = {"status": "notified"}

            elif call.method == "getPumpStatus":
                result = await get_pump_status(call.params["pump_id"])

            elif call.method == "turnOnPump":
                pump_id = call.params["pump_id"]
                # 1. Actua no dispositivo físico via Pump MCP
                result = await turn_on_pump(pump_id)
                # 2. Atualiza o digital twin no Orion via FIWARE MCP
                if result.get("success"):
                    pump = result.get("pump", {})
                    orion_id = pump_id.replace("Pump:", "PumpDevice:")
                    await update_attribute(orion_id, "status", "on")
                    await update_attribute(orion_id, "flow_rate_m3h", str(pump.get("flow_rate_m3h", 0)))

            elif call.method == "turnOffPump":
                pump_id = call.params["pump_id"]
                # 1. Actua no dispositivo físico via Pump MCP
                result = await turn_off_pump(pump_id)
                # 2. Atualiza o digital twin no Orion via FIWARE MCP
                if result.get("success"):
                    orion_id = pump_id.replace("Pump:", "PumpDevice:")
                    await update_attribute(orion_id, "status", "off")
                    await update_attribute(orion_id, "flow_rate_m3h", "0.0")

            else:
                status_label = "400"
                raise HTTPException(status_code=400, detail="Unknown method")

    except HTTPException as http_exc:
        status_label = str(http_exc.status_code)
        MCP_CALLS_TOTAL.labels(method=call.method, status=status_label).inc()
        record_event(
            component="mcp_server",
            event_type="MCP_CALL_REJECTED",
            trace_id=trace_id,
            actor="mcp_server",
            outcome=status_label,
            payload={
                "method": call.method,
                "params": call.params,
                "detail": str(http_exc.detail),
            },
        )
        raise
    except Exception as exc:  # pragma: no cover
        status_label = "500"
        MCP_CALLS_TOTAL.labels(method=call.method, status=status_label).inc()
        ERRORS_TOTAL.labels(component="mcp_server", kind="tool_error").inc()
        logger.exception("MCP tool error", extra={"traceId": trace_id})
        record_event(
            component="mcp_server",
            event_type="MCP_CALL_ERROR",
            trace_id=trace_id,
            actor="mcp_server",
            outcome="error",
            payload={
                "method": call.method,
                "params": call.params,
                "error": str(exc),
            },
        )
        raise HTTPException(status_code=500, detail=str(exc))

    MCP_CALLS_TOTAL.labels(method=call.method, status=status_label).inc()
    logger.info(
        "MCP call executed",
        extra={
            "traceId": trace_id,
            "extra_fields": {
                "method": call.method,
                "duration_ms": timing["duration_ms"],
            },
        },
    )
    record_event(
        component="mcp_server",
        event_type="MCP_CALL",
        trace_id=trace_id,
        actor="mcp_server",
        outcome="ok",
        payload={
            "method": call.method,
            "params": call.params,
            "duration_ms": timing["duration_ms"],
        },
    )
    return {"result": result}


@app.get("/metrics")
def metrics() -> Response:
    body, content_type = render_latest()
    return Response(content=body, media_type=content_type)
