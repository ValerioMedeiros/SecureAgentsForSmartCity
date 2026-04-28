from __future__ import annotations

import os
import time
import uuid
from statistics import mean
from typing import Any, Dict, List, Tuple

from ..core.executor import execute_candidate_plan
from ..core.models import MonitorEvent
from ..core.planner import build_candidate_plan, malformed_plan_fixture

SCENARIOS: Dict[str, MonitorEvent] = {
    "flood-only": MonitorEvent(
        event_type="flood-only", heavy_rain=True, flood_risk=True, crowd_level="high"
    ),
    "ambulance-only": MonitorEvent(
        event_type="ambulance-only", ambulance_detected=True
    ),
    "combined": MonitorEvent(
        event_type="combined",
        ambulance_detected=True,
        heavy_rain=True,
        flood_risk=False,
        crowd_level="high",
    ),
}


def _timed_run(event: MonitorEvent) -> Tuple[float, bool]:
    trace_id = str(uuid.uuid4())
    start = time.perf_counter()
    plan = build_candidate_plan(event, trace_id)
    report = execute_candidate_plan(plan)
    elapsed = (time.perf_counter() - start) * 1000
    return elapsed, report.executed


def experiment_latency(runs: int = 5) -> Dict[str, Any]:
    """
    Experiment to measure the latency of plan execution under different scenarios.
    """
    samples: List[float] = []
    executed: List[bool] = []
    for _ in range(runs):
        elapsed, ok = _timed_run(SCENARIOS["ambulance-only"])
        samples.append(elapsed)
        executed.append(ok)
    return {
        "name": "latency-impact",
        "runs": runs,
        "avg_ms": round(mean(samples), 2),
        "min_ms": round(min(samples), 2),
        "max_ms": round(max(samples), 2),
        "success_rate": round(sum(1 for x in executed if x) / runs, 2),
    }


def experiment_guardrails() -> Dict[str, Any]:
    """
    Test that malformed plans are blocked by the guardrails and do not cause system failures. 
    We attempt to build a candidate plan from a known malformed fixture and check if it raises an 
    exception, which would indicate that the guardrails are working as intended.
    """
    trace_id = str(uuid.uuid4())
    malformed = malformed_plan_fixture(trace_id)
    blocked = False
    reason = ""
    try:
        build_candidate_plan(MonitorEvent(event_type="fixture"), trace_id)
        from ..core.models import validate_plan_dict

        validate_plan_dict(malformed)
    except Exception as exc:
        blocked = True
        reason = str(exc)

    return {
        "name": "guardrail-effectiveness",
        "malformed_blocked": blocked,
        "reason": reason,
    }


def experiment_robustness() -> Dict[str, Any]:
    """
    Run each scenario and report the policy decision and execution
    """
    outputs: List[Dict[str, Any]] = []
    for name, event in SCENARIOS.items():
        trace_id = str(uuid.uuid4())
        plan = build_candidate_plan(event, trace_id)
        report = execute_candidate_plan(plan)
        outputs.append(
            {
                "scenario": name,
                "risk": plan.risk_level.value,
                "executed": report.executed,
                "mode": report.policy.approval_mode.value,
                "reason": report.policy.reason,
            }
        )
    return {
        "name": "scenario-robustness",
        "outputs": outputs,
    }


def run_all() -> List[Dict[str, Any]]:
    runs = int(os.getenv("EXPERIMENT_RUNS", "5"))
    return [
        experiment_guardrails(),
        experiment_latency(runs=runs),
        experiment_robustness(),
    ]


if __name__ == "__main__":
    import json

    print(json.dumps(run_all(), indent=2))
