from __future__ import annotations

import os
import uuid
from typing import Any, Dict, List, Optional

from fastapi import Body, FastAPI, HTTPException, Query, Response

from ..core.executor import execute_candidate_plan
from ..core.models import MonitorEvent
from ..core.planner import build_candidate_plan
from ..infra.audit import read_entries, record_event, verify_chain
from ..infra.logging_utils import configure_logger
from ..infra.metrics import render_latest, stage_timer
from ..infra.ngsi_client import create_subscription

logger = configure_logger("monitor")
app = FastAPI(title="Monitor Service")

MONITOR_CALLBACK_URL = os.getenv("MONITOR_CALLBACK_URL", "http://localhost:8010/monitor/notify")

TRAFFIC_SIGNAL_ID = os.getenv("TRAFFIC_SIGNAL_ID", "TrafficSignal:001")

def _get_attr_value(item: Dict[str, Any], key: str, default=None):
    """Extract value from NGSI-v2 attribute (supports both {value: X} and raw scalar)."""
    attr = item.get(key, default)
    if isinstance(attr, dict):
        return attr.get("value", default)
    return attr if attr is not None else default


def _notification_to_event(notification: Dict[str, Any]) -> MonitorEvent:
    data: List[Dict[str, Any]] = notification.get("data", [])
    if not data:
        return MonitorEvent(event_type="empty")

    item = data[0]
    entity_type = str(item.get("type", "")).lower()

    # --- WeatherObserved (from WeatherStation) ---
    if entity_type == "weatherobserved":
        precipitation = float(_get_attr_value(item, "precipitation", 0))
        humidity = float(_get_attr_value(item, "humidity", 0))
        pressure = float(_get_attr_value(item, "atmosphericPressure", 1013))
        location = str(_get_attr_value(item, "location", "unknown"))

        heavy_rain = precipitation > 0.50
        flood_risk = precipitation > 5.0 or pressure < 1005

        return MonitorEvent(
            event_type="weather",
            ambulance_detected=False,
            heavy_rain=heavy_rain,
            flood_risk=flood_risk,
            crowd_level="high" if flood_risk else ("normal" if not heavy_rain else "dense"),
            location=location,
            notes=f"precipitation={precipitation}mm humidity={humidity}% pressure={pressure}hPa",
        )

    # --- TrafficSignal or generic event ---
    weather = str(_get_attr_value(item, "weather", "normal")).lower()
    crowd = str(_get_attr_value(item, "crowd", "normal")).lower()
    event_type = str(_get_attr_value(item, "eventType", "combined")).lower()

    return MonitorEvent(
        event_type=event_type,
        ambulance_detected=bool(_get_attr_value(item, "ambulanceDetected", False)),
        heavy_rain=weather in {"rain", "storm", "heavy_rain"},
        flood_risk=bool(_get_attr_value(item, "floodRisk", False)),
        crowd_level=crowd,
        location=str(_get_attr_value(item, "location", "unknown")),
        notes=str(_get_attr_value(item, "notes", "")) or None,
    )


@app.post("/monitor/notify")
async def handle_notification(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    trace_id = str(uuid.uuid4())
    with stage_timer("monitor", "monitor") as timing:
        event = _notification_to_event(payload)
        record_event(
            component="monitor",
            event_type="EVENT_RECEIVED",
            trace_id=trace_id,
            actor="ngsi",
            outcome=event.event_type,
            payload={
                "event_type": event.event_type,
                "ambulance_detected": event.ambulance_detected,
                "heavy_rain": event.heavy_rain,
                "flood_risk": event.flood_risk,
                "crowd_level": event.crowd_level,
                "location": event.location,
                "raw_keys": list(payload.keys()),
            },
        )
        plan = build_candidate_plan(event, trace_id)
        report = execute_candidate_plan(plan)
    logger.info(
        "MAPE-K loop completed from monitor event",
        extra={
            "traceId": trace_id,
            "extra_fields": {
                "scenario": event.event_type,
                "executed": report.executed,
                "policy_mode": report.policy.approval_mode.value,
                "duration_ms": timing["duration_ms"],
            },
        },
    )
    record_event(
        component="monitor",
        event_type="LOOP_COMPLETED",
        trace_id=trace_id,
        plan_id=report.plan_id,
        actor="monitor",
        outcome="executed" if report.executed else "not_executed",
        payload={
            "scenario": event.event_type,
            "executed": report.executed,
            "policy_mode": report.policy.approval_mode.value,
            "risk_level": report.policy.risk_level.value,
            "duration_ms": timing["duration_ms"],
        },
    )
    return {
        "traceId": trace_id,
        "planId": report.plan_id,
        "executed": report.executed,
        "policy": report.policy.model_dump(),
    }


@app.get("/metrics")
def metrics() -> Response:
    body, content_type = render_latest()
    return Response(content=body, media_type=content_type)


@app.get("/audit/entries")
def audit_entries(
    trace_id: Optional[str] = Query(default=None),
    plan_id: Optional[str] = Query(default=None),
    component: Optional[str] = Query(default=None),
    event_type: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=5000),
) -> Dict[str, Any]:
    entries = read_entries(
        trace_id=trace_id,
        plan_id=plan_id,
        component=component,
        event_type=event_type,
        limit=limit,
    )
    return {"count": len(entries), "entries": entries}


@app.get("/audit/entries/{entry_id}")
def audit_entry(entry_id: str) -> Dict[str, Any]:
    for entry in read_entries():
        if entry.get("id") == entry_id:
            return entry
    raise HTTPException(status_code=404, detail="Audit entry not found")


@app.get("/audit/verify")
def audit_verify() -> Dict[str, Any]:
    return verify_chain()


def register_default_subscription() -> Dict[str, Any]:
    trace_id = str(uuid.uuid4())
    subscription = {
        "description": "Monitor traffic/weather events",
        "subject": {
            "entities": [{"id": TRAFFIC_SIGNAL_ID, "type": "TrafficSignal"}],
            "condition": {"attrs": ["status", "priorityCorridor"]},
        },
        "notification": {
            "http": {"url": MONITOR_CALLBACK_URL},
            "attrs": ["status", "priorityCorridor", "location"],
        },
        "throttling": 1,
    }
    return create_subscription(subscription, trace_id)
