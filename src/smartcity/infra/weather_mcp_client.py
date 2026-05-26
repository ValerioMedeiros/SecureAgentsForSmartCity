"""
Async client para o Weather Forecast MCP Server.

Wraps fastmcp.Client para comunicação com o servidor de previsão do tempo.
O servidor deve estar rodando antes das chamadas — configure
WEATHER_MCP_URL para o endpoint HTTP (padrão: http://localhost:8003/mcp).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from fastmcp import Client

logger = logging.getLogger(__name__)

WEATHER_MCP_URL = os.getenv("WEATHER_MCP_URL", "http://localhost:8003/mcp")


async def _call(tool_name: str, params: dict) -> Any:
    """Chama uma ferramenta do Weather MCP e retorna o resultado."""
    async with Client(WEATHER_MCP_URL) as client:
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


async def get_current_weather(
    latitude: float,
    longitude: float,
    timezone: str = "America/Sao_Paulo",
) -> dict:
    """Retorna as condições climáticas atuais para uma localização."""
    return await _call("get_current_weather", {
        "latitude": latitude,
        "longitude": longitude,
        "timezone": timezone,
    })


async def get_rainfall_risk(
    latitude: float,
    longitude: float,
    hours: int = 6,
    timezone: str = "America/Sao_Paulo",
) -> dict:
    """Avalia o risco de alagamento com base na previsão de precipitação."""
    return await _call("get_rainfall_risk", {
        "latitude": latitude,
        "longitude": longitude,
        "hours": hours,
        "timezone": timezone,
    })


async def get_hourly_forecast(
    latitude: float,
    longitude: float,
    hours: int = 24,
    timezone: str = "America/Sao_Paulo",
) -> dict:
    """Retorna previsão climática hora a hora."""
    return await _call("get_hourly_forecast", {
        "latitude": latitude,
        "longitude": longitude,
        "hours": hours,
        "timezone": timezone,
    })
