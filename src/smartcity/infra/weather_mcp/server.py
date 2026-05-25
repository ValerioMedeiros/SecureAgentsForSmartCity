"""
Weather Forecast MCP Server — previsão do tempo via Open-Meteo para agentes LLM.

API utilizada: Open-Meteo (https://open-meteo.com)
  - 100% gratuita, sem necessidade de chave de API ou cadastro
  - Cobertura global, incluindo todo o Brasil
  - Modelo meteorológico ECMWF com atualização horária

Transports:
  stdio (default):  uv run weather-mcp-server
  HTTP:             uv run weather-mcp-server http 8002
"""

import json
import logging
import sys
from typing import Optional

from fastmcp import FastMCP

from .weather_client import WeatherClient, WeatherConnectionError

logger = logging.getLogger("weather_mcp")

mcp = FastMCP(
    name="Weather Forecast",
    instructions=(
        "Ferramentas de previsão do tempo via Open-Meteo (gratuito, sem chave de API). "
        "Use get_current_weather para condições em tempo real, get_hourly_forecast para previsão "
        "hora a hora, get_daily_forecast para resumo diário e get_rainfall_risk para avaliar risco "
        "de alagamento — essencial para decisões de controle de bombas e alertas à população. "
        "Coordenadas devem estar em graus decimais (latitude/longitude WGS-84). "
        "Para cidades brasileiras: São Paulo (-23.55, -46.63), Rio de Janeiro (-22.91, -43.17), "
        "Fortaleza (-3.72, -38.54), Recife (-8.05, -34.88), Salvador (-12.97, -38.50), "
        "Belo Horizonte (-19.92, -43.94), Manaus (-3.10, -60.02), Belém (-1.46, -48.50)."
    ),
)

_client = WeatherClient()


def _weather_error(e: Exception) -> dict:
    if isinstance(e, WeatherConnectionError):
        return {"error": f"Erro de conexão: {e}"}
    return {"error": f"Erro inesperado: {e}"}


# ── Condições atuais ────────────────────────────────────────────────────


@mcp.tool()
def get_current_weather(
    latitude: float,
    longitude: float,
    timezone: str = "America/Sao_Paulo",
) -> dict:
    """
    Retorna as condições climáticas atuais para uma localização.

    Args:
        latitude: Latitude em graus decimais (ex: -23.5505 para São Paulo).
        longitude: Longitude em graus decimais (ex: -46.6333 para São Paulo).
        timezone: Fuso horário IANA (padrão: America/Sao_Paulo).
            Outros fusos brasileiros: America/Manaus, America/Belem, America/Fortaleza,
            America/Recife, America/Porto_Velho, America/Boa_Vista, America/Noronha.
    """
    try:
        return _client.get_current(latitude, longitude, timezone)
    except Exception as e:
        return _weather_error(e)


# ── Previsão horária ────────────────────────────────────────────────────


@mcp.tool()
def get_hourly_forecast(
    latitude: float,
    longitude: float,
    hours: int = 24,
    timezone: str = "America/Sao_Paulo",
) -> dict:
    """
    Retorna previsão climática hora a hora.

    Cada entrada inclui temperatura, umidade, precipitação (volume e probabilidade),
    velocidade e rajadas de vento, e descrição da condição climática.

    Args:
        latitude: Latitude em graus decimais.
        longitude: Longitude em graus decimais.
        hours: Número de horas a prever (1–384, padrão 24). Máximo: 16 dias.
        timezone: Fuso horário IANA (padrão: America/Sao_Paulo).
    """
    try:
        return _client.get_hourly_forecast(latitude, longitude, hours, timezone)
    except Exception as e:
        return _weather_error(e)


# ── Previsão diária ─────────────────────────────────────────────────────


@mcp.tool()
def get_daily_forecast(
    latitude: float,
    longitude: float,
    days: int = 7,
    timezone: str = "America/Sao_Paulo",
) -> dict:
    """
    Retorna previsão climática diária com resumo do dia.

    Cada entrada inclui temperatura mínima/máxima, precipitação acumulada,
    probabilidade máxima de chuva, vento máximo e condição climática dominante.

    Args:
        latitude: Latitude em graus decimais.
        longitude: Longitude em graus decimais.
        days: Número de dias a prever (1–16, padrão 7).
        timezone: Fuso horário IANA (padrão: America/Sao_Paulo).
    """
    try:
        return _client.get_daily_forecast(latitude, longitude, days, timezone)
    except Exception as e:
        return _weather_error(e)


# ── Risco de alagamento ─────────────────────────────────────────────────


@mcp.tool()
def get_rainfall_risk(
    latitude: float,
    longitude: float,
    hours: int = 6,
    timezone: str = "America/Sao_Paulo",
) -> dict:
    """
    Avalia o risco de alagamento com base na previsão de precipitação.

    Retorna precipitação total e máxima horária previstas no período, com classificação
    de risco calibrada nas referências do CEMADEN (Centro Nacional de Monitoramento e
    Alertas de Desastres Naturais):
      - baixo:   precipitação acumulada < 5mm e probabilidade < 70%
      - médio:   precipitação acumulada 5–20mm ou probabilidade ≥ 70%
      - alto:    precipitação acumulada 20–50mm ou máximo horário ≥ 10mm/h
      - crítico: precipitação acumulada ≥ 50mm ou máximo horário ≥ 20mm/h

    Útil para: acionamento preventivo de bombas, alertas de alagamento,
    priorização de corredores de emergência e decisões do planejador MAPE-K.

    Args:
        latitude: Latitude em graus decimais.
        longitude: Longitude em graus decimais.
        hours: Janela de análise em horas (1–48, padrão 6).
        timezone: Fuso horário IANA (padrão: America/Sao_Paulo).
    """
    try:
        return _client.get_rainfall_risk(latitude, longitude, hours, timezone)
    except Exception as e:
        return _weather_error(e)


# ── Resources (contexto somente leitura) ────────────────────────────────


@mcp.resource("weather://current/{latitude}/{longitude}")
def current_weather_resource(latitude: str, longitude: str) -> str:
    """Condições climáticas atuais como contexto somente leitura."""
    try:
        result = _client.get_current(float(latitude), float(longitude))
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps(_weather_error(e))


@mcp.resource("weather://rainfall-risk/{latitude}/{longitude}")
def rainfall_risk_resource(latitude: str, longitude: str) -> str:
    """Risco de alagamento para as próximas 6 horas como contexto somente leitura."""
    try:
        result = _client.get_rainfall_risk(float(latitude), float(longitude), hours=6)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps(_weather_error(e))


# ── Entrypoint ──────────────────────────────────────────────────────────


def main():
    transport = sys.argv[1] if len(sys.argv) > 1 else "http"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8002
    if transport == "http":
        mcp.run(transport="http", port=port)
    else:
        mcp.run(transport=transport)


if __name__ == "__main__":
    main()
