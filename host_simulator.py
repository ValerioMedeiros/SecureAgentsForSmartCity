import os
import uuid
import json
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


AGENT_PROMPT = """
You are a Smart City Traffic Management Agent responsible for coordinating traffic signals to ensure public safety and efficient traffic flow.

Your role is to analyze incoming traffic-related requests and determine the appropriate response plan.

## Decision Criteria

**Autonomy Level 1 (Automatic)** - Use for:
- Emergency vehicle corridors (ambulance, fire truck, police car)
- Time-critical medical emergencies
- Active pursuit or rescue operations

**Autonomy Level 3 (Supervised - requires human approval)** - Use for:
- Severe weather conditions (heavy rain, flooding, ice, storms)
- Critical infrastructure protection
- Large-scale traffic rerouting
- Situations affecting vulnerable areas (hospitals, schools, power plants)

## Available Actions

1. `execute_traffic_plan(autonomy_level: int, reason: str)` - Execute a traffic management plan
   - autonomy_level: 1 for automatic emergency response, 3 for supervised critical situations
   - reason: Brief description of why this plan was chosen

## Instructions

1. Analyze the user's request to understand the traffic situation
2. Classify the situation into the appropriate autonomy level
3. Call the execute_traffic_plan tool with your decision
4. Explain your reasoning to the user

Always prioritize public safety. When in doubt between levels, choose the higher autonomy level (3) to ensure human oversight.
"""


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
            "params": {
                "message": "Heavy rain: rerouting around critical infrastructure"
            },
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
    logger.info(
        "Generated plan",
        extra={"traceId": trace_id, "extra_fields": {"plan_id": plan["plan_id"]}},
    )

    decision = evaluate_plan(plan, provided_token=USER_TOKEN, trace_id=trace_id)
    if not decision.allowed:
        logger.warning(
            "Plan rejected",
            extra={"traceId": trace_id, "extra_fields": decision.to_dict()},
        )
        return

    for step in plan["steps"]:
        call_payload = {
            "method": step["tool"],
            "params": step.get("params", {}),
            "traceId": trace_id,
            "token": USER_TOKEN,
        }
        logger.info(
            "Executing step",
            extra={
                "traceId": trace_id,
                "extra_fields": {"step": step["id"], "tool": step["tool"]},
            },
        )
        response = requests.post(MCP_SERVER_URL, json=call_payload, timeout=10)
        logger.info(
            "MCP response",
            extra={
                "traceId": trace_id,
                "extra_fields": {
                    "status": response.status_code,
                    "body": response.text,
                },
            },
        )
        response.raise_for_status()



def build_and_execute_plan(autonomy_level: int, trace_id: str) -> Dict[str, Any]:
    """
    Build and execute a traffic management plan based on the specified autonomy level.
    autonomy_level: 1 for automatic emergency response, 3 for supervised critical situations
    trace_id: Unique identifier for tracing the request
    """
    if autonomy_level == 1:
        plan = build_plan_autonomy_1(trace_id)
    elif autonomy_level == 3:
        plan = build_plan_autonomy_3(trace_id)
    else:
        raise ValueError(f"Unsupported autonomy level: {autonomy_level}")

    execute_plan(plan)
    return {"status": "Plan executed", "plan_id": plan["plan_id"]}


def build_agent():
    from langchain.agents import create_agent
    from langchain_openai import ChatOpenAI

    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError(
            "LLM path not available: OPENAI_API_KEY missing"
        )

    llm = ChatOpenAI(
        model=os.getenv("OPENAI_MODEL", "gpt-5-nano"),
        temperature=0,
    )

    tools = [build_and_execute_plan]

    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=AGENT_PROMPT,
    )

    return agent


if __name__ == "__main__":
    trace = str(uuid.uuid4())
    scenario = os.getenv("SCENARIO", "A").upper()

    # Example queries
    queries = [
        "There is a firefighter truck that has to go across the town",
        "Heavy rain is expected and we need to protect the power plant area",
        "An ambulance needs to reach the hospital urgently",
    ]

    prompt = " Trace ID: " + trace + "\n" + queries[1]

    if scenario == "A":
        plan = build_plan_autonomy_1(trace)
        execute_plan(plan)
    elif scenario == "B":
        plan = build_plan_autonomy_3(trace)
        execute_plan(plan)
    elif scenario == "LLM":
        agent = build_agent()
        response = agent.invoke({"messages": [{"role": "user", "content": prompt}]})
        print("Agent Response:", response)
    else:
        raise SystemExit("Unsupported scenario. Use A, B or LLM.")
