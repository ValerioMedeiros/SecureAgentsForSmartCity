from __future__ import annotations

import json
import os
import uuid
from typing import Any, Dict, Optional

from dotenv import load_dotenv

from ..infra.logging_utils import configure_logger
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

TRAFFIC_SIGNAL_ID = os.getenv("TRAFFIC_SIGNAL_ID", "TrafficSignal:001")


def _risk_from_event(event: MonitorEvent) -> RiskLevel:
    if event.flood_risk:
        return RiskLevel.HIGH
    if event.heavy_rain or event.crowd_level.lower() in {"high", "dense"}:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def _approval_level(risk_level: RiskLevel) -> int:
    if risk_level == RiskLevel.LOW:
        return 1
    if risk_level == RiskLevel.MEDIUM:
        return 2
    return 3


def _build_rule_based_plan(event: MonitorEvent, trace_id: str) -> Dict[str, Any]:
    risk_level = _risk_from_event(event)
    autonomy_level = _approval_level(risk_level)

    if event.ambulance_detected:
        corridor_value = "emergency"
        goal = "Create emergency corridor for ambulance"
        scenario = "ambulance-only"
        message = "Emergency corridor activated for ambulance"
    elif event.flood_risk or event.heavy_rain:
        corridor_value = "critical-infra"
        goal = "Protect critical infrastructure under weather stress"
        scenario = "flood-only"
        message = "Weather response rerouting activated"
    else:
        corridor_value = "none"
        goal = "Maintain normal traffic operation"
        scenario = "baseline"
        message = "Traffic remains in normal mode"

    if event.ambulance_detected and (event.heavy_rain or event.flood_risk):
        scenario = "combined-flood-corridor"
        goal = "Coordinate emergency corridor with weather risk mitigation"
        corridor_value = "emergency"
        message = "Combined emergency and weather protocol activated"

    steps = [
        {
            "id": "read-state",
            "action": ActionType.GET_TRAFFIC_SIGNAL_STATE.value,
            "params": {"entity_id": TRAFFIC_SIGNAL_ID},
        },
        {
            "id": "set-priority",
            "action": ActionType.SET_PRIORITY_CORRIDOR.value,
            "params": {"entity_id": TRAFFIC_SIGNAL_ID, "value": corridor_value},
        },
        {
            "id": "notify",
            "action": ActionType.NOTIFY_TRAFFIC_AGENTS.value,
            "params": {"message": message},
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
    llm_payload = _llm_planner_payload(event, trace_id)
    plan_data = llm_payload if llm_payload else _build_rule_based_plan(event, trace_id)
    plan = validate_plan_dict(plan_data)
    logger.info(
        "Candidate plan generated",
        extra={
            "traceId": trace_id,
            "extra_fields": {
                "plan_id": plan.plan_id,
                "scenario": plan.scenario,
                "risk_level": plan.risk_level.value,
                "autonomy_level": plan.approval.autonomy_level,
            },
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
                "action": "setPriorityCorridor",
                "params": {"entity_id": TRAFFIC_SIGNAL_ID},
            }
        ],
        "approval": {"autonomy_level": 3},
        "telemetry": {"traceId": trace_id},
    }
