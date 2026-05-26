from __future__ import annotations

import json
import os
import uuid
from typing import Any, Dict, Optional

from dotenv import load_dotenv

from ..infra.audit import record_event
from ..infra.logging_utils import configure_logger
from ..infra.metrics import ERRORS_TOTAL, PLANS_TOTAL, stage_timer
from .llm_planner import generate_plan_with_llm
from .models import (
    ActionType,
    CandidatePlan,
    MonitorEvent,
    RiskLevel,
    validate_plan_dict,
)

load_dotenv()

logger = configure_logger("planner")

PUMP_ENTITY_ID = os.getenv("PUMP_ENTITY_ID", "PumpStation:001")


_FORECAST_RISK_MAP: dict[str, RiskLevel] = {
    "crítico": RiskLevel.HIGH,
    "alto": RiskLevel.HIGH,
    "médio": RiskLevel.MEDIUM,
    "baixo": RiskLevel.LOW,
}

_RISK_RANK: dict[RiskLevel, int] = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
}


def _escalate(current: RiskLevel, candidate: RiskLevel) -> RiskLevel:
    return candidate if _RISK_RANK[candidate] > _RISK_RANK[current] else current


def _risk_from_event(event: MonitorEvent) -> RiskLevel:
    et = (event.event_type or "").lower()

    if "flood" in et:
        return RiskLevel.HIGH

    risk = RiskLevel.MEDIUM if "pump" in et else RiskLevel.LOW

    for obs in (event.weather_observations or []):
        # Forecast-based risk takes priority when available
        forecast_risk = _FORECAST_RISK_MAP.get(getattr(obs, "rainfall_risk", None) or "")
        if forecast_risk:
            risk = _escalate(risk, forecast_risk)

        # Fallback: current precipitation thresholds
        try:
            precip = float(getattr(obs, "precipitation", 0) or 0)
            if precip > 50:
                risk = _escalate(risk, RiskLevel.HIGH)
            elif precip > 30:
                risk = _escalate(risk, RiskLevel.MEDIUM)
        except Exception:
            continue

    return risk


def _approval_level(risk_level: RiskLevel) -> int:
    if risk_level == RiskLevel.LOW:
        return 1
    if risk_level == RiskLevel.MEDIUM:
        return 2
    return 3


def _build_rule_based_plan(event: MonitorEvent, trace_id: str) -> Dict[str, Any]:
    """Build a simple rule-based pump/flood plan based on the event."""
    risk_level = _risk_from_event(event)
    autonomy_level = _approval_level(risk_level)

    et = (event.event_type or "").lower()

    # Default plan values
    goal = "Maintain normal infrastructure operation"
    scenario = "baseline"
    message = "No immediate pump actions required"
    steps = [
        {
            "id": "notify",
            "action": ActionType.NOTIFY_USER.value,
            "params": {
                "message": "Monitoring normal conditions",
                "user_id": "ops-team",
            },
        }
    ]

    # If any weather observation has a resolved pump, add pump actuation steps
    for obs in (event.weather_observations or []):
        pump_id = getattr(obs, "pump_id", None)
        precipitation = float(getattr(obs, "precipitation", 0) or 0)
        if pump_id and precipitation > 0.50:
            goal = "Activate pump in response to weather alert"
            scenario = "flood-response"
            steps += [
                {
                    "id": f"activate-pump-{pump_id}",
                    "action": ActionType.TURN_ON_PUMP.value,
                    "params": {"entity_id": pump_id},
                },
            ]

    return {
        "plan_id": str(uuid.uuid4()),
        "goal": goal,
        "scenario": scenario,
        "risk_level": risk_level.value,
        "steps": steps,
        "approval": {"autonomy_level": autonomy_level},
        "telemetry": {"traceId": trace_id},
    }


def _llm_planner_payload(
    event: MonitorEvent, trace_id: str
) -> Optional[Dict[str, Any]]:
    """
    Generate plan using LLM planner with LangChain.
    """

    # Use LangChain-based LLM planner
    llm_plan = generate_plan_with_llm(event, trace_id)
    if llm_plan:
        return llm_plan

    return None


def build_candidate_plan(event: MonitorEvent, trace_id: str) -> CandidatePlan:
    with stage_timer("plan", "planner") as timing:
        try:
            llm_payload = _llm_planner_payload(event, trace_id)
            plan_data = (
                llm_payload if llm_payload else _build_rule_based_plan(event, trace_id)
            )
            source = "llm" if llm_payload else "rule_based"
            plan = validate_plan_dict(plan_data)
        except Exception:
            ERRORS_TOTAL.labels(component="planner", kind="plan_build").inc()
            raise

    PLANS_TOTAL.labels(
        scenario=plan.scenario,
        risk_level=plan.risk_level.value,
        source=source,
    ).inc()

    logger.info(
        "Candidate plan generated",
        extra={
            "traceId": trace_id,
            "extra_fields": {
                "plan_id": plan.plan_id,
                "goal": plan.goal,
                "scenario": plan.scenario,
                "risk_level": plan.risk_level.value,
                "autonomy_level": plan.approval.autonomy_level,
                "source": source,
                "duration_ms": timing["duration_ms"],
            },
        },
    )
    record_event(
        component="planner",
        event_type="PLAN_CREATED",
        trace_id=trace_id,
        plan_id=plan.plan_id,
        actor=source,
        outcome="created",
        payload={
            "scenario": plan.scenario,
            "risk_level": plan.risk_level.value,
            "autonomy_level": plan.approval.autonomy_level,
            "goal": plan.goal,
            "steps": [
                {"id": s.id, "action": s.action.value, "params": s.params}
                for s in plan.steps
            ],
            "duration_ms": timing["duration_ms"],
            "event": event.model_dump(),
        },
    )
    return plan


def malformed_plan_fixture(trace_id: str) -> Dict[str, Any]:
    """
    Malformed plan fixture is designed to test the robustness of the plan validation and execution system.
    """
    return {
        "plan_id": str(uuid.uuid4()),
        "goal": "Malformed plan fixture",
        "scenario": "test-malformed",
        "risk_level": "high",
        "steps": [
            {
                "id": "bad-step",
                "action": "turnOnPump",
                "params": {"entity_id": PUMP_ENTITY_ID},
            }
        ],
        "approval": {"autonomy_level": 3},
        "telemetry": {"traceId": trace_id},
    }
