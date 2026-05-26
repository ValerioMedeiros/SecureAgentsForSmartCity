"""
FIWARE Orion MCP Server — generic NGSI-v2 tools for LLM agents.

Transports:
  stdio (default):  uv run fiware-mcp-server
  HTTP:             uv run fiware-mcp-server --transport http --port 8001
"""

import json
import logging
import os
import sys
from typing import Optional

from fastmcp import FastMCP

from .orion_client import OrionClient, OrionConnectionError, OrionEntityError

logger = logging.getLogger("fiware_mcp")

mcp = FastMCP(
    name="FIWARE Orion",
    instructions=(
        "Tools for reading and writing context data in a FIWARE Orion Context Broker via NGSI-v2. "
        "Use get_entities to query, get_entity for a single entity, upsert_entity to create/update, "
        "update_attribute for a single field, and create_subscription for event-driven flows. "
        "Use list_subscriptions and delete_subscription to manage active subscriptions."
    ),
)

_client = OrionClient(
    base_url=os.getenv("ORION_BASE_URL", "http://localhost:1026"),
    fiware_service=os.getenv("ORION_FIWARE_SERVICE", "openiot"),
    fiware_service_path=os.getenv("ORION_FIWARE_SERVICE_PATH", "/"),
)


def _orion_error(e: Exception) -> str:
    """Convert Orion exceptions to readable strings for the LLM."""
    if isinstance(e, OrionEntityError):
        return f"Entity error: {e}"
    if isinstance(e, OrionConnectionError):
        return f"Connection error: {e}"
    return f"Unexpected error: {e}"


# ── Health ────────────────────────────────────────────────────────────

@mcp.tool()
def get_orion_version() -> dict:
    """Return FIWARE Orion version and confirm the broker is reachable."""
    try:
        return _client.get_version()
    except Exception as e:
        return {"error": _orion_error(e)}


# ── Read ──────────────────────────────────────────────────────────────

@mcp.tool()
def get_entities(
    entity_type: Optional[str] = None,
    id_pattern: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    query: Optional[str] = None,
    attrs: Optional[str] = None,
    count: bool = False,
    georel: Optional[str] = None,
    geometry: Optional[str] = None,
    coords: Optional[str] = None,
) -> dict:
    """
    Query entities from Orion, with optional geo-filter support.

    Returns {"entities": [...]} or {"entities": [...], "total": N} when count=True.

    Args:
        entity_type: NGSI entity type (e.g. 'WeatherObserved', 'TrafficSignal').
        id_pattern: Regex to match entity IDs (e.g. 'WeatherStation:.*').
        limit: Max results per page (default 20, max 1000).
        offset: Number of results to skip for pagination (default 0).
        query: NGSI simple query string (e.g. 'precipitation>50', 'status==off').
        attrs: Comma-separated attributes to return (e.g. 'precipitation,humidity').
        count: If True, include total count in response header (Fiware-Total-Count).
        georel: Geo relation (e.g. 'near;maxDistance:300', 'coveredBy').
        geometry: Geometry type ('point', 'polygon', 'line').
        coords: Coordinates in 'lat,lon' order (e.g. '-5.7945,-35.2094').
    """
    try:
        return _client.get_entities(
            entity_type=entity_type,
            id_pattern=id_pattern,
            limit=limit,
            offset=offset,
            query=query,
            attrs=attrs,
            count=count,
            georel=georel,
            geometry=geometry,
            coords=coords,
        )
    except Exception as e:
        return {"error": _orion_error(e)}


@mcp.tool()
def get_entity(entity_id: str, entity_type: Optional[str] = None, key_values: bool = True) -> dict:
    """
    Retrieve a single entity by ID.

    Args:
        entity_id: Full entity ID (e.g. 'WeatherStation:001').
        entity_type: Optional entity type for disambiguation.
        key_values: Return simplified key-value format instead of full NGSI (default True).
    """
    try:
        return _client.get_entity(entity_id, entity_type=entity_type, key_values=key_values)
    except Exception as e:
        return {"error": _orion_error(e)}


# ── Write ─────────────────────────────────────────────────────────────

@mcp.tool()
def create_entity(entity_json: str) -> str:
    """
    Create a new entity in Orion (NGSI-v2 format). Fails if entity already exists — use upsert_entity for idempotent creation.

    Args:
        entity_json: JSON string with the entity. Must include 'id' and 'type'.
            Example: '{"id": "WeatherStation:001", "type": "WeatherObserved",
                       "precipitation": {"type": "Number", "value": 0.0}}'
    """
    try:
        data = json.loads(entity_json)
        _client.create_entity(data)
        return f"Entity '{data['id']}' created."
    except Exception as e:
        return f"Error: {_orion_error(e)}"


@mcp.tool()
def upsert_entity(entity_json: str) -> str:
    """
    Create or update an entity using keyValues upsert. Idempotent — safe to call repeatedly.

    Args:
        entity_json: JSON string with flat key-value entity data. Must include 'id' and 'type'.
            Example: '{"id": "WeatherStation:001", "type": "WeatherObserved", "precipitation": 12.5}'
    """
    try:
        data = json.loads(entity_json)
        _client.upsert_entity(data)
        return f"Entity '{data['id']}' upserted."
    except Exception as e:
        return f"Error: {_orion_error(e)}"


@mcp.tool()
def update_attribute(entity_id: str, attribute: str, value: str) -> str:
    """
    Update a single attribute on an existing entity. Numbers and booleans are auto-cast.

    Args:
        entity_id: Full entity ID (e.g. 'TrafficSignal:001').
        attribute: Attribute name (e.g. 'priorityCorridor').
        value: New value as string. Examples: '42', '3.14', 'true', 'emergency'.
    """
    try:
        cast_value: object = value
        if value.lower() == "true":
            cast_value = True
        elif value.lower() == "false":
            cast_value = False
        else:
            try:
                cast_value = float(value) if "." in value else int(value)
            except ValueError:
                pass

        _client.update_attribute(entity_id, attribute, cast_value)
        return f"Attribute '{attribute}' on '{entity_id}' updated to '{value}'."
    except Exception as e:
        return f"Error: {_orion_error(e)}"


@mcp.tool()
def delete_entity(entity_id: str, entity_type: Optional[str] = None) -> str:
    """
    Delete an entity from Orion.

    Args:
        entity_id: Full entity ID.
        entity_type: Optional type for disambiguation.
    """
    try:
        _client.delete_entity(entity_id, entity_type=entity_type)
        return f"Entity '{entity_id}' deleted."
    except Exception as e:
        return f"Error: {_orion_error(e)}"


# ── Subscriptions ─────────────────────────────────────────────────────

@mcp.tool()
def list_subscriptions(limit: int = 20, offset: int = 0) -> dict:
    """
    List active Orion subscriptions.

    Returns {"subscriptions": [...], "total": N}.

    Args:
        limit: Max results per page (default 20).
        offset: Number of results to skip for pagination (default 0).
    """
    try:
        return _client.list_subscriptions(limit=limit, offset=offset)
    except Exception as e:
        return {"error": _orion_error(e)}


@mcp.tool()
def create_subscription(subscription_json: str) -> str:
    """
    Register an Orion subscription that POSTs to a webhook when conditions are met.
    Skips creation if a subscription with the same description, URL and entities already exists.

    Args:
        subscription_json: Full NGSI-v2 subscription payload as JSON string.
    """
    try:
        data = json.loads(subscription_json)
        response = _client.create_subscription(data)
        if response is None:
            return "Subscription already exists, skipped."
        return f"Subscription created (HTTP {response.status_code})."
    except Exception as e:
        return f"Error: {_orion_error(e)}"


@mcp.tool()
def delete_subscription(subscription_id: str) -> str:
    """
    Delete an active subscription by its ID.

    Args:
        subscription_id: Subscription ID returned by Orion (e.g. '6473f1a2d4a315e87b2c9df1').
                         Use list_subscriptions to find active subscription IDs.
    """
    try:
        _client.delete_subscription(subscription_id)
        return f"Subscription '{subscription_id}' deleted."
    except Exception as e:
        return f"Error: {_orion_error(e)}"


# ── Resources (read-only context) ─────────────────────────────────────

@mcp.resource("orion://entities/{entity_id}")
def entity_resource(entity_id: str) -> str:
    """Estado atual de uma entidade Orion como contexto somente leitura."""
    try:
        result = _client.get_entity(entity_id, key_values=True)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": _orion_error(e)})


@mcp.resource("orion://entities/type/{entity_type}")
def entities_by_type_resource(entity_type: str) -> str:
    """Lista de entidades de um tipo específico."""
    try:
        result = _client.get_entities(entity_type=entity_type, limit=50)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": _orion_error(e)})


@mcp.resource("orion://status")
def orion_status_resource() -> str:
    """Versão e saúde do Orion Context Broker."""
    try:
        result = _client.get_version()
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": _orion_error(e)})


# ── Entrypoint ────────────────────────────────────────────────────────

def main():
    transport = sys.argv[1] if len(sys.argv) > 1 else "http"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8001
    if transport == "http":
        mcp.run(transport="http", port=port)
    else:
        mcp.run(transport=transport)


if __name__ == "__main__":
    main()
