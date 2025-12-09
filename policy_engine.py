"""
Simplified policy engine that stands in for OPA.
In a real deployment this module would send the plan to OPA's REST API.
"""
from __future__ import annotations

import os
from typing import Any, Dict

from logging_utils import configure_logger

logger = configure_logger("policy_engine")

USER_TOKEN = os.getenv("USER_TOKEN", "user-token")
HUMAN_APPROVAL_TOKEN = os.getenv("HUMAN_APPROVAL_TOKEN", "human-approval-token")


class PolicyDecision:
    def __init__(self, allowed: bool, reason: str):
        self.allowed = allowed
        self.reason = reason

    def to_dict(self) -> Dict[str, Any]:
        return {"allowed": self.allowed, "reason": self.reason}


def evaluate_plan(plan: Dict[str, Any], provided_token: str, trace_id: str) -> PolicyDecision:
    """
    Simulate OPA by applying simple rules:
    - Autonomy level 1/2: allow with user token.
    - Autonomy level 3: require HUMAN_APPROVAL_TOKEN to be present in the plan approval.
    """
    autonomy = plan.get("approval", {}).get("autonomy_level", 1)
    human_token = plan.get("approval", {}).get("human_token")

    if provided_token != USER_TOKEN:
        decision = PolicyDecision(False, "Invalid user token")
    elif autonomy >= 3:
        if human_token == HUMAN_APPROVAL_TOKEN:
            decision = PolicyDecision(True, "Approved with human oversight")
        else:
            decision = PolicyDecision(False, "Missing human approval token for autonomy level 3")
    else:
        decision = PolicyDecision(True, "Auto-approved for autonomy level <=2")

    logger.info("Policy evaluated", extra={"traceId": trace_id, "extra_fields": decision.to_dict()})
    return decision
