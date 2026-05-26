"""LLM-based planner using LangChain for intelligent pump/flood management planning."""

from __future__ import annotations

import json
import os
import re
import uuid
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI

from ..infra.logging_utils import configure_logger
from .models import ActionType, MonitorEvent, validate_plan_dict  # noqa: F401

load_dotenv()

logger = configure_logger("llm_planner")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4-turbo")
LLM_PLANNER_ENABLED = os.getenv("LLM_PLANNER_ENABLED", "true").lower() == "true"
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.3"))

# Regex patterns for PII masking before logging
_PII_PATTERNS = [
    (re.compile(r"\btoken\s*[:=]\s*\S+", re.IGNORECASE), "token: [REDACTED]"),
    (re.compile(r"\bpassword\s*[:=]\s*\S+", re.IGNORECASE), "password: [REDACTED]"),
    (re.compile(r"\bapi[_-]?key\s*[:=]\s*\S+", re.IGNORECASE), "api_key: [REDACTED]"),
]

PLAN_GENERATION_PROMPT = PromptTemplate(
    input_variables=["event_data", "available_actions", "available_pumps", "schema_example"],
    template="""You are an intelligent pump management planner for a smart city system.
Your task is to generate a pump management plan in response to a monitoring event.

## Event Data
{event_data}

## Available Actions
{available_actions}

## Available Pumps
{available_pumps}

## Plan Schema (REQUIRED - must match exactly)
{schema_example}

## Instructions
1. Analyze the event and determine the appropriate response
2. Generate a sequence of actionable steps
3. Return ONLY valid JSON matching the schema above
4. Set risk_level based on event severity:
   - LOW: normal conditions, light rain
   - MEDIUM: heavy rain, moderate wind
   - HIGH: flood risk, heavy winds
5. Use realistic goal and scenario descriptions based on the event context

## Output
Return ONLY the JSON plan, no explanation or markdown:
""",
)

SAFETY_CHECK_PROMPT = PromptTemplate(
    input_variables=["plan_json"],
    template="""You are a safety reviewer for a smart city pump management system.
Evaluate if the following plan is safe and appropriate to execute.
A plan is UNSAFE if it: activates pumps without flood context, contains contradictory steps,
or could cause infrastructure damage.

Plan:
{plan_json}

Respond with only 'SAFE' or 'UNSAFE' followed by a brief reason (max 20 words).""",
)


def _mask_pii(text: str) -> str:
    """Apply regex-based PII masking before logging."""
    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _get_available_actions_description() -> str:
    return f"""
1. {ActionType.TURN_ON_PUMP.value}
   - Turns on a pump to drain floodwater
   - Required params: entity_id (string)

2. {ActionType.TURN_OFF_PUMP.value}
   - Turns off a pump
   - Required params: entity_id (string)

3. {ActionType.NOTIFY_USER.value}
   - Notifies an operator or team
   - Required params: message (string), user_id (string)
"""


def _get_schema_example() -> str:
    return json.dumps(
        {
            "plan_id": "uuid-will-be-generated",
            "goal": "Prevent basement flooding on Avenue 1",
            "scenario": "heavy-precipitation",
            "risk_level": "high",
            "steps": [
                {
                    "id": "turn-on-pump-1",
                    "action": ActionType.TURN_ON_PUMP.value,
                    "params": {"entity_id": "Pump:001"},
                },
                {
                    "id": "notify-owner",
                    "action": ActionType.NOTIFY_USER.value,
                    "params": {
                        "message": "Pumps activated due to heavy precipitation",
                        "user_id": "ops-team",
                    },
                },
            ],
            "approval": {"autonomy_level": 2},
            "telemetry": {"traceId": "will-be-injected"},
        },
        indent=2,
    )


def _safety_check(plan_json: str, llm: ChatOpenAI, trace_id: str) -> bool:
    """
    Guardrail: use a lightweight LLM call to evaluate plan safety.
    Returns True if SAFE, False if UNSAFE.
    """
    try:
        prompt = SAFETY_CHECK_PROMPT.format(plan_json=plan_json)
        response = llm.invoke([HumanMessage(content=prompt)])
        verdict = response.content.strip().upper()
        is_safe = verdict.startswith("SAFE")
        if not is_safe:
            logger.warning(
                "Safety guardrail blocked LLM plan",
                extra={"traceId": trace_id, "verdict": verdict[:100]},
            )
        return is_safe
    except Exception as exc:
        logger.warning(
            "Safety guardrail check failed, defaulting to safe",
            extra={"traceId": trace_id, "error": str(exc)},
        )
        return True


def _get_llm_client() -> Optional[ChatOpenAI]:
    """Initialize LangChain ChatOpenAI client if API key is available."""
    if not OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not set; LLM planner unavailable")
        return None
    try:
        return ChatOpenAI(
            api_key=OPENAI_API_KEY,
            model=OPENAI_MODEL,
            temperature=LLM_TEMPERATURE,
        )
    except Exception as exc:
        logger.error("Failed to initialize ChatOpenAI client", extra={"error": str(exc)})
        return None


def _parse_llm_response(response_text: str, trace_id: str) -> Optional[Dict[str, Any]]:
    """Extract and validate JSON from LLM response."""
    try:
        text = response_text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]

        plan_data = json.loads(text.strip())
        logger.debug("LLM response parsed successfully", extra={"traceId": trace_id})
        return plan_data
    except json.JSONDecodeError as exc:
        logger.warning(
            "Failed to parse LLM response as JSON",
            extra={
                "traceId": trace_id,
                "error": str(exc),
                "response": _mask_pii(response_text[:200]),
            },
        )
        return None


def generate_plan_with_llm(
    event: MonitorEvent, trace_id: str
) -> Optional[Dict[str, Any]]:
    """
    Generate a pump management plan using a LangChain LLM chain.

    Flow:
      1. Format prompt with event data
      2. Invoke ChatOpenAI to generate a JSON plan
      3. Run safety guardrail (second LLM call) to validate the plan
      4. Parse and validate against the CandidatePlan schema

    Returns the plan dict or None if generation/validation fails.
    """
    if not LLM_PLANNER_ENABLED:
        return None

    llm = _get_llm_client()
    if not llm:
        return None

    try:
        event_data = json.dumps(event.model_dump(by_alias=True), indent=2)

        prompt_text = PLAN_GENERATION_PROMPT.format(
            event_data=_mask_pii(event_data),
            available_actions=_get_available_actions_description(),
            available_pumps="Pump:001 (max 120 m³/h), Pump:002 (max 80 m³/h)",
            schema_example=_get_schema_example(),
        )

        logger.debug(
            "Invoking LLM for plan generation",
            extra={"traceId": trace_id, "model": OPENAI_MODEL},
        )

        response: AIMessage = llm.invoke([HumanMessage(content=prompt_text)])
        response_text = response.content

        logger.debug(
            "LLM response received",
            extra={"traceId": trace_id, "response_length": len(response_text)},
        )

        if not _safety_check(response_text, llm, trace_id):
            return None

        plan_data = _parse_llm_response(response_text, trace_id)
        if not plan_data:
            return None

        plan_data.setdefault("plan_id", str(uuid.uuid4()))
        plan_data.setdefault("telemetry", {})
        plan_data["telemetry"]["traceId"] = trace_id

        try:
            validated = validate_plan_dict(plan_data)
            logger.info(
                "LLM plan generated and validated",
                extra={
                    "traceId": trace_id,
                    "plan_id": validated.plan_id,
                    "scenario": validated.scenario,
                    "risk_level": validated.risk_level.value,
                },
            )
            return plan_data
        except ValueError as exc:
            logger.warning(
                "LLM plan failed schema validation",
                extra={"traceId": trace_id, "error": str(exc)},
            )
            return None

    except Exception as exc:
        logger.error(
            "Error during LLM plan generation",
            extra={"traceId": trace_id, "error": str(exc)},
        )
        return None
