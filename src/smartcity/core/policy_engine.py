"""Policy engine with optional OPA integration and deterministic fallback."""

from __future__ import annotations

import os
from typing import Any, Dict

import requests
from dotenv import load_dotenv

from ..infra.logging_utils import configure_logger
from .models import ApprovalMode, CandidatePlan, PolicyDecision, RiskLevel

load_dotenv()

logger = configure_logger("policy_engine")

USER_TOKEN = os.getenv("USER_TOKEN", "user-token")
HUMAN_APPROVAL_TOKEN = os.getenv("HUMAN_APPROVAL_TOKEN", "human-approval-token")
OPA_URL = os.getenv("OPA_URL", "").strip()
OPA_POLICY_PATH = os.getenv("OPA_POLICY_PATH", "v1/data/smartcity/allow")
OPA_TIMEOUT_SECONDS = float(os.getenv("OPA_TIMEOUT_SECONDS", "1.5"))


def _color_for_mode(mode: ApprovalMode) -> str:
    if mode == ApprovalMode.AUTO:
        return "green"
    if mode == ApprovalMode.HUMAN:
        return "yellow"
    return "red"


def _fallback_policy(plan: CandidatePlan, provided_token: str) -> PolicyDecision:
    if provided_token != USER_TOKEN:
        return PolicyDecision(
            allowed=False,
            risk_level=plan.risk_level,
            approval_mode=ApprovalMode.DENY,
            verdict_color="red",
            reason="Invalid user token",
            source="fallback",
        )

    if plan.risk_level == RiskLevel.LOW:
        mode = ApprovalMode.AUTO
        allowed = True
        reason = "Low risk plan auto-approved"
    elif plan.risk_level == RiskLevel.MEDIUM:
        mode = ApprovalMode.HUMAN
        if plan.approval.human_token == HUMAN_APPROVAL_TOKEN:
            allowed = True
            reason = "Medium risk approved with human token"
        else:
            allowed = False
            reason = "Medium risk requires human approval token"
    else:
        mode = ApprovalMode.DENY
        allowed = False
        reason = "High risk denied by policy"

    return PolicyDecision(
        allowed=allowed,
        risk_level=plan.risk_level,
        approval_mode=mode,
        verdict_color=_color_for_mode(mode),
        reason=reason,
        source="fallback",
    )


def _opa_policy(
    plan: CandidatePlan, provided_token: str, trace_id: str
) -> PolicyDecision:
    if not OPA_URL:
        raise RuntimeError("OPA_URL not configured")

    url = f"{OPA_URL.rstrip('/')}/{OPA_POLICY_PATH.lstrip('/')}"
    payload = {
        "input": {
            "plan": plan.to_wire_dict(),
            "token": provided_token,
            "expected_user_token": USER_TOKEN,
            "expected_human_token": HUMAN_APPROVAL_TOKEN,
        }
    }
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
    plan: Dict[str, Any], provided_token: str, trace_id: str
) -> PolicyDecision:
    validated_plan = CandidatePlan.model_validate(plan)

    try:
        decision = _opa_policy(validated_plan, provided_token, trace_id)
    except Exception as exc:  # pragma: no cover - network path
        logger.warning(
            "OPA unavailable, using fallback policy",
            extra={
                "traceId": trace_id,
                "extra_fields": {"error": str(exc)},
            },
        )
        decision = _fallback_policy(validated_plan, provided_token)

    logger.info(
        "Policy evaluated",
        extra={"traceId": trace_id, "extra_fields": decision.model_dump()},
    )
    return decision
