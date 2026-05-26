from __future__ import annotations

import os
import uuid
from typing import Any, Dict, Optional

import requests
from dotenv import load_dotenv

from ..infra.audit import record_event
from ..infra.logging_utils import configure_logger
from ..infra.metrics import ERRORS_TOTAL, EXECUTIONS_TOTAL, stage_timer
from .models import (
    CandidatePlan,
    ExecutionReport,
    MonitorEvent,
    PolicyDecision,
    StepResult,
    validate_plan_dict,
)
from .planner import build_candidate_plan
from .policy_engine import evaluate_plan
from .security import security_manager

load_dotenv()

logger = configure_logger("pipeline")

MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8000/mcp")


def _coerce_event(event: MonitorEvent | Dict[str, Any]) -> MonitorEvent:
    if isinstance(event, MonitorEvent):
        return event
    return MonitorEvent.model_validate(event)


def _coerce_plan(plan: CandidatePlan | Dict[str, Any]) -> CandidatePlan:
    if isinstance(plan, CandidatePlan):
        return plan
    return validate_plan_dict(plan)


def _prompt_human_token(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError as exc:
        raise RuntimeError(
            "Human token required but no terminal input was available"
        ) from exc


def _request_authorized_token(
    plan: CandidatePlan,
    *,
    prompt: str,
    initial_token: Optional[str] = None,
    max_attempts: int = 3,
) -> str:
    token = initial_token.strip() if initial_token else None
    for attempt in range(1, max_attempts + 1):
        candidate_token = token or _prompt_human_token(
            f"{prompt} (attempt {attempt}/{max_attempts}): "
        )
        if security_manager.authorize_plan(candidate_token, plan):
            return candidate_token
        token = None

    raise PermissionError(
        f"Token did not authorize plan {plan.plan_id} after {max_attempts} attempts"
    )


def _execute_plan(
    plan: CandidatePlan, decision: PolicyDecision,
) -> ExecutionReport:
    trace_id = plan.telemetry.trace_id
    results: list[StepResult] = []
    execute_status = "completed"

    with stage_timer("execute", "pipeline") as exec_timing:
        for step in plan.steps:
            call_payload = {
                "method": step.action.value,
                "params": step.params,
                "traceId": trace_id,
            }
            with stage_timer("mcp_call_client", "pipeline") as step_timing:
                try:
                    response = requests.post(
                        MCP_SERVER_URL, json=call_payload, timeout=10
                    )
                except Exception:
                    ERRORS_TOTAL.labels(component="pipeline", kind="mcp_call").inc()
                    record_event(
                        component="pipeline",
                        event_type="TOOL_INVOCATION_FAILED",
                        trace_id=trace_id,
                        plan_id=plan.plan_id,
                        actor="pipeline",
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
                component="pipeline",
                event_type="TOOL_INVOKED",
                trace_id=trace_id,
                plan_id=plan.plan_id,
                actor="pipeline",
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
        component="pipeline",
        event_type="EXECUTION_COMPLETED",
        trace_id=trace_id,
        plan_id=plan.plan_id,
        actor="pipeline",
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


def pipeline(
    event: MonitorEvent | Dict[str, Any],
    *,
    trace_id: Optional[str] = None,
    candidate_plan: CandidatePlan | Dict[str, Any] | None = None,
    build: bool = True,
    validate: bool = True,
    request_human_token: bool = True,
    human_token: Optional[str] = None,
    execute: bool = True,
) -> Dict[str, Any]:
    normalized_event = _coerce_event(event)
    effective_trace_id = trace_id or str(uuid.uuid4())

    with stage_timer("pipeline", "pipeline") as timing:
        if candidate_plan is not None:
            plan = _coerce_plan(candidate_plan)
        elif build:
            plan = build_candidate_plan(normalized_event, effective_trace_id)
        else:
            raise ValueError(
                "pipeline requires build=True or candidate_plan to be provided"
            )

        if (
            validate
            and candidate_plan is not None
            and not isinstance(candidate_plan, CandidatePlan)
        ):
            plan = _coerce_plan(candidate_plan)

        policy = evaluate_plan(
            plan=plan.to_wire_dict(),
            trace_id=effective_trace_id,
        )

        approval_requested = False
        if policy.approval_mode.value == "human" and request_human_token:
            approval_requested = True
            human_token = _request_authorized_token(
                plan,
                prompt=f"Human approval required for plan {plan.plan_id}. Enter human token",
                initial_token=human_token,
                max_attempts=3,
            )
            plan.approval.human_token = human_token

        if (
            policy.approval_mode.value != "human"
            and not security_manager.authorize_plan(plan.approval.human_token, plan)
        ):
            raise PermissionError(
                "Execution token does not have permission to execute this plan"
            )

        execution_report: ExecutionReport | None = None
        if execute:
            if policy.allowed or (
                policy.approval_mode.value == "human" and human_token
            ):
                execution_report = _execute_plan(
                    plan=plan,
                    decision=policy,
                )
            else:
                execution_report = ExecutionReport(
                    plan_id=plan.plan_id,
                    trace_id=effective_trace_id,
                    policy=policy,
                    executed=False,
                )

    return {
        "traceId": effective_trace_id,
        "event": normalized_event.model_dump(),
        "plan": plan.model_dump(),
        "policy": policy.model_dump(),
        "human_token_requested": approval_requested,
        "execution": execution_report.model_dump() if execution_report else None,
        "duration_ms": timing["duration_ms"],
    }
