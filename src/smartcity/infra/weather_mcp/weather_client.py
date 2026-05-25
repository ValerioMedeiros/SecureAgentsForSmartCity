"""
Cliente HTTP para a API Open-Meteo.

Open-Meteo é gratuita, sem necessidade de chave de API.
Documentação: https://open-meteo.com/en/docs

Parâmetros climáticos relevantes para smart cities brasileiras:
  - precipitation / precipitation_probability: chuva e risco de alagamento
  - temperature_2m: temperatura do ar
  - relative_humidity_2m: umidade relativa
  - wind_speed_10m / wind_gusts_10m: velocidade e rajadas de vento
  - weather_code: código WMO de condição climática (0=céu limpo, 95/99=tempestade)
"""

import logging
from typing import Any

import requests
from requests.exceptions import ConnectionError, RequestException, Timeout

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

_BASE_URL = "https://api.open-meteo.com/v1/forecast"
_TIMEOUT = 10


class WeatherConnectionError(Exception):
    pass


class WeatherClient:
    """Thin wrapper around the Open-Meteo REST API."""

    # WMO weather code descriptions (subset most relevant for smart cities)
    _WMO_CODES: dict[int, str] = {
        0: "Céu limpo",
        1: "Principalmente limpo",
        2: "Parcialmente nublado",
        3: "Nublado",
        45: "Nevoeiro",
        48: "Nevoeiro com geada",
        51: "Garoa leve",
        53: "Garoa moderada",
        55: "Garoa densa",
        61: "Chuva leve",
        63: "Chuva moderada",
        65: "Chuva forte",
        71: "Neve leve",
        73: "Neve moderada",
        75: "Neve intensa",
        77: "Granizo",
        80: "Pancadas de chuva leves",
        81: "Pancadas de chuva moderadas",
        82: "Pancadas de chuva violentas",
        85: "Pancadas de neve leves",
        86: "Pancadas de neve intensas",
        95: "Tempestade",
        96: "Tempestade com granizo leve",
        99: "Tempestade com granizo intenso",
    }

    def _get(self, params: dict[str, Any]) -> dict:
        try:
            response = requests.get(_BASE_URL, params=params, timeout=_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except ConnectionError as e:
            raise WeatherConnectionError(f"Falha ao conectar com Open-Meteo: {e}") from e
        except Timeout as e:
            raise WeatherConnectionError("Requisição para Open-Meteo expirou (timeout).") from e
        except RequestException as e:
            raise WeatherConnectionError(f"Erro inesperado ao consultar Open-Meteo: {e}") from e

    def describe_wmo(self, code: int) -> str:
        return self._WMO_CODES.get(code, f"Condição desconhecida (código WMO {code})")

    def get_current(self, latitude: float, longitude: float, timezone: str = "America/Sao_Paulo") -> dict:
        """
        Retorna as condições climáticas atuais para a localização informada.

        Campos retornados: temperatura, umidade, precipitação, velocidade do vento,
        rajadas, código WMO e horário da leitura.
        """
        data = self._get({
            "latitude": latitude,
            "longitude": longitude,
            "current": ",".join([
                "temperature_2m",
                "relative_humidity_2m",
                "precipitation",
                "wind_speed_10m",
                "wind_gusts_10m",
                "weather_code",
            ]),
            "timezone": timezone,
        })
        current = data.get("current", {})
        code = current.get("weather_code", -1)
        return {
            "latitude": latitude,
            "longitude": longitude,
            "timezone": timezone,
            "time": current.get("time"),
            "temperature_c": current.get("temperature_2m"),
            "humidity_pct": current.get("relative_humidity_2m"),
            "precipitation_mm": current.get("precipitation"),
            "wind_speed_kmh": current.get("wind_speed_10m"),
            "wind_gusts_kmh": current.get("wind_gusts_10m"),
            "weather_code": code,
            "weather_description": self.describe_wmo(code),
        }

    def get_hourly_forecast(
        self,
        latitude: float,
        longitude: float,
        hours: int = 24,
        timezone: str = "America/Sao_Paulo",
    ) -> dict:
        """
        Retorna previsão horária para as próximas `hours` horas (máximo 384h / 16 dias).

        Inclui probabilidade e volume de precipitação, temperatura, umidade,
        vento e código WMO por hora.
        """
        hours = max(1, min(hours, 384))
        forecast_days = max(1, (hours + 23) // 24)

        data = self._get({
            "latitude": latitude,
            "longitude": longitude,
            "hourly": ",".join([
                "temperature_2m",
                "relative_humidity_2m",
                "precipitation_probability",
                "precipitation",
                "wind_speed_10m",
                "wind_gusts_10m",
                "weather_code",
            ]),
            "timezone": timezone,
            "forecast_days": forecast_days,
        })

        hourly = data.get("hourly", {})
        times = hourly.get("time", [])[:hours]
        codes = hourly.get("weather_code", [])[:hours]

        return {
            "latitude": latitude,
            "longitude": longitude,
            "timezone": timezone,
            "hours_requested": hours,
            "forecast": [
                {
                    "time": times[i],
                    "temperature_c": hourly.get("temperature_2m", [])[i] if i < len(hourly.get("temperature_2m", [])) else None,
                    "humidity_pct": hourly.get("relative_humidity_2m", [])[i] if i < len(hourly.get("relative_humidity_2m", [])) else None,
                    "precipitation_probability_pct": hourly.get("precipitation_probability", [])[i] if i < len(hourly.get("precipitation_probability", [])) else None,
                    "precipitation_mm": hourly.get("precipitation", [])[i] if i < len(hourly.get("precipitation", [])) else None,
                    "wind_speed_kmh": hourly.get("wind_speed_10m", [])[i] if i < len(hourly.get("wind_speed_10m", [])) else None,
                    "wind_gusts_kmh": hourly.get("wind_gusts_10m", [])[i] if i < len(hourly.get("wind_gusts_10m", [])) else None,
                    "weather_code": codes[i] if i < len(codes) else None,
                    "weather_description": self.describe_wmo(codes[i]) if i < len(codes) else None,
                }
                for i in range(len(times))
            ],
        }

    def get_daily_forecast(
        self,
        latitude: float,
        longitude: float,
        days: int = 7,
        timezone: str = "America/Sao_Paulo",
    ) -> dict:
        """
        Retorna previsão diária para os próximos `days` dias (máximo 16 dias).

        Inclui precipitação acumulada, probabilidade máxima de chuva,
        temperaturas mínima/máxima, vento máximo e código WMO dominante.
        """
        days = max(1, min(days, 16))

        data = self._get({
            "latitude": latitude,
            "longitude": longitude,
            "daily": ",".join([
                "weather_code",
                "temperature_2m_max",
                "temperature_2m_min",
                "precipitation_sum",
                "precipitation_probability_max",
                "wind_speed_10m_max",
                "wind_gusts_10m_max",
            ]),
            "timezone": timezone,
            "forecast_days": days,
        })

        daily = data.get("daily", {})
        times = daily.get("time", [])
        codes = daily.get("weather_code", [])

        return {
            "latitude": latitude,
            "longitude": longitude,
            "timezone": timezone,
            "days_requested": days,
            "forecast": [
                {
                    "date": times[i],
                    "temperature_max_c": daily.get("temperature_2m_max", [])[i] if i < len(daily.get("temperature_2m_max", [])) else None,
                    "temperature_min_c": daily.get("temperature_2m_min", [])[i] if i < len(daily.get("temperature_2m_min", [])) else None,
                    "precipitation_sum_mm": daily.get("precipitation_sum", [])[i] if i < len(daily.get("precipitation_sum", [])) else None,
                    "precipitation_probability_max_pct": daily.get("precipitation_probability_max", [])[i] if i < len(daily.get("precipitation_probability_max", [])) else None,
                    "wind_speed_max_kmh": daily.get("wind_speed_10m_max", [])[i] if i < len(daily.get("wind_speed_10m_max", [])) else None,
                    "wind_gusts_max_kmh": daily.get("wind_gusts_10m_max", [])[i] if i < len(daily.get("wind_gusts_10m_max", [])) else None,
                    "weather_code": codes[i] if i < len(codes) else None,
                    "weather_description": self.describe_wmo(codes[i]) if i < len(codes) else None,
                }
                for i in range(len(times))
            ],
        }

    def get_rainfall_risk(
        self,
        latitude: float,
        longitude: float,
        hours: int = 6,
        timezone: str = "America/Sao_Paulo",
    ) -> dict:
        """
        Retorna análise de risco de chuva/alagamento para as próximas `hours` horas.

        Consolida precipitação acumulada prevista e probabilidade máxima de chuva
        no período, com classificação de risco (baixo/médio/alto/crítico).
        """
        hours = max(1, min(hours, 48))
        forecast = self.get_hourly_forecast(latitude, longitude, hours=hours, timezone=timezone)
        entries = forecast["forecast"]

        total_precip = sum(e["precipitation_mm"] or 0.0 for e in entries)
        max_prob = max((e["precipitation_probability_pct"] or 0 for e in entries), default=0)
        max_hourly = max((e["precipitation_mm"] or 0.0 for e in entries), default=0.0)

        # Thresholds based on Brazilian civil defense (CEMADEN) references
        if total_precip >= 50 or max_hourly >= 20:
            risk = "crítico"
        elif total_precip >= 20 or max_hourly >= 10:
            risk = "alto"
        elif total_precip >= 5 or max_prob >= 70:
            risk = "médio"
        else:
            risk = "baixo"

        return {
            "latitude": latitude,
            "longitude": longitude,
            "timezone": timezone,
            "hours_analyzed": hours,
            "total_precipitation_mm": round(total_precip, 2),
            "max_hourly_precipitation_mm": round(max_hourly, 2),
            "max_precipitation_probability_pct": max_prob,
            "flood_risk": risk,
            "hourly_breakdown": [
                {
                    "time": e["time"],
                    "precipitation_mm": e["precipitation_mm"],
                    "precipitation_probability_pct": e["precipitation_probability_pct"],
                }
                for e in entries
            ],
        }
