import asyncio
import uuid

from ..infra.fiware_mcp_client import upsert_entity
from ..infra.logging_utils import configure_logger

logger = configure_logger("init")

ENTITY = {
    "id": "TrafficSignal:001",
    "type": "TrafficSignal",
    "status": "normal",
    "priorityCorridor": "none",
    "location": "Avenue 1",
}


async def _init() -> None:
    trace_id = str(uuid.uuid4())
    result = await upsert_entity(ENTITY)
    logger.info(
        "TrafficSignal initialised",
        extra={"traceId": trace_id, "extra_fields": {"result": result, **ENTITY}},
    )


def main() -> None:
    asyncio.run(_init())


if __name__ == "__main__":
    main()
