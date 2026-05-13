import uuid

from ..infra.logging_utils import configure_logger
from ..infra.ngsi_client import upsert_traffic_signal

logger = configure_logger("host")


def main() -> None:
    trace_id = str(uuid.uuid4())
    entity = {
        "id": "TrafficSignal:001",
        "type": "TrafficSignal",
        "status": "normal",
        "priorityCorridor": "none",
        "location": "Avenue 1",
    }

    upsert_traffic_signal(entity, trace_id)
    logger.info(
        "Initial TrafficSignal created",
        extra={"traceId": trace_id, "extra_fields": entity},
    )


if __name__ == "__main__":
    main()
