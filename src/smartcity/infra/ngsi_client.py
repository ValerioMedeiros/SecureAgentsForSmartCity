import os
from typing import Any, Dict, Optional
from urllib.parse import quote

import requests

from .logging_utils import configure_logger

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
    return quote(entity_id, safe="")


def get_traffic_signal(
    entity_id: str, trace_id: str, token: Optional[str] = None
) -> Dict[str, Any]:
    url = f"{ORION_BASE_URL}/v2/entities/{_encode_entity_id(entity_id)}"
    response = requests.get(url, headers=_headers(token))
    logger.info(
        "Fetched TrafficSignal",
        extra={"traceId": trace_id, "extra_fields": {"status": response.status_code}},
    )
    response.raise_for_status()
    return response.json()


def upsert_traffic_signal(entity: Dict[str, Any], trace_id: str) -> None:
    url = f"{ORION_BASE_URL}/v2/entities?options=upsert,keyValues"
    response = requests.post(
        url, headers={**_headers(), "Content-Type": "application/json"}, json=entity
    )
    logger.info(
        "Upsert TrafficSignal",
        extra={"traceId": trace_id, "extra_fields": {"status": response.status_code}},
    )
    response.raise_for_status()


def update_priority_corridor(
    entity_id: str, value: str, trace_id: str, token: Optional[str] = None
) -> Dict[str, Any]:
    url = f"{ORION_BASE_URL}/v2/entities/{_encode_entity_id(entity_id)}/attrs/priorityCorridor"
    headers = _headers(token)
    headers["Content-Type"] = "application/json"
    payload = {"value": value}
    response = requests.put(url, headers=headers, json=payload)
    logger.info(
        "Updated priorityCorridor",
        extra={
            "traceId": trace_id,
            "extra_fields": {"status": response.status_code, "value": value},
        },
    )
    response.raise_for_status()
    return response.json() if response.content else {"result": "updated"}


def create_subscription(subscription: Dict[str, Any], trace_id: str) -> Dict[str, Any]:
    url = f"{ORION_BASE_URL}/v2/subscriptions"
    headers = _headers()
    headers["Content-Type"] = "application/json"
    response = requests.post(url, headers=headers, json=subscription, timeout=10)
    logger.info(
        "Created subscription",
        extra={"traceId": trace_id, "extra_fields": {"status": response.status_code}},
    )
    if response.status_code not in (201, 204):
        response.raise_for_status()
    return {
        "status": response.status_code,
        "location": response.headers.get("Location"),
    }


def list_subscriptions(trace_id: str) -> Dict[str, Any]:
    url = f"{ORION_BASE_URL}/v2/subscriptions"
    response = requests.get(url, headers=_headers(), timeout=10)
    logger.info(
        "Listed subscriptions",
        extra={"traceId": trace_id, "extra_fields": {"status": response.status_code}},
    )
    response.raise_for_status()
    return {"subscriptions": response.json()}
