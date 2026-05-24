from __future__ import annotations

import os
import uuid
from typing import Any, Dict, List

from fastapi import Body, FastAPI

from ..core.executor import execute_candidate_plan
from ..core.models import MonitorEvent
from ..core.planner import build_candidate_plan
from ..infra.logging_utils import configure_logger
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
    event = _notification_to_event(payload)
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
            },
        },
    )
    return {
        "traceId": trace_id,
        "planId": report.plan_id,
        "executed": report.executed,
        "policy": report.policy.model_dump(),
    }


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
