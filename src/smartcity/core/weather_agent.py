"""Weather Agent — LLM orchestrator for smart city pump management.

Replaces the planner + executor pair with an LLM agent that reasons over
the enriched MonitorEvent and calls MCP tools directly.

Flow (mirrors the sequence diagram):
  1. Receive enriched MonitorEvent from monitor
  2. Gather context via tools (pump status, weather forecast)
  3. Check OPA policy (abstracted security agent)
  4. Execute pump actions via pump-mcp
  5. Digital twin is synced automatically by turn_on/off_pump_and_sync
  6. Notify citizen-facing interface of the outcome

If LLM is unavailable (no API key), falls back to the rule-based executor.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

import requests
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from ..infra.audit import record_event
from ..infra.logging_utils import configure_logger
from ..infra.fiware_mcp_client import update_attribute as _update_orion_attribute
from ..infra.pump_mcp_client import (
    get_pump_status as _get_pump_status,
    turn_off_pump as _turn_off_pump,
    turn_on_pump as _turn_on_pump,
)
from ..infra.weather_mcp_client import get_rainfall_risk as _get_rainfall_risk
from .models import MonitorEvent
from .policy_engine import evaluate_plan

logger = configure_logger("weather_agent")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
LLM_AGENT_ENABLED = os.getenv("LLM_AGENT_ENABLED", "true").lower() == "true"
CITIZEN_INTERFACE_URL = os.getenv("CITIZEN_INTERFACE_URL", "http://localhost:8020")

# ── Tools ─────────────────────────────────────────────────────────────


@tool
async def get_pump_status(pump_id: str) -> str:
    """
    Retorna o estado atual de uma bomba (on/off, vazão, última atualização).

    Args:
        pump_id: ID da bomba — ex: 'PumpDevice:001'
    """
    result = await _get_pump_status(pump_id)
    return json.dumps(result, ensure_ascii=False)


@tool
async def activate_pump(pump_id: str) -> str:
    """
    Liga uma bomba via Pump MCP e atualiza o digital twin no Orion via FIWARE MCP.
    Use quando há risco de alagamento confirmado e a política permite.

    Args:
        pump_id: ID da bomba — ex: 'PumpDevice:001'
    """
    # 1. Actua no dispositivo físico via Pump MCP
    result = await _turn_on_pump(pump_id)

    # 2. Sincroniza o digital twin no Orion via FIWARE MCP
    if result and result.get("success"):
        pump = result.get("pump", {})
        await _update_orion_attribute(pump_id, "status", "on")
        await _update_orion_attribute(pump_id, "flow_rate_m3h", str(pump.get("flow_rate_m3h", 0)))

    return json.dumps(result, ensure_ascii=False)


@tool
async def deactivate_pump(pump_id: str) -> str:
    """
    Desliga uma bomba via Pump MCP e atualiza o digital twin no Orion via FIWARE MCP.

    Args:
        pump_id: ID da bomba — ex: 'PumpDevice:001'
    """
    # 1. Actua no dispositivo físico via Pump MCP
    result = await _turn_off_pump(pump_id)

    # 2. Sincroniza o digital twin no Orion via FIWARE MCP
    if result and result.get("success"):
        await _update_orion_attribute(pump_id, "status", "off")
        await _update_orion_attribute(pump_id, "flow_rate_m3h", "0.0")

    return json.dumps(result, ensure_ascii=False)


@tool
def check_policy(pump_id: str, action: str, risk_level: str = "low") -> str:
    """
    Verifica com o OPA se a ação é permitida (Security Agent abstrato).
    Deve ser chamado ANTES de activate_pump ou deactivate_pump.

    Args:
        pump_id:    ID da bomba — ex: 'PumpDevice:001'
        action:     'turnOnPump' ou 'turnOffPump'
        risk_level: 'low', 'medium' ou 'high'

    Returns:
        JSON com {allowed, approval_mode, reason, risk_level}
    """
    trace_id = str(uuid.uuid4())
    autonomy = {"low": 1, "medium": 2, "high": 3}.get(risk_level.lower(), 2)
    plan_dict = {
        "plan_id": str(uuid.uuid4()),
        "goal": f"Execute {action} on {pump_id}",
        "scenario": "flood-response",
        "risk_level": risk_level.lower(),
        "steps": [{"id": "step-1", "action": action, "params": {"entity_id": pump_id}}],
        "approval": {"autonomy_level": autonomy},
        "telemetry": {"traceId": trace_id},
    }
    try:
        decision = evaluate_plan(plan_dict, trace_id)
        return json.dumps({
            "allowed": decision.allowed,
            "approval_mode": decision.approval_mode.value,
            "reason": decision.reason,
            "risk_level": decision.risk_level.value,
        })
    except Exception as exc:
        return json.dumps({"allowed": False, "reason": str(exc)})


@tool
async def request_human_approval(
    pump_id: str,
    action: str,
    risk_level: str,
    reason: str,
) -> str:
    """
    Solicita aprovação humana ao operador via Citizen Interface e aguarda a decisão (até 3 min).
    Use quando check_policy retornar approval_mode=human — NÃO notifique e pare; aguarde a resposta.

    Args:
        pump_id:    ID da bomba — ex: 'PumpDevice:001'
        action:     'turnOnPump' ou 'turnOffPump'
        risk_level: 'low', 'medium' ou 'high'
        reason:     Motivo da solicitação (precipitação, previsão, etc.)

    Returns:
        JSON com {approved: bool, status: 'approved'|'denied'|'timeout'}
    """
    import asyncio

    try:
        resp = requests.post(
            f"{CITIZEN_INTERFACE_URL}/approvals",
            json={"pump_id": pump_id, "action": action,
                  "risk_level": risk_level, "reason": reason},
            timeout=5,
        )
        resp.raise_for_status()
        approval_id = resp.json()["approval_id"]
    except Exception as exc:
        return json.dumps({"approved": False, "status": "error", "error": str(exc)})

    # Poll for decision — up to 3 minutes (36 × 5 s)
    for _ in range(36):
        await asyncio.sleep(5)
        try:
            poll = requests.get(
                f"{CITIZEN_INTERFACE_URL}/approvals/{approval_id}", timeout=5
            )
            status = poll.json().get("status", "pending")
            if status != "pending":
                return json.dumps({"approved": status == "approved", "status": status})
        except Exception:
            pass

    return json.dumps({"approved": False, "status": "timeout"})


@tool
def notify_citizen(message: str, level: str = "info") -> str:
    """
    Envia notificação para a Citizen-Facing Interface (operadores, cidadãos).
    Use ao final para informar o resultado das ações tomadas.

    Args:
        message: Mensagem descritiva da ação ou situação
        level:   'info', 'warning' ou 'alert'
    """
    try:
        response = requests.post(
            f"{CITIZEN_INTERFACE_URL}/notify",
            json={"message": message, "level": level},
            timeout=5,
        )
        return json.dumps({"status": "sent", "code": response.status_code})
    except Exception as exc:
        logger.warning("Citizen interface unavailable: %s", exc)
        return json.dumps({"status": "unavailable", "error": str(exc)})


@tool
async def get_weather_forecast(latitude: float, longitude: float) -> str:
    """
    Consulta o risco de alagamento previsto para as próximas 6 horas via Weather MCP (Open-Meteo).
    Retorna precipitação total prevista, máximo horário e classificação de risco:
      baixo (<5mm), médio (5–20mm), alto (20–50mm), crítico (≥50mm).
    Chame SEMPRE como primeiro passo para enriquecer a análise de risco antes de agir.

    Args:
        latitude:  Latitude da estação em graus decimais (ex: -5.7945)
        longitude: Longitude da estação em graus decimais (ex: -35.2094)
    """
    result = await _get_rainfall_risk(latitude, longitude, hours=6)
    return json.dumps(result, ensure_ascii=False)


_TOOLS = [get_weather_forecast, get_pump_status, activate_pump, deactivate_pump, check_policy, request_human_approval, notify_citizen]
_TOOL_MAP = {t.name: t for t in _TOOLS}

# ── Agent loop ────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are a smart city pump management agent for flood prevention.

You received a weather alert from a sensor station. Follow these steps in order:
1. Call get_weather_forecast with the station coordinates to get the 6-hour rainfall risk forecast
2. Combine the current sensor reading with the forecast to assess flood risk (low/medium/high)
3. Call check_policy to verify if action is allowed
4. If policy returns approval_mode=auto → activate the pump directly, then notify_citizen
5. If policy returns approval_mode=human → call request_human_approval and WAIT for the response
   - If approved → activate the pump, then notify_citizen
   - If denied or timeout → notify_citizen that action was not taken
6. If no pump action is needed → notify_citizen with a status update

IMPORTANT: When approval_mode=human, always use request_human_approval — never skip waiting for the operator.
Risk levels: LOW = forecast<5mm | MEDIUM = forecast 5–20mm | HIGH = forecast>20mm or current precipitation>20mm
Pump IDs always use the format 'PumpDevice:001' — never 'Pump:001'."""


def _build_prompt(event: MonitorEvent) -> str:
    obs_summary = []
    for obs in (event.weather_observations or []):
        lon, lat = obs.coordinates if obs.coordinates else (None, None)
        obs_summary.append(
            f"  - Station {obs.station_id}: "
            f"precipitation={obs.precipitation}mm, "
            f"humidity={obs.humidity}%, "
            f"pressure={obs.atmospheric_pressure}hPa, "
            f"coordinates=(lat={lat}, lon={lon}), "
            f"pump_id={obs.pump_id}"
        )
    return (
        f"Weather alert received (type={event.event_type}):\n"
        + "\n".join(obs_summary or ["  No observations."])
        + "\n\nStart by calling get_weather_forecast for each station's coordinates."
    )


async def _dispatch_tool(tool_call: dict) -> Any:
    """Execute a LangChain tool call and return the result."""
    name = tool_call["name"]
    args = tool_call["args"]
    t = _TOOL_MAP.get(name)
    if t is None:
        return json.dumps({"error": f"Unknown tool: {name}"})
    try:
        # ainvoke handles both sync and async tools correctly inside an async context
        return await t.ainvoke(args)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


def _infer_outcome(tool_calls: list, ctx: Dict[str, Any]) -> str:
    """Derive a human-readable outcome key from what the agent did."""
    if "activate_pump" in tool_calls:
        return "pump_activated"
    if "deactivate_pump" in tool_calls:
        return "pump_deactivated"
    if ctx.get("approval_status") == "denied":
        return "approval_denied"
    if ctx.get("approval_status") == "timeout":
        return "approval_timeout"
    return "no_action"


def _post_run_report(report_data: Dict[str, Any]) -> None:
    """Fire-and-forget: post execution report to citizen interface (best effort)."""
    try:
        requests.post(
            f"{CITIZEN_INTERFACE_URL}/runs",
            json=report_data,
            timeout=3,
        )
    except Exception as exc:
        logger.warning("Could not post run report: %s", exc)


async def run(event: MonitorEvent, trace_id: str) -> Dict[str, Any]:
    """
    Run the Weather Agent agentic loop.

    Returns a summary dict with agent response and actions taken.
    Falls back to rule-based executor if LLM is unavailable.
    """
    import time
    start_ms = time.monotonic() * 1000

    if not LLM_AGENT_ENABLED or not OPENAI_API_KEY:
        logger.info(
            "Weather Agent disabled — falling back to rule-based executor",
            extra={"traceId": trace_id},
        )
        return await _fallback(event, trace_id)

    try:
        llm = ChatOpenAI(
            api_key=OPENAI_API_KEY,
            model=OPENAI_MODEL,
            temperature=0,
        )
        llm_with_tools = llm.bind_tools(_TOOLS)
    except Exception as exc:
        logger.error("Failed to initialize LLM: %s", exc, extra={"traceId": trace_id})
        return await _fallback(event, trace_id)

    messages = [
        HumanMessage(content=f"{_SYSTEM_PROMPT}\n\n{_build_prompt(event)}"),
    ]

    record_event(
        component="weather_agent",
        event_type="AGENT_STARTED",
        trace_id=trace_id,
        actor="weather_agent",
        outcome="started",
        payload={"model": OPENAI_MODEL, "observations": len(event.weather_observations or [])},
    )

    tool_calls_made = []
    # Context collected during tool execution for the run report
    ctx: Dict[str, Any] = {}
    max_iterations = 10
    response = None

    for iteration in range(max_iterations):
        response: AIMessage = await llm_with_tools.ainvoke(messages)
        messages.append(response)

        if not response.tool_calls:
            logger.info(
                "Weather Agent completed",
                extra={
                    "traceId": trace_id,
                    "extra_fields": {
                        "iterations": iteration + 1,
                        "tool_calls": len(tool_calls_made),
                        "response": response.content[:200] if response.content else "",
                    },
                },
            )
            break

        for tool_call in response.tool_calls:
            name = tool_call["name"]
            args = tool_call["args"]
            tool_calls_made.append(name)
            logger.info(
                "Agent tool call",
                extra={"traceId": trace_id, "extra_fields": {"tool": name, "args": args}},
            )
            result_str = await _dispatch_tool(tool_call)
            messages.append(ToolMessage(content=str(result_str), tool_call_id=tool_call["id"]))

            # Harvest key context from tool results
            try:
                result = json.loads(result_str) if isinstance(result_str, str) else result_str
            except Exception:
                result = {}

            if name == "get_weather_forecast" and isinstance(result, dict):
                ctx["forecast_risk"] = result.get("flood_risk")
                ctx["forecast_precipitation_mm"] = result.get("total_precipitation_mm")

            elif name == "check_policy" and isinstance(result, dict):
                ctx["risk_level"] = args.get("risk_level")
                ctx["policy_decision"] = result.get("approval_mode")

            elif name == "request_human_approval" and isinstance(result, dict):
                ctx["approval_status"] = result.get("status")  # approved|denied|timeout
                ctx["pump_id"] = args.get("pump_id")
                ctx["action_taken"] = args.get("action")

            elif name in ("activate_pump", "deactivate_pump"):
                ctx.setdefault("pump_id", args.get("pump_id"))
                ctx.setdefault("action_taken",
                               "turnOnPump" if name == "activate_pump" else "turnOffPump")
                if ctx.get("approval_status") is None:
                    ctx["approval_status"] = "n/a"

    record_event(
        component="weather_agent",
        event_type="AGENT_COMPLETED",
        trace_id=trace_id,
        actor="weather_agent",
        outcome="completed",
        payload={
            "tool_calls": tool_calls_made,
            "iterations": len([m for m in messages if isinstance(m, AIMessage)]),
            "summary": response.content[:500] if response.content else "",
        },
    )

    outcome = _infer_outcome(tool_calls_made, ctx)
    duration_ms = time.monotonic() * 1000 - start_ms

    # Build and post structured execution report
    report_data = {
        "trace_id": trace_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "llm_agent",
        "observations": [
            {
                "station_id": obs.station_id,
                "precipitation_mm": obs.precipitation,
                "humidity_pct": obs.humidity,
                "pressure_hpa": obs.atmospheric_pressure,
                "pump_id": obs.pump_id,
            }
            for obs in (event.weather_observations or [])
        ],
        "forecast_risk": ctx.get("forecast_risk"),
        "forecast_precipitation_mm": ctx.get("forecast_precipitation_mm"),
        "risk_level": ctx.get("risk_level"),
        "policy_decision": ctx.get("policy_decision"),
        "approval_status": ctx.get("approval_status"),
        "action_taken": ctx.get("action_taken"),
        "pump_id": ctx.get("pump_id"),
        "tool_calls": tool_calls_made,
        "agent_summary": response.content if response else "",
        "outcome": outcome,
        "duration_ms": round(duration_ms),
    }
    _post_run_report(report_data)

    return {
        "source": "llm_agent",
        "model": OPENAI_MODEL,
        "tool_calls": tool_calls_made,
        "summary": response.content if response else "",
    }


async def _fallback(event: MonitorEvent, trace_id: str) -> Dict[str, Any]:
    """Fallback to rule-based planner + executor when LLM is unavailable."""
    from .executor import execute_candidate_plan
    from .planner import build_candidate_plan

    plan = build_candidate_plan(event, trace_id)
    report = execute_candidate_plan(plan)
    return {
        "source": "rule_based",
        "executed": report.executed,
        "plan_id": report.plan_id,
    }
