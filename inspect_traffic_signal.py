import os
import uuid

from logging_utils import configure_logger
from ngsi_client import get_traffic_signal

logger = configure_logger("host")


if __name__ == "__main__":
    trace_id = str(uuid.uuid4())
    entity_id = os.getenv("TRAFFIC_SIGNAL_ID", "TrafficSignal:001")
    result = get_traffic_signal(entity_id, trace_id)
    logger.info("TrafficSignal state", extra={"traceId": trace_id, "extra_fields": result})
    print(result)
