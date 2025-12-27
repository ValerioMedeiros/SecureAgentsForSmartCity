import os
import uuid
from typing import Any, Dict, List

import requests

from logging_utils import configure_logger
from policy_engine import evaluate_plan, USER_TOKEN, HUMAN_APPROVAL_TOKEN
from dotenv import load_dotenv
load_dotenv()

logger = configure_logger("host")

MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8000/mcp")
TRAFFIC_SIGNAL_ID = os.getenv("TRAFFIC_SIGNAL_ID", "TrafficSignal:001")


class Step(Dict[str, Any]):
    """Typed alias used for clarity in the simple plan representation."""

# Tool de decisão de autonomia(level, trace_id)


def build_plan_autonomy_1(trace_id: str) -> Dict[str, Any]:
    steps: List[Step] = [
        {
            "id": "read-state",
            "tool": "getTrafficSignalState",
            "params": {"entity_id": TRAFFIC_SIGNAL_ID},
        },
        {
            "id": "set-priority",
            "tool": "setPriorityCorridor",
            "params": {"entity_id": TRAFFIC_SIGNAL_ID, "value": "emergency"},
        },
        {
            "id": "notify",
            "tool": "notifyTrafficAgents",
            "params": {"message": "Ambulance corridor activated"},
        },
    ]
    return {
        "plan_id": str(uuid.uuid4()),
        "goal": "Create an ambulance corridor",
        "steps": steps,
        "approval": {"autonomy_level": 1},
        "telemetry": {"traceId": trace_id},
    }


def build_plan_autonomy_3(trace_id: str) -> Dict[str, Any]:
    steps: List[Step] = [
        {
            "id": "read-state",
            "tool": "getTrafficSignalState",
            "params": {"entity_id": TRAFFIC_SIGNAL_ID},
        },
        {
            "id": "set-priority",
            "tool": "setPriorityCorridor",
            "params": {"entity_id": TRAFFIC_SIGNAL_ID, "value": "critical-infra"},
        },
        {
            "id": "notify",
            "tool": "notifyTrafficAgents",
            "params": {"message": "Heavy rain: rerouting around critical infrastructure"},
        },
    ]
    return {
        "plan_id": str(uuid.uuid4()),
        "goal": "Handle heavy rain near critical infrastructure",
        "steps": steps,
        "approval": {"autonomy_level": 3, "human_token": HUMAN_APPROVAL_TOKEN},
        "telemetry": {"traceId": trace_id},
    }


def execute_plan(plan: Dict[str, Any]) -> None:
    trace_id = plan["telemetry"]["traceId"]
    logger.info("Generated plan", extra={"traceId": trace_id, "extra_fields": {"plan_id": plan["plan_id"]}})

    decision = evaluate_plan(plan, provided_token=USER_TOKEN, trace_id=trace_id)
    if not decision.allowed:
        logger.warning("Plan rejected", extra={"traceId": trace_id, "extra_fields": decision.to_dict()})
        return

    # The plan stays immutable; only the MCP tools execute side effects so that reasoning and execution remain separated.
    for step in plan["steps"]:
        call_payload = {
            "method": step["tool"],
            "params": step.get("params", {}),
            "traceId": trace_id,
            "token": USER_TOKEN,
        }
        logger.info(
            "Executing step",
            extra={"traceId": trace_id, "extra_fields": {"step": step["id"], "tool": step["tool"]}},
        )
        response = requests.post(MCP_SERVER_URL, json=call_payload, timeout=10)
        logger.info(
            "MCP response",
            extra={"traceId": trace_id, "extra_fields": {"status": response.status_code, "body": response.text}},
        )
        response.raise_for_status()


if __name__ == "__main__":
    trace = str(uuid.uuid4())
    scenario = os.getenv("SCENARIO", "A").upper()

    if scenario == "A":
        plan = build_plan_autonomy_1(trace)
    elif scenario == "B":
        plan = build_plan_autonomy_3(trace)
    else:
        raise SystemExit("Unsupported scenario. Use A or B.")

    execute_plan(plan)
