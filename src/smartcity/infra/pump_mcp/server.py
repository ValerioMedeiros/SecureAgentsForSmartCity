"""
Pump MCP Server — simulador de bombas da lagoa de captação.

Simula um dispositivo IoT que controla bombas de uma lagoa de captação.
O estado é mantido em memória durante a execução do servidor.

Transports:
  HTTP (default): uv run pump-mcp-server
  stdio:          uv run pump-mcp-server stdio
"""

import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict

from fastmcp import FastMCP

logger = logging.getLogger("pump_mcp")

mcp = FastMCP(
    name="Pump Controller",
    instructions=(
        "Tools for controlling water pumps of a stormwater retention lake (lagoa de captação). "
        "Use get_pump_status to check the current state of a pump, "
        "turn_on_pump to activate a pump, and turn_off_pump to deactivate it. "
        "Always check the current status before issuing a command."
    ),
)

# ── Estado em memória ─────────────────────────────────────────────────
# Simula o estado dos dispositivos IoT.
# Em produção, isso seria substituído por chamadas a um broker MQTT ou API real.

_pumps: Dict[str, Dict[str, Any]] = {
    "PumpDevice:001": {
        "id": "PumpDevice:001",
        "location": "Lagoa de Captação Norte",
        "status": "off",
        "flow_rate_m3h": 0.0,
        "max_flow_rate_m3h": 120.0,
        "last_updated": None,
    },
    "PumpDevice:002": {
        "id": "PumpDevice:002",
        "location": "Lagoa de Captação Sul",
        "status": "off",
        "flow_rate_m3h": 0.0,
        "max_flow_rate_m3h": 80.0,
        "last_updated": None,
    },
}

DEFAULT_PUMP_ID = "PumpDevice:001"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_pump(pump_id: str) -> Dict[str, Any] | None:
    return _pumps.get(pump_id)


# ── Tools ─────────────────────────────────────────────────────────────

@mcp.tool()
def get_pump_status(pump_id: str = DEFAULT_PUMP_ID) -> dict:
    """
    Retorna o estado atual de uma bomba.

    Args:
        pump_id: ID da bomba (ex: 'PumpDevice:001'). Padrão: 'PumpDevice:001'.

    Returns:
        Dict com id, location, status ('on'/'off'), flow_rate_m3h e last_updated.
    """
    pump = _get_pump(pump_id)
    if pump is None:
        return {"error": f"Bomba '{pump_id}' não encontrada. IDs disponíveis: {list(_pumps.keys())}"}
    return dict(pump)


@mcp.tool()
def turn_on_pump(pump_id: str = DEFAULT_PUMP_ID) -> dict:
    """
    Liga uma bomba da lagoa de captação.

    Ativa o escoamento de água com a vazão máxima da bomba.
    Retorna erro se a bomba já estiver ligada ou não for encontrada.

    Args:
        pump_id: ID da bomba (ex: 'PumpDevice:001'). Padrão: 'PumpDevice:001'.
    """
    pump = _get_pump(pump_id)
    if pump is None:
        return {"error": f"Bomba '{pump_id}' não encontrada. IDs disponíveis: {list(_pumps.keys())}"}

    if pump["status"] == "on":
        return {
            "success": False,
            "message": f"Bomba '{pump_id}' já está ligada.",
            "pump": dict(pump),
        }

    pump["status"] = "on"
    pump["flow_rate_m3h"] = pump["max_flow_rate_m3h"]
    pump["last_updated"] = _now()

    logger.info("Bomba %s ligada — vazão %.1f m³/h", pump_id, pump["flow_rate_m3h"])

    return {
        "success": True,
        "message": f"Bomba '{pump_id}' ligada com sucesso. Vazão: {pump['flow_rate_m3h']} m³/h.",
        "pump": dict(pump),
    }


@mcp.tool()
def turn_off_pump(pump_id: str = DEFAULT_PUMP_ID) -> dict:
    """
    Desliga uma bomba da lagoa de captação.

    Interrompe o escoamento de água. Retorna erro se a bomba já estiver
    desligada ou não for encontrada.

    Args:
        pump_id: ID da bomba (ex: 'PumpDevice:001'). Padrão: 'PumpDevice:001'.
    """
    pump = _get_pump(pump_id)
    if pump is None:
        return {"error": f"Bomba '{pump_id}' não encontrada. IDs disponíveis: {list(_pumps.keys())}"}

    if pump["status"] == "off":
        return {
            "success": False,
            "message": f"Bomba '{pump_id}' já está desligada.",
            "pump": dict(pump),
        }

    pump["status"] = "off"
    pump["flow_rate_m3h"] = 0.0
    pump["last_updated"] = _now()

    logger.info("Bomba %s desligada", pump_id)

    return {
        "success": True,
        "message": f"Bomba '{pump_id}' desligada com sucesso.",
        "pump": dict(pump),
    }


@mcp.tool()
def list_pumps() -> dict:
    """
    Lista todas as bombas disponíveis e seus estados atuais.
    """
    return {"pumps": [dict(p) for p in _pumps.values()]}


# ── Resources ─────────────────────────────────────────────────────────

@mcp.resource("pump://status/{pump_id}")
def pump_status_resource(pump_id: str) -> str:
    """Estado atual de uma bomba como contexto somente leitura."""
    import json
    pump = _get_pump(pump_id)
    if pump is None:
        return json.dumps({"error": f"Bomba '{pump_id}' não encontrada."})
    return json.dumps(dict(pump), ensure_ascii=False, indent=2)


@mcp.resource("pump://status")
def all_pumps_resource() -> str:
    """Estado de todas as bombas como contexto somente leitura."""
    import json
    return json.dumps({"pumps": [dict(p) for p in _pumps.values()]}, ensure_ascii=False, indent=2)


# ── Entrypoint ────────────────────────────────────────────────────────

def main():
    transport = sys.argv[1] if len(sys.argv) > 1 else "http"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8002
    if transport == "http":
        mcp.run(transport="streamable-http", port=port, host="0.0.0.0")
    else:
        mcp.run(transport=transport)


if __name__ == "__main__":
    main()
