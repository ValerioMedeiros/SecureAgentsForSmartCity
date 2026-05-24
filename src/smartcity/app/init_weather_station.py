"""
Initialise WeatherStation entities and register Orion subscriptions.

Creates one or more WeatherStation entities in Orion and subscribes to
threshold-based conditions that trigger the monitor webhook.

Usage:
    python -m smartcity.app.init_weather_station

Environment variables:
    MONITOR_CALLBACK_URL  Webhook that receives Orion notifications (default: http://localhost:8010/monitor/notify)
    ORION_FIWARE_SERVICE  Fiware-Service header (default: openiot)
"""

import asyncio
import os
import uuid

from ..infra.fiware_mcp_client import create_subscription, upsert_entity
from ..infra.logging_utils import configure_logger

logger = configure_logger("init")

MONITOR_CALLBACK_URL = os.getenv(
    "MONITOR_CALLBACK_URL", "http://localhost:8010/monitor/notify"
)

# Initial state — all readings at safe baseline values
WEATHER_STATIONS = [
    {
        "id": "WeatherStation:001",
        "type": "WeatherObserved",
        "precipitation": 0.0,       # mm — alert threshold: > 0.50
        "humidity": 60.0,           # %  — alert threshold: > 80
        "atmosphericPressure": 1013.0,  # hPa — alert threshold: < 1005
        "location": "Avenue 1",
    },
]

# Subscriptions: one per threshold condition, all pointing to the monitor webhook
SUBSCRIPTIONS = [
    {
        "description": "WeatherStation - Precipitation above 0.50mm",
        "subject": {
            "entities": [{"idPattern": "WeatherStation:.*", "type": "WeatherObserved"}],
            "condition": {
                "attrs": ["precipitation"],
                "expression": {"q": "precipitation>0.50"},
            },
        },
        "notification": {
            "http": {"url": MONITOR_CALLBACK_URL},
            "attrs": ["precipitation", "humidity", "atmosphericPressure", "location"],
        },
        "throttling": 5,
    },
    {
        "description": "WeatherStation - Humidity above 80%",
        "subject": {
            "entities": [{"idPattern": "WeatherStation:.*", "type": "WeatherObserved"}],
            "condition": {
                "attrs": ["humidity"],
                "expression": {"q": "humidity>80"},
            },
        },
        "notification": {
            "http": {"url": MONITOR_CALLBACK_URL},
            "attrs": ["precipitation", "humidity", "atmosphericPressure", "location"],
        },
        "throttling": 5,
    },
    {
        "description": "WeatherStation - Pressure below 1005hPa",
        "subject": {
            "entities": [{"idPattern": "WeatherStation:.*", "type": "WeatherObserved"}],
            "condition": {
                "attrs": ["atmosphericPressure"],
                "expression": {"q": "atmosphericPressure<1005"},
            },
        },
        "notification": {
            "http": {"url": MONITOR_CALLBACK_URL},
            "attrs": ["precipitation", "humidity", "atmosphericPressure", "location"],
        },
        "throttling": 5,
    },
]


async def _init() -> None:
    trace_id = str(uuid.uuid4())

    # Create entities
    for station in WEATHER_STATIONS:
        result = await upsert_entity(station)
        logger.info(
            "WeatherStation initialised",
            extra={"traceId": trace_id, "extra_fields": {"id": station["id"], "result": result}},
        )

    # Register subscriptions
    for sub in SUBSCRIPTIONS:
        result = await create_subscription(sub)
        logger.info(
            "Subscription registered",
            extra={"traceId": trace_id, "extra_fields": {"description": sub["description"], "result": result}},
        )


def main() -> None:
    asyncio.run(_init())


if __name__ == "__main__":
    main()
