import os
import uuid
from typing import Dict

from dotenv import load_dotenv

from ..core.executor import execute_candidate_plan
from ..core.models import MonitorEvent
from ..core.planner import build_candidate_plan
from ..infra.logging_utils import configure_logger
from .init_traffic_signal import main as initialize_traffic_signal

load_dotenv()

logger = configure_logger("host")


def _scenario_event(scenario: str) -> MonitorEvent:
    table: Dict[str, MonitorEvent] = {
        "A": MonitorEvent(
            event_type="ambulance-only",
            ambulance_detected=True,
            heavy_rain=False,
            flood_risk=False,
            crowd_level="normal",
            location="Hospital corridor",
        ),
        "B": MonitorEvent(
            event_type="flood-only",
            ambulance_detected=False,
            heavy_rain=True,
            flood_risk=True,
            crowd_level="high",
            location="Power plant area",
        ),
        "C": MonitorEvent(
            event_type="combined-flood-corridor",
            ambulance_detected=True,
            heavy_rain=True,
            flood_risk=False,
            crowd_level="high",
            location="Downtown crossing",
        ),
    }
    if scenario not in table:
        raise ValueError("Unsupported scenario. Use A, B or C.")
    return table[scenario]


def run_once(scenario: str) -> None:
    """
    Simulates a single run of the host application for a given scenario. 
    The scenario is determined by the SCENARIO environment variable, which can be set to "A", "B", or "C". 
    Each scenario corresponds to a different combination of events (e.g., ambulance detected, heavy rain, flood risk) 
    that the system will process. The function builds a candidate plan based on the scenario's event, 
    executes it, and logs the results, including whether the plan was executed and details about the policy applied.
    """
    trace_id = str(uuid.uuid4())
    event = _scenario_event(scenario)
    plan = build_candidate_plan(event, trace_id)
    report = execute_candidate_plan(plan)
    logger.info(
        "Run completed",
        extra={
            "traceId": trace_id,
            "extra_fields": {
                "scenario": event.event_type,
                "plan_id": report.plan_id,
                "executed": report.executed,
                "policy_source": report.policy.source,
                "policy_mode": report.policy.approval_mode.value,
                "policy_reason": report.policy.reason,
            },
        },
    )


if __name__ == "__main__":
    initialize_traffic_signal()
    selected_scenario = os.getenv("SCENARIO", "A").upper()
    run_once(selected_scenario)
