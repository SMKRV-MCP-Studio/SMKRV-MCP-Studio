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


async def import_config(
    data: dict,
    mode: str = "merge",
) -> dict:
    """Import Studio configuration from a JSON export.

    Args:
        data: The full export JSON object (same structure as export_config output).
        mode: Import mode — 'merge' adds/updates without deleting existing,
              'replace' removes existing entities first.

    Returns:
        Object with import summary: created, updated, skipped counts per entity type.
    """
    # Backend expects ExportPayload directly (not wrapped in {data, mode})
    return await backend_client.post("/export-import/import", data=data)
