import json
import logging
import os
import sys
from datetime import datetime
from typing import Any, Dict


def configure_logger(component: str) -> logging.Logger:
    logger = logging.getLogger(component)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    formatter = JsonFormatter(component)
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    log_file = os.getenv("JSON_LOG_FILE", "logs/traces.jsonl")
    if log_file:
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    logger.propagate = False
    return logger


class JsonFormatter(logging.Formatter):
    def __init__(self, component: str):
        super().__init__()
        self.component = component

    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        payload: Dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "component": self.component,
            "message": record.getMessage(),
        }
        if hasattr(record, "traceId"):
            payload["traceId"] = getattr(record, "traceId")
        if hasattr(record, "extra_fields"):
            payload.update(getattr(record, "extra_fields"))
        return json.dumps(payload)
