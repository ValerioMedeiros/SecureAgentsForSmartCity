import datetime
import logging
import os
import urllib.parse
from typing import Any, Literal, Optional

import requests
from requests.exceptions import ConnectionError, HTTPError, RequestException, Timeout

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

ActionType = Literal["APPEND", "APPEND_STRICT", "UPDATE", "DELETE", "REPLACE"]


class OrionConnectionError(Exception):
    def __init__(self, message: str, details: dict = None):
        super().__init__(message)
        self.details = details or {}


class OrionEntityError(Exception):
    def __init__(self, message: str, details: dict = None):
        super().__init__(message)
        self.details = details or {}


class OrionClient:
    """Generic NGSI-v2 client for FIWARE Orion Context Broker."""

    def __init__(
        self,
        base_url: str = None,
        fiware_service: str = None,
        fiware_service_path: str = "/",
    ):
        self.base_url = (base_url or os.getenv("ORION_BASE_URL", "http://localhost:1026")).rstrip("/")
        self.fiware_service = fiware_service or os.getenv("ORION_FIWARE_SERVICE", "openiot")
        self.fiware_service_path = fiware_service_path or os.getenv("ORION_FIWARE_SERVICE_PATH", "/")

    def _headers(self, extra: dict = None) -> dict:
        headers = {
            "Fiware-Service": self.fiware_service,
            "Fiware-ServicePath": self.fiware_service_path,
        }
        if extra:
            headers.update(extra)
        return headers

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        try:
            response = requests.request(method, url, **kwargs)
            response.raise_for_status()
            logger.info("Request %s %s → %s", method, url, response.status_code)
            return response

        except ConnectionError as e:
            raise OrionConnectionError(f"Failed to connect to Orion at {url}", {"url": url}) from e

        except Timeout as e:
            raise OrionConnectionError(f"Request timed out: {url}", {"url": url}) from e

        except HTTPError as e:
            status = e.response.status_code if e.response else None
            body = e.response.text if e.response else ""
            logger.error("HTTP %s for %s %s: %s", status, method, url, body)
            if status == 404:
                raise OrionEntityError("Entity not found", {"url": url, "status": status}) from e
            if status == 409:
                raise OrionEntityError("Entity already exists", {"url": url, "status": status}) from e
            if status == 400:
                raise OrionEntityError(f"Bad request: {body}", {"url": url, "status": status}) from e
            raise OrionConnectionError(f"Orion returned {status}", {"url": url, "status": status}) from e

        except RequestException as e:
            raise OrionConnectionError(f"Unexpected error: {e}", {"url": url}) from e

    # --- Entity operations ---

    def get_entities(
        self,
        entity_type: str = None,
        id_pattern: str = None,
        limit: int = 20,
        offset: int = 0,
        query: str = None,
        attrs: str = None,
        count: bool = False,
        georel: str = None,
        geometry: str = None,
        coords: str = None,
    ) -> dict:
        """
        Query entities from Orion with optional geo-filter.

        Returns {"entities": [...], "total": N} where total is only present when count=True.

        Geo params follow NGSI-v2 spec:
          georel:   'near;maxDistance:300' or 'coveredBy'
          geometry: 'point', 'polygon', 'line'
          coords:   'lat,lon' (e.g. '-5.7945,-35.2094')
        """
        params = {"limit": limit, "offset": offset}
        if entity_type:
            params["type"] = entity_type
        if id_pattern:
            params["idPattern"] = id_pattern
        if query:
            params["q"] = query
        if attrs:
            params["attrs"] = attrs
        if count:
            params["options"] = "count"
        if georel:
            params["georel"] = georel
        if geometry:
            params["geometry"] = geometry
        if coords:
            params["coords"] = coords

        url = f"{self.base_url}/v2/entities?{urllib.parse.urlencode(params)}"
        response = self._request("GET", url, headers=self._headers())
        result: dict = {"entities": response.json()}
        if count:
            total = response.headers.get("Fiware-Total-Count")
            if total is not None:
                result["total"] = int(total)
        return result

    def get_entity(self, entity_id: str, entity_type: str = None, key_values: bool = False) -> dict:
        """Retrieve a specific entity."""
        if not entity_id:
            raise ValueError("entity_id cannot be empty")

        params = {}
        if entity_type:
            params["type"] = entity_type
        if key_values:
            params["options"] = "keyValues"

        url = f"{self.base_url}/v2/entities/{urllib.parse.quote(entity_id, safe='')}"
        if params:
            url += f"?{urllib.parse.urlencode(params)}"

        return self._request("GET", url, headers=self._headers()).json()

    def create_entity(self, entity_data: dict) -> requests.Response:
        """Create a new entity in Orion (NGSI-v2 format)."""
        if not entity_data:
            raise ValueError("entity_data cannot be empty")
        if "id" not in entity_data or "type" not in entity_data:
            raise ValueError("entity_data must contain 'id' and 'type'")

        url = f"{self.base_url}/v2/entities"
        return self._request("POST", url, json=entity_data, headers=self._headers({"Content-Type": "application/json"}))

    def upsert_entity(self, entity_data: dict) -> requests.Response:
        """Create or update an entity using keyValues upsert."""
        url = f"{self.base_url}/v2/entities?options=upsert,keyValues"
        return self._request("POST", url, json=entity_data, headers=self._headers({"Content-Type": "application/json"}))

    def update_entity(self, payload: dict, action_type: ActionType = "APPEND") -> requests.Response:
        """
        Update entity attributes via /op/update.

        action_type options:
          APPEND        — add or update attributes (default)
          APPEND_STRICT — add only, fail if attribute already exists
          UPDATE        — update only existing attributes
          DELETE        — remove listed attributes
          REPLACE       — replace all attributes with the provided ones
        """
        if not payload or "id" not in payload:
            raise ValueError("payload must contain 'id'")

        url = f"{self.base_url}/v2/op/update"
        body = {
            "actionType": action_type,
            "entities": [self._convert_datetimes(payload)],
        }
        return self._request("POST", url, json=body, headers=self._headers({"Content-Type": "application/json"}))

    def update_attribute(self, entity_id: str, attr_name: str, value: Any) -> requests.Response:
        """Update a single attribute value."""
        url = f"{self.base_url}/v2/entities/{urllib.parse.quote(entity_id, safe='')}/attrs/{attr_name}"
        return self._request(
            "PUT", url,
            json={"value": value},
            headers=self._headers({"Content-Type": "application/json"}),
        )

    def delete_entity(self, entity_id: str, entity_type: str = None) -> requests.Response:
        """Delete an entity from Orion."""
        if not entity_id:
            raise ValueError("entity_id cannot be empty")

        url = f"{self.base_url}/v2/entities/{urllib.parse.quote(entity_id, safe='')}"
        if entity_type:
            url += f"?type={entity_type}"

        return self._request("DELETE", url, headers=self._headers())

    # --- Subscription operations ---

    def list_subscriptions(self, limit: int = 20, offset: int = 0) -> dict:
        """List active subscriptions with pagination."""
        params = {"limit": limit, "offset": offset, "options": "count"}
        url = f"{self.base_url}/v2/subscriptions?{urllib.parse.urlencode(params)}"
        response = self._request("GET", url, headers=self._headers())
        result: dict = {"subscriptions": response.json()}
        total = response.headers.get("Fiware-Total-Count")
        if total is not None:
            result["total"] = int(total)
        return result

    def delete_subscription(self, subscription_id: str) -> None:
        """Delete a subscription by ID."""
        if not subscription_id:
            raise ValueError("subscription_id cannot be empty")
        url = f"{self.base_url}/v2/subscriptions/{subscription_id}"
        self._request("DELETE", url, headers=self._headers())

    def subscription_exists(self, description: str, notification_url: str, entities: list) -> bool:
        """
        Check if a matching subscription already exists.
        Iterates all pages to avoid false negatives on large subscription sets.
        """
        offset = 0
        page_size = 100
        while True:
            result = self.list_subscriptions(limit=page_size, offset=offset)
            for sub in result["subscriptions"]:
                if (
                    sub.get("description") == description
                    and sub.get("notification", {}).get("http", {}).get("url") == notification_url
                    and sub.get("subject", {}).get("entities") == entities
                ):
                    return True
            total = result.get("total", len(result["subscriptions"]) + offset)
            offset += page_size
            if offset >= total:
                return False

    def create_subscription(self, subscription_data: dict, extra_headers: dict = None) -> Optional[requests.Response]:
        """Create a subscription, skipping if one with the same description/URL/entities exists."""
        if not subscription_data:
            raise ValueError("subscription_data cannot be empty")

        description = subscription_data.get("description", "")
        notification_url = subscription_data.get("notification", {}).get("http", {}).get("url", "")
        entities = subscription_data.get("subject", {}).get("entities", [])

        if self.subscription_exists(description, notification_url, entities):
            logger.info("Subscription '%s' already exists, skipping.", description)
            return None

        url = f"{self.base_url}/v2/subscriptions"
        headers = self._headers({"Content-Type": "application/json"})
        if extra_headers:
            headers.update(extra_headers)

        return self._request("POST", url, json=subscription_data, headers=headers)

    def get_version(self) -> dict:
        """Return Orion version info."""
        return self._request("GET", f"{self.base_url}/version", headers=self._headers()).json()

    # --- Helpers ---

    def build_attr(self, value: Any, attr_type: str) -> dict:
        """Build an NGSI-v2 attribute dict."""
        if value is None:
            return {"type": attr_type, "value": None}
        if attr_type == "geo:json":
            if not isinstance(value, (list, tuple)) or len(value) != 2:
                raise ValueError("geo:json requires [longitude, latitude]")
            return {"type": "geo:json", "value": {"type": "Point", "coordinates": list(value)}}
        return {"type": attr_type, "value": value}

    def _convert_datetimes(self, obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: self._convert_datetimes(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._convert_datetimes(i) for i in obj]
        if isinstance(obj, datetime.datetime):
            return self._normalize_datetime(obj)
        return obj

    def _normalize_datetime(self, dt: datetime.datetime) -> str:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
