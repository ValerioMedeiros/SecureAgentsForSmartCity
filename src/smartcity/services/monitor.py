from __future__ import annotations

import os
import uuid
from typing import Any, Dict, List, Optional

from fastapi import Body, FastAPI, HTTPException, Query, Response

from ..core.models import MonitorEvent, WeatherObserved
from ..core.weather_agent import run as run_weather_agent
from ..infra.audit import read_entries, record_event, verify_chain
from ..infra.logging_utils import configure_logger
from ..infra.metrics import render_latest, stage_timer
from ..infra.ngsi_client import create_subscription
from ..infra.pump_mcp_client import get_nearest_pump

logger = configure_logger("monitor")
app = FastAPI(title="Monitor Service")

MONITOR_CALLBACK_URL = os.getenv("MONITOR_CALLBACK_URL", "http://localhost:8010/monitor/notify")


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

    observations: List[WeatherObserved] = []
    for item in data:
        entity_type = str(item.get("type", "")).lower()
        if entity_type != "weatherobserved":
            continue

        precipitation = float(_get_attr_value(item, "precipitation", 0))
        humidity = float(_get_attr_value(item, "humidity", 0))
        pressure = float(_get_attr_value(item, "atmosphericPressure", 1013))
        coverage_radius_m = int(_get_attr_value(item, "coverage_radius_m", 300))
        station_id = str(item.get("id", "unknown"))

        location_val = _get_attr_value(item, "location", {})
        coordinates = None
        location_str = "unknown"
        if isinstance(location_val, dict) and location_val.get("type") == "Point":
            coords = location_val.get("coordinates", [])
            if len(coords) == 2:
                coordinates = (coords[0], coords[1])  # (lon, lat)
                location_str = f"{coords[1]},{coords[0]}"
        elif isinstance(location_val, str):
            location_str = location_val

        observations.append(WeatherObserved(
            event_type="weather",
            station_id=station_id,
            precipitation=precipitation,
            humidity=humidity,
            atmospheric_pressure=pressure,
            location=location_str,
            notes=f"precipitation={precipitation}mm humidity={humidity}% pressure={pressure}hPa",
            coordinates=coordinates,
            coverage_radius_m=coverage_radius_m,
        ))

    if not observations:
        return MonitorEvent(event_type="unknown")

    return MonitorEvent(event_type="weather", weather_observations=observations)


@app.post("/monitor/notify")
async def handle_notification(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    trace_id = str(uuid.uuid4())
    with stage_timer("monitor", "monitor") as timing:
        event = _notification_to_event(payload)

        # Enrich observations with forecast data and resolve nearest pump
        resolved_observations = []
        for obs in (event.weather_observations or []):
            if obs.coordinates:
                lon, lat = obs.coordinates

                # Resolve nearest pump based on current precipitation threshold
                should_resolve_pump = obs.precipitation > 0.50
                if should_resolve_pump:
                    pump_entity = await get_nearest_pump(lon, lat, obs.coverage_radius_m)
                    if pump_entity:
                        pump_id = pump_entity.get("id", "")
                        obs = obs.model_copy(update={"pump_id": pump_id})
                        logger.info(
                            "Nearest pump resolved via geo-query",
                            extra={"traceId": trace_id, "extra_fields": {
                                "station_id": obs.station_id,
                                "pump_id": pump_id,
                                "radius_m": obs.coverage_radius_m,
                                "coordinates": obs.coordinates,
                            }},
                        )
                    else:
                        logger.warning(
                            "No available pump found within radius",
                            extra={"traceId": trace_id, "extra_fields": {
                                "station_id": obs.station_id,
                                "radius_m": obs.coverage_radius_m,
                                "coordinates": obs.coordinates,
                            }},
                        )
            resolved_observations.append(obs)

        if resolved_observations:
            event = event.model_copy(update={"weather_observations": resolved_observations})

        record_event(
            component="monitor",
            event_type="EVENT_RECEIVED",
            trace_id=trace_id,
            actor="ngsi",
            outcome=event.event_type,
            payload={
                "event_type": event.event_type,
                "observations": len(event.weather_observations or []),
                "raw_keys": list(payload.keys()),
            },
        )
        agent_result = await run_weather_agent(event, trace_id)

    logger.info(
        "Weather Agent loop completed",
        extra={
            "traceId": trace_id,
            "extra_fields": {
                "scenario": event.event_type,
                "source": agent_result.get("source"),
                "tool_calls": agent_result.get("tool_calls", []),
                "duration_ms": timing["duration_ms"],
            },
        },
    )
    record_event(
        component="monitor",
        event_type="LOOP_COMPLETED",
        trace_id=trace_id,
        actor="monitor",
        outcome="completed",
        payload={
            "scenario": event.event_type,
            "source": agent_result.get("source"),
            "tool_calls": agent_result.get("tool_calls", []),
            "duration_ms": timing["duration_ms"],
        },
    )
    return {
        "traceId": trace_id,
        "source": agent_result.get("source"),
        "tool_calls": agent_result.get("tool_calls", []),
        "summary": agent_result.get("summary", ""),
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
        "description": "Monitor WeatherObserved events",
        "subject": {
            "entities": [{"idPattern": "WeatherStation:.*", "type": "WeatherObserved"}],
            "condition": {"attrs": ["precipitation", "humidity", "atmosphericPressure"]},
        },
        "notification": {
            "http": {"url": MONITOR_CALLBACK_URL},
            "attrs": ["precipitation", "humidity", "atmosphericPressure", "location", "coverage_radius_m"],
        },
        "throttling": 5,
    }
    return create_subscription(subscription, trace_id)
