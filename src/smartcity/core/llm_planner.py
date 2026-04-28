"""LLM-based planner using LangChain for intelligent traffic management planning."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from dotenv import load_dotenv
# Handle optional LangChain imports
try:
    from langchain_core.prompts import PromptTemplate
    from langchain_openai import ChatOpenAI

    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False

    class PromptTemplate:
        """Dummy PromptTemplate for when LangChain is not installed."""

        def __init__(self, *args, **kwargs):
            pass

        def format(self, **kwargs):
            return ""

    class ChatOpenAI:
        """Dummy ChatOpenAI for when LangChain is not installed."""

        def __init__(self, *args, **kwargs):
            pass

        def invoke(self, prompt):
            return None


from ..infra.logging_utils import configure_logger
from .models import ActionType, MonitorEvent, RiskLevel, validate_plan_dict

load_dotenv()

logger = configure_logger("llm_planner")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4-turbo")
TRAFFIC_SIGNAL_ID = os.getenv("TRAFFIC_SIGNAL_ID", "TrafficSignal:001")
LLM_PLANNER_ENABLED = os.getenv("LLM_PLANNER_ENABLED", "false").lower() == "true"
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.3"))


PLAN_GENERATION_PROMPT = PromptTemplate(
    input_variables=["event_data", "available_actions", "schema_example"],
    template="""You are an intelligent traffic management planner for a smart city system.
Your task is to generate a traffic management plan in response to a monitoring event.

## Event Data
{event_data}

## Available Actions
{available_actions}

## Plan Schema (REQUIRED - must match exactly)
{schema_example}

## Instructions
1. Analyze the event and determine the appropriate response
2. Generate a sequence of actionable steps
3. Return ONLY valid JSON matching the schema above
4. Set risk_level based on event severity:
   - LOW: normal conditions, light rain
   - MEDIUM: heavy rain, moderate crowd
   - HIGH: flood risk, ambulance detected
5. Set autonomy_level:
   - 1 for LOW risk (auto-approve)
   - 2 for MEDIUM risk (human review)
   - 3 for HIGH risk (human review required)
6. Use realistic goal and scenario descriptions
7. Always include exactly 3 steps: read-state, set-priority, notify

## Output
Return ONLY the JSON plan, no explanation or markdown:
""",
)


def _get_available_actions_description() -> str:
    """Generate description of available actions for the LLM."""
    return f"""
1. {ActionType.GET_TRAFFIC_SIGNAL_STATE.value}
   - Reads current state of traffic signal
   - Required params: entity_id (string)
   
2. {ActionType.SET_PRIORITY_CORRIDOR.value}
   - Sets priority corridor mode
   - Required params: entity_id (string), value (enum: "emergency", "critical-infra", "none")
   
3. {ActionType.NOTIFY_TRAFFIC_AGENTS.value}
   - Notifies traffic agents of situation
   - Required params: message (string)
"""


def _get_schema_example() -> str:
    """Generate example of plan schema for the LLM."""
    return json.dumps(
        {
            "plan_id": "uuid-will-be-generated",
            "goal": "Create emergency corridor for ambulance",
            "scenario": "ambulance-only",
            "risk_level": "high",
            "steps": [
                {
                    "id": "read-state",
                    "action": ActionType.GET_TRAFFIC_SIGNAL_STATE.value,
                    "params": {"entity_id": TRAFFIC_SIGNAL_ID},
                },
                {
                    "id": "set-priority",
                    "action": ActionType.SET_PRIORITY_CORRIDOR.value,
                    "params": {"entity_id": TRAFFIC_SIGNAL_ID, "value": "emergency"},
                },
                {
                    "id": "notify",
                    "action": ActionType.NOTIFY_TRAFFIC_AGENTS.value,
                    "params": {"message": "Emergency corridor activated for ambulance"},
                },
            ],
            "approval": {"autonomy_level": 3},
            "telemetry": {"traceId": "will-be-injected"},
        },
        indent=2,
    )


def _get_llm_client() -> Optional[ChatOpenAI]:
    """Initialize LangChain ChatOpenAI client if API key is available."""
    if not LANGCHAIN_AVAILABLE:
        logger.warning("LangChain not installed; LLM planner unavailable")
        return None

    if not OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not set; LLM planner unavailable")
        return None

    try:
        return ChatOpenAI(
            api_key=OPENAI_API_KEY,
            model=OPENAI_MODEL,
            temperature=LLM_TEMPERATURE,
        )
    except Exception as e:
        logger.error(
            "Failed to initialize ChatOpenAI client",
            extra={"error": str(e)},
        )
        return None


def _parse_llm_response(response_text: str, trace_id: str) -> Optional[Dict[str, Any]]:
    """Extract and validate JSON from LLM response."""
    try:
        # Try to parse the response as JSON
        response_text = response_text.strip()
        if response_text.startswith("```json"):
            response_text = response_text[7:]
        if response_text.startswith("```"):
            response_text = response_text[3:]
        if response_text.endswith("```"):
            response_text = response_text[:-3]

        plan_data = json.loads(response_text.strip())
        logger.debug(
            "LLM response parsed successfully",
            extra={"traceId": trace_id, "plan_data": plan_data},
        )
        return plan_data
    except json.JSONDecodeError as e:
        logger.warning(
            "Failed to parse LLM response as JSON",
            extra={
                "traceId": trace_id,
                "error": str(e),
                "response": response_text[:200],
            },
        )
        return None


def generate_plan_with_llm(
    event: MonitorEvent, trace_id: str
) -> Optional[Dict[str, Any]]:
    """
    Generate a traffic management plan using LangChain LLM.

    Args:
        event: Monitoring event containing traffic and weather conditions
        trace_id: Trace ID for logging and telemetry

    Returns:
        Dictionary representing the candidate plan, or None if generation fails
    """
    if not LLM_PLANNER_ENABLED:
        return None

    if not LANGCHAIN_AVAILABLE:
        logger.debug(
            "LangChain not available; LLM planner disabled",
            extra={"traceId": trace_id},
        )
        return None

    llm = _get_llm_client()
    if not llm:
        return None

    try:
        # Prepare prompt inputs
        event_data = json.dumps(
            {
                "event_type": event.event_type,
                "ambulance_detected": event.ambulance_detected,
                "heavy_rain": event.heavy_rain,
                "flood_risk": event.flood_risk,
                "crowd_level": event.crowd_level,
                "location": event.location,
                "notes": event.notes,
            },
            indent=2,
        )

        available_actions = _get_available_actions_description()
        schema_example = _get_schema_example()

        # Build and invoke the chain
        prompt = PLAN_GENERATION_PROMPT.format(
            event_data=event_data,
            available_actions=available_actions,
            schema_example=schema_example,
        )

        logger.debug(
            "Invoking LLM for plan generation",
            extra={"traceId": trace_id, "model": OPENAI_MODEL},
        )

        response = llm.invoke(prompt)
        response_text = response.content

        logger.debug(
            "LLM response received",
            extra={"traceId": trace_id, "response_length": len(response_text)},
        )

        # Parse and validate response
        plan_data = _parse_llm_response(response_text, trace_id)
        if not plan_data:
            return None

        # Inject trace ID and set plan_id
        import uuid

        plan_data.setdefault("plan_id", str(uuid.uuid4()))
        plan_data.setdefault("telemetry", {})
        plan_data["telemetry"]["traceId"] = trace_id

        # Validate against schema
        try:
            validated_plan = validate_plan_dict(plan_data)
            logger.info(
                "LLM plan generated and validated successfully",
                extra={
                    "traceId": trace_id,
                    "plan_id": validated_plan.plan_id,
                    "scenario": validated_plan.scenario,
                    "risk_level": validated_plan.risk_level.value,
                },
            )
            return plan_data
        except ValueError as e:
            logger.warning(
                "Generated plan failed validation",
                extra={"traceId": trace_id, "error": str(e)},
            )
            return None

    except Exception as e:
        logger.error(
            "Error during LLM plan generation",
            extra={"traceId": trace_id, "error": str(e)},
        )
        return None
