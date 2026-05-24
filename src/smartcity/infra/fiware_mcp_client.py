"""
Async client for the FIWARE Orion MCP Server.

Wraps fastmcp.Client to provide typed helpers used by mcp_server.py.
The FIWARE MCP server must be running before calls are made — set
FIWARE_MCP_URL to its HTTP endpoint (default: http://localhost:8001/mcp).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from fastmcp import Client

logger = logging.getLogger(__name__)

FIWARE_MCP_URL = os.getenv("FIWARE_MCP_URL", "http://localhost:8001/mcp")


async def _call(tool_name: str, params: dict) -> Any:
    """Call a FIWARE MCP tool and return the parsed result."""
    async with Client(FIWARE_MCP_URL) as client:
        result = await client.call_tool(tool_name, params)

    # fastmcp 3.x returns a CallToolResult with a .content list
    items = result.content if hasattr(result, "content") else result
    if not items:
        return None

    first = items[0]
    text = first.text if hasattr(first, "text") else str(first)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return text


async def get_entity(entity_id: str, key_values: bool = True) -> dict:
    """Retrieve a single entity by ID."""
    return await _call("get_entity", {"entity_id": entity_id, "key_values": key_values})


async def update_attribute(entity_id: str, attribute: str, value: str) -> Any:
    """Update a single attribute on an entity."""
    return await _call("update_attribute", {
        "entity_id": entity_id,
        "attribute": attribute,
        "value": value,
    })


async def get_entities(
    entity_type: str = None,
    query: str = None,
    attrs: str = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    """Query entities from Orion."""
    params: dict = {"limit": limit, "offset": offset}
    if entity_type:
        params["entity_type"] = entity_type
    if query:
        params["query"] = query
    if attrs:
        params["attrs"] = attrs
    return await _call("get_entities", params)


async def upsert_entity(entity_data: dict) -> Any:
    """Create or update an entity (idempotent)."""
    return await _call("upsert_entity", {"entity_json": json.dumps(entity_data)})


async def create_subscription(subscription_data: dict) -> Any:
    """Register an Orion subscription."""
    return await _call("create_subscription", {
        "subscription_json": json.dumps(subscription_data)
    })
