"""
Initialise PumpDevice entities in Orion (digital twins).

Creates pump entities with geo:json location so the monitor can resolve
the nearest available pump via Orion geo-queries — no explicit link to
any WeatherStation is needed.

  Pump MCP Server  →  controls the physical device (actuator layer)
  Orion entity     →  reflects the device state   (context broker layer)

Usage:
    python -m smartcity.app.init_pumps

Environment variables:
    ORION_FIWARE_SERVICE  Fiware-Service header (default: openiot)
"""

import asyncio
import uuid

from ..infra.fiware_mcp_client import upsert_entity
from ..infra.logging_utils import configure_logger

logger = configure_logger("init")

# Coordinates: GeoJSON uses [longitude, latitude]
# Each pump is placed near its corresponding WeatherStation
PUMP_DEVICES = [
    {
        "id": "PumpDevice:001",
        "type": "PumpDevice",
        "status": {"type": "Text", "value": "off"},
        "flow_rate_m3h": {"type": "Number", "value": 0.0},
        "max_flow_rate_m3h": {"type": "Number", "value": 120.0},
        "location": {
            "type": "Point",
            "coordinates": [-35.2100, -5.7950],  # ~90m from WeatherStation:001
        },
    },
    {
        "id": "PumpDevice:002",
        "type": "PumpDevice",
        "status": {"type": "Text", "value": "off"},
        "flow_rate_m3h": {"type": "Number", "value": 0.0},
        "max_flow_rate_m3h": {"type": "Number", "value": 80.0},
        "location": {
            "type": "Point",
            "coordinates": [-35.2185, -5.8025],  # ~80m from WeatherStation:002
        },
    },
]


async def _init() -> None:
    trace_id = str(uuid.uuid4())

    for pump in PUMP_DEVICES:
        orion_entity = await upsert_entity(pump)

        logger.info(
            "PumpDevice initialised",
            extra={
                "traceId": trace_id,
                "extra_fields": {
                    "id": pump["id"],
                    "coordinates": pump["location"]["coordinates"],
                    "result": orion_entity,
                },
            },
        )


def main() -> None:
    asyncio.run(_init())


if __name__ == "__main__":
    main()
