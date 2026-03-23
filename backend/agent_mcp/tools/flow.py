"""MCP tools for retrieving the Studio flow layout (all entities with relationships)."""

from __future__ import annotations

import asyncio

from agent_mcp import backend_client


async def get_flow_layout() -> dict:
    """Get the complete Studio configuration as a flow layout.

    Fetches all connections, tools, resources, and prompts and organizes them
    into a unified view showing relationships between entities. Useful for
    understanding the current Studio configuration at a glance.

    Returns:
        Object with 'connections', 'tools', 'resources', 'prompts' arrays,
        each containing the full entity objects.
    """
    connections, tools, resources, prompts = await asyncio.gather(
        backend_client.get("/connections", params={"limit": 500}),
        backend_client.get("/tools", params={"limit": 500}),
        backend_client.get("/resources", params={"limit": 500}),
        backend_client.get("/prompts", params={"limit": 500}),
    )

    return {
        "connections": connections,
        "tools": tools,
        "resources": resources,
        "prompts": prompts,
    }
