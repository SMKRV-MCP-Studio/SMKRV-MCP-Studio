"""MCP tools for exporting and importing Studio configuration."""

from __future__ import annotations

from agent_mcp import backend_client


async def export_config() -> dict:
    """Export the complete Studio configuration as JSON.

    Exports all connections, tools, resources, and prompts.
    Sensitive fields (passwords, tokens) are included in encrypted form.

    Returns:
        JSON object with format version, metadata, and all entity arrays.
    """
    return await backend_client.get("/export-import/export")


async def import_config(data: dict, dry_run: bool = False) -> dict:
    """Import configuration (tools, resources, prompts, connections, server config).

    Always merges with existing configuration (skips duplicates by name).

    Args:
        data: Export payload dict with version, connections, tools, resources,
              prompts, server_config keys.
        dry_run: If True, validate and preview without applying changes.

    Returns:
        dict with import summary: counts of created/skipped entities.
    """
    path = "/export-import/import"
    if dry_run:
        path += "?dry_run=true"
    return await backend_client.post(path, data=data)
