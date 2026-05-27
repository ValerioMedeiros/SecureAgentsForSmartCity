from __future__ import annotations

import asyncio
import os
import threading
from typing import List

from dotenv import load_dotenv

from ..infra.audit import record_event
from ..infra.logging_utils import configure_logger
from ..infra.metrics import (
    ERRORS_TOTAL,
    EXECUTIONS_TOTAL,
    stage_timer,
)
from ..infra.pump_mcp_client import turn_off_pump, turn_on_pump
from .models import CandidatePlan, ExecutionReport, StepResult
from .policy_engine import evaluate_plan

load_dotenv()
logger = configure_logger("executor")

def _run_async(coro):
    """Run async Pump MCP client calls from both sync and async contexts."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result = {}
    error = {}

    def _runner() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except Exception as exc:  # pragma: no cover - defensive guard
            error["exc"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()

    if "exc" in error:
        raise error["exc"]
    return result.get("value")


def _extract_pump_id(params: dict) -> str:
    return str(params.get("pump_id") or params.get("entity_id") or "Pump:001")


def _invoke_step(action: str, params: dict) -> tuple[int, str]:
    if action == "turnOnPump":
        pump_id = _extract_pump_id(params)
        payload = _run_async(turn_on_pump(pump_id))
        ok = bool(payload.get("success", False))
        return (200 if ok else 409, str(payload))

    if action == "turnOffPump":
        pump_id = _extract_pump_id(params)
        payload = _run_async(turn_off_pump(pump_id))
        ok = bool(payload.get("success", False))
        return (200 if ok else 409, str(payload))

    if action == "notifyUser":
        return (200, "notification accepted")

    raise ValueError(f"Unsupported action for Pump MCP execution: {action}")


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
            with stage_timer("mcp_call_client", "executor") as step_timing:
                try:
                    status_code, body = _invoke_step(step.action.value, step.params)
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

            results.append(
                StepResult(
                    step_id=step.id,
                    action=step.action,
                    status_code=status_code,
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
                        "status": status_code,
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
                outcome="ok" if status_code < 400 else "http_error",
                payload={
                    "step": step.id,
                    "action": step.action.value,
                    "params": step.params,
                    "status_code": status_code,
                    "duration_ms": step_timing["duration_ms"],
                    "response_snippet": body[:512],
                },
            )
            if status_code >= 400:
                raise RuntimeError(
                    f"Step '{step.id}' failed with status {status_code}: {body}"
                )

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
