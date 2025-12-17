import os
from typing import Any, Dict, Optional
from urllib.parse import quote

import requests

from logging_utils import configure_logger

ORION_BASE_URL = os.getenv("ORION_BASE_URL", "http://localhost:1026")
SERVICE = os.getenv("ORION_FIWARE_SERVICE", "openiot")
SERVICE_PATH = os.getenv("ORION_FIWARE_SERVICE_PATH", "/")

logger = configure_logger("ngsi_client")


def _headers(token: Optional[str] = None) -> Dict[str, str]:
    headers = {
        "Fiware-Service": SERVICE,
        "Fiware-ServicePath": SERVICE_PATH,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _encode_entity_id(entity_id: str) -> str:
    # Encode reserved characters (colon, etc.) so Orion accepts the path segment.
    return quote(entity_id, safe="")


def get_traffic_signal(entity_id: str, trace_id: str, token: Optional[str] = None) -> Dict[str, Any]:
    url = f"{ORION_BASE_URL}/v2/entities/{_encode_entity_id(entity_id)}"
    response = requests.get(url, headers=_headers(token))
    logger.info("Fetched TrafficSignal", extra={"traceId": trace_id, "extra_fields": {"status": response.status_code}})
    response.raise_for_status()
    return response.json()


# def upsert_traffic_signal(entity: Dict[str, Any], trace_id: str) -> None:
#     url = f"{ORION_BASE_URL}/v2/entities"
#     response = requests.post(url, headers=_headers(), json=entity)
#     logger.info("Upsert TrafficSignal", extra={"traceId": trace_id, "extra_fields": {"status": response.status_code}})
#     if response.status_code not in (200, 201, 204):
#         response.raise_for_status()

# ngsi_client.py
def upsert_traffic_signal(entity: Dict[str, Any], trace_id: str) -> None:
    # Use upsert + keyValues to avoid type boilerplate and allow re-runs.
    url = f"{ORION_BASE_URL}/v2/entities?options=upsert,keyValues"
    response = requests.post(url, headers={**_headers(), "Content-Type": "application/json"}, json=entity)
    logger.info(
        "Upsert TrafficSignal",
        extra={"traceId": trace_id, "extra_fields": {"status": response.status_code}},
    )
    response.raise_for_status()



def update_priority_corridor(entity_id: str, value: str, trace_id: str, token: Optional[str] = None) -> Dict[str, Any]:
    url = f"{ORION_BASE_URL}/v2/entities/{_encode_entity_id(entity_id)}/attrs/priorityCorridor"
    headers = _headers(token)
    headers["Content-Type"] = "application/json" 
    payload = {"value": value}
    response = requests.put(url, headers=headers, json=payload)
    logger.info(
        "Updated priorityCorridor",
        extra={"traceId": trace_id, "extra_fields": {"status": response.status_code, "value": value}},
    )
    response.raise_for_status()
    return response.json() if response.content else {"result": "updated"}