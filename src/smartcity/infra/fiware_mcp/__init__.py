"""
FIWARE Orion MCP — embedded module.

Exposes the OrionClient for direct use and the FastMCP server
that can be started as a subprocess or via the CLI entry point.

Usage (HTTP transport):
    uv run fiware-mcp-server
"""

from .orion_client import OrionClient, OrionConnectionError, OrionEntityError

__all__ = ["OrionClient", "OrionConnectionError", "OrionEntityError"]
