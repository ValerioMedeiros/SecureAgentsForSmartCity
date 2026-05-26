from __future__ import annotations

import os
from typing import List

import requests
from dotenv import load_dotenv

from ..infra.audit import record_event
from ..infra.logging_utils import configure_logger
from ..infra.metrics import (
    ERRORS_TOTAL,
    EXECUTIONS_TOTAL,
    stage_timer,
)
from .models import CandidatePlan, ExecutionReport, StepResult
from .policy_engine import evaluate_plan

load_dotenv()

logger = configure_logger("executor")

MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8000/mcp")


def execute_candidate_plan(plan: CandidatePlan) -> ExecutionReport:
    trace_id = plan.telemetry.trace_id
    decision = evaluate_plan(
        plan=plan.to_wire_dict(),
        trace_id=trace_id,
    )

    if not decision.allowed:
        EXECUTIONS_TOTAL.labels(status="blocked").inc()
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
        record_event(
            component="executor",
            event_type="EXECUTION_BLOCKED",
            trace_id=trace_id,
            plan_id=plan.plan_id,
            actor="executor",
            outcome="blocked",
            payload={
                "reason": decision.reason,
                "approval_mode": decision.approval_mode.value,
                "risk_level": decision.risk_level.value,
            },
        )
        return ExecutionReport(
            plan_id=plan.plan_id,
            trace_id=trace_id,
            policy=decision,
            executed=False,
        )

    results: List[StepResult] = []
    execute_status = "completed"
    with stage_timer("execute", "executor") as exec_timing:
        for step in plan.steps:
            call_payload = {
                "method": step.action.value,
                "params": step.params,
                "traceId": trace_id,
            }
            with stage_timer("mcp_call_client", "executor") as step_timing:
                try:
                    response = requests.post(
                        MCP_SERVER_URL, json=call_payload, timeout=10
                    )
                except Exception:
                    ERRORS_TOTAL.labels(component="executor", kind="mcp_call").inc()
                    record_event(
                        component="executor",
                        event_type="TOOL_INVOCATION_FAILED",
                        trace_id=trace_id,
                        plan_id=plan.plan_id,
                        actor="executor",
                        outcome="error",
                        payload={
                            "step": step.id,
                            "action": step.action.value,
                            "params": step.params,
                        },
                    )
                    execute_status = "error"
                    raise
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
                        "duration_ms": step_timing["duration_ms"],
                    },
                },
            )
            record_event(
                component="executor",
                event_type="TOOL_INVOKED",
                trace_id=trace_id,
                plan_id=plan.plan_id,
                actor="executor",
                outcome="ok" if response.ok else "http_error",
                payload={
                    "step": step.id,
                    "action": step.action.value,
                    "params": step.params,
                    "status_code": response.status_code,
                    "duration_ms": step_timing["duration_ms"],
                    "response_snippet": body[:512],
                },
            )
            response.raise_for_status()

    EXECUTIONS_TOTAL.labels(status=execute_status).inc()
    record_event(
        component="executor",
        event_type="EXECUTION_COMPLETED",
        trace_id=trace_id,
        plan_id=plan.plan_id,
        actor="executor",
        outcome=execute_status,
        payload={
            "steps": len(results),
            "duration_ms": exec_timing["duration_ms"],
        },
    )

    return ExecutionReport(
        plan_id=plan.plan_id,
        trace_id=trace_id,
        policy=decision,
        executed=True,
        step_results=results,
    )
