"""
Async client para o Pump MCP Server.

Wraps fastmcp.Client para comunicação com o servidor de bombas.
O servidor deve estar rodando antes das chamadas — configure
PUMP_MCP_URL para o endpoint HTTP (padrão: http://localhost:8002/mcp).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, List

from fastmcp import Client

from .fiware_mcp_client import get_entities

logger = logging.getLogger(__name__)

PUMP_MCP_URL = os.getenv("PUMP_MCP_URL", "http://localhost:8002/mcp")


async def _call(tool_name: str, params: dict) -> Any:
    """Chama uma ferramenta do Pump MCP e retorna o resultado."""
    async with Client(PUMP_MCP_URL) as client:
        result = await client.call_tool(tool_name, params)

    items = result.content if hasattr(result, "content") else result
    if not items:
        return None

    first = items[0]
    text = first.text if hasattr(first, "text") else str(first)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return text


async def get_pump_status(pump_id: str = "Pump:001") -> dict:
    """Retorna o estado atual de uma bomba."""
    return await _call("get_pump_status", {"pump_id": pump_id})


async def turn_on_pump(pump_id: str = "Pump:001") -> dict:
    """Liga uma bomba."""
    return await _call("turn_on_pump", {"pump_id": pump_id})


async def turn_off_pump(pump_id: str = "Pump:001") -> dict:
    """Desliga uma bomba."""
    return await _call("turn_off_pump", {"pump_id": pump_id})


async def list_pumps() -> dict:
    """Lista todas as bombas e seus estados."""
    return await _call("list_pumps", {})


async def get_available_pumps() -> List[dict]:
    """
    Retorna as bombas disponíveis (status 'off') consultando os digital twins no Orion.

    Usa o FIWARE MCP para buscar entidades do tipo PumpDevice com status=off,
    refletindo o último estado registrado pelo executor após cada atuação.

    Returns:
        Lista de entidades PumpDevice disponíveis para acionamento.
    """
    result = await get_entities(
        entity_type="PumpDevice",
        query="status==off",
        attrs="id,type,status,flow_rate_m3h,max_flow_rate_m3h,location",
    )
    return result.get("entities", []) if isinstance(result, dict) else []


async def get_nearest_pump(
    lon: float,
    lat: float,
    max_distance_m: int = 300,
) -> dict | None:
    """
    Retorna o PumpDevice disponível mais próximo das coordenadas fornecidas.

    Realiza uma geo-query no Orion via FIWARE MCP buscando entidades do tipo
    PumpDevice com status='off' dentro do raio de cobertura da estação.
    O raio é definido pela própria estação meteorológica (coverage_radius_m),
    permitindo que cada estação declare sua zona de influência no contexto.

    Args:
        lon: Longitude da estação meteorológica (GeoJSON: longitude primeiro).
        lat: Latitude da estação meteorológica.
        max_distance_m: Raio máximo de busca em metros (padrão: 300m).

    Returns:
        Entidade PumpDevice mais próxima disponível, ou None se não encontrada.
    """
    result = await get_entities(
        entity_type="PumpDevice",
        query="status==off",
        attrs="id,type,status,location,max_flow_rate_m3h",
        georel=f"near;maxDistance:{max_distance_m}",
        geometry="point",
        coords=f"{lat},{lon}",   # Orion usa lat,lon (inverso do GeoJSON)
        limit=1,                 # mais próxima apenas
    )
    entities = result.get("entities", []) if isinstance(result, dict) else []
    return entities[0] if entities else None
