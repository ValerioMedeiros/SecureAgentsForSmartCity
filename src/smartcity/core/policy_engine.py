"""Policy engine with optional OPA integration and deterministic fallback."""

from __future__ import annotations

import os
from typing import Any, Dict

import requests
from dotenv import load_dotenv

from ..infra.audit import record_event
from ..infra.logging_utils import configure_logger
from ..infra.metrics import (
    ERRORS_TOTAL,
    POLICY_DECISIONS_TOTAL,
    stage_timer,
)
from .models import ActionType, ApprovalMode, CandidatePlan, PolicyDecision, RiskLevel

load_dotenv()

logger = configure_logger("policy_engine")

OPA_URL = os.getenv("OPA_URL", "").strip()
OPA_POLICY_PATH = os.getenv("OPA_POLICY_PATH", "v1/data/smartcity/allow")
OPA_TIMEOUT_SECONDS = float(os.getenv("OPA_TIMEOUT_SECONDS", "1.5"))


def _color_for_mode(mode: ApprovalMode) -> str:
    if mode == ApprovalMode.AUTO:
        return "green"
    if mode == ApprovalMode.HUMAN:
        return "yellow"
    return "red"


def _fallback_policy(plan: CandidatePlan) -> PolicyDecision:
    # If plan only has low risk steps, allow with auto-approval; otherwise require human approval
    allowed = True
    mode = ApprovalMode.AUTO
    risk_level = RiskLevel.LOW
    reason = "Plan allowed by fallback policy"
    for step in plan.steps:
        if step.action == ActionType.NOTIFY_USER:
            continue
        if step.action in {ActionType.TURN_OFF_PUMP, ActionType.TURN_ON_PUMP}:
            mode = ApprovalMode.HUMAN
            allowed = False
            risk_level = RiskLevel.MEDIUM
            reason = "Plan includes pump control actions, requires human approval"
            break

    return PolicyDecision(
        allowed=allowed,
        risk_level=risk_level,
        approval_mode=mode,
        verdict_color=_color_for_mode(mode),
        reason=reason,
        source="fallback",
    )


def _opa_policy(
    plan: CandidatePlan,
) -> PolicyDecision:
    if not OPA_URL:
        raise RuntimeError("OPA_URL not configured")

    url = f"{OPA_URL.rstrip('/')}/{OPA_POLICY_PATH.lstrip('/')}"
    payload = {
        "input": {
            "plan": plan.to_wire_dict(),
        }
    }
    with stage_timer("policy_opa", "opa"):
        response = requests.post(url, json=payload, timeout=OPA_TIMEOUT_SECONDS)
        response.raise_for_status()
    result = response.json().get("result", {})

    mode_raw = result.get("approval_mode", ApprovalMode.DENY.value)
    mode = ApprovalMode(mode_raw)
    return PolicyDecision(
        allowed=bool(result.get("allowed", False)),
        risk_level=RiskLevel(result.get("risk_level", plan.risk_level.value)),
        approval_mode=mode,
        verdict_color=result.get("verdict_color", _color_for_mode(mode)),
        reason=result.get("reason", "Policy decision returned by OPA"),
        source="opa",
    )


def evaluate_plan(
    plan: Dict[str, Any], trace_id: str
) -> PolicyDecision:
    validated_plan = CandidatePlan.model_validate(plan)

    with stage_timer("policy", "policy_engine") as timing:
        try:
            decision = _opa_policy(validated_plan)
        except Exception as exc:  # pragma: no cover - network path
            ERRORS_TOTAL.labels(component="opa", kind="opa_unavailable").inc()
            logger.warning(
                "OPA unavailable, using fallback policy",
                extra={
                    "traceId": trace_id,
                    "extra_fields": {"error": str(exc)},
                },
            )
            decision = _fallback_policy(validated_plan)

    POLICY_DECISIONS_TOTAL.labels(
        approval_mode=decision.approval_mode.value,
        risk_level=decision.risk_level.value,
        allowed=str(decision.allowed).lower(),
        source=decision.source,
    ).inc()

    logger.info(
        "Policy evaluated",
        extra={
            "traceId": trace_id,
            "extra_fields": {
                **decision.model_dump(),
                "duration_ms": timing["duration_ms"],
            },
        },
    )
    record_event(
        component="policy_engine",
        event_type="POLICY_DECIDED",
        trace_id=trace_id,
        plan_id=validated_plan.plan_id,
        actor=decision.source,
        outcome=decision.approval_mode.value,
        payload={
            "allowed": decision.allowed,
            "approval_mode": decision.approval_mode.value,
            "risk_level": decision.risk_level.value,
            "verdict_color": decision.verdict_color,
            "reason": decision.reason,
            "duration_ms": timing["duration_ms"],
        },
    )
    return decision
