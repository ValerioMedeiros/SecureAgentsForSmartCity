from __future__ import annotations

import os
from typing import List

import requests
from dotenv import load_dotenv

from ..infra.logging_utils import configure_logger
from .models import CandidatePlan, ExecutionReport, StepResult
from .policy_engine import USER_TOKEN, evaluate_plan

load_dotenv()

logger = configure_logger("executor")

MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8000/mcp")


def execute_candidate_plan(plan: CandidatePlan) -> ExecutionReport:
    trace_id = plan.telemetry.trace_id
    decision = evaluate_plan(
        plan=plan.to_wire_dict(),
        provided_token=USER_TOKEN,
        trace_id=trace_id,
    )

    if not decision.allowed:
        logger.warning(
            "Plan blocked before execution",
            extra={
                "traceId": trace_id,
                "extra_fields": {
                    "plan_id": plan.plan_id,
                    "reason": decision.reason,
                    "mode": decision.approval_mode.value,
                },
            },
        )
        return ExecutionReport(
            plan_id=plan.plan_id,
            trace_id=trace_id,
            policy=decision,
            executed=False,
        )

    results: List[StepResult] = []
    for step in plan.steps:
        call_payload = {
            "method": step.action.value,
            "params": step.params,
            "traceId": trace_id,
            "token": USER_TOKEN,
        }
        response = requests.post(MCP_SERVER_URL, json=call_payload, timeout=10)
        body = response.text
        results.append(
            StepResult(
                step_id=step.id,
                action=step.action,
                status_code=response.status_code,
                response_body=body,
            )
        )
        logger.info(
            "Step executed",
            extra={
                "traceId": trace_id,
                "extra_fields": {
                    "step": step.id,
                    "action": step.action.value,
                    "status": response.status_code,
                },
            },
        )
        response.raise_for_status()

    return ExecutionReport(
        plan_id=plan.plan_id,
        trace_id=trace_id,
        policy=decision,
        executed=True,
        step_results=results,
    )
