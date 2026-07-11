"""MCP tools for viewing change history and performing rollbacks."""

from __future__ import annotations

from agent_mcp import backend_client


async def list_history(
    skip: int = 0,
    limit: int = 50,
    entity_type: str | None = None,
    entity_id: str | None = None,
) -> dict:
    """List change history entries (audit trail).

    Args:
        skip: Pagination offset.
        limit: Maximum items to return (1-100).
        entity_type: Filter by entity type ('tool', 'resource', 'prompt', 'connection').
        entity_id: Filter by specific entity UUID.

    Returns:
        Object with 'items' array (each has action, entity_type, entity_id, snapshot, timestamp)
        and 'total' count.
    """
    params: dict = {"skip": skip, "limit": limit}
    if entity_type:
        params["entity_type"] = entity_type
    if entity_id:
        params["entity_id"] = entity_id
    return await backend_client.get("/history", params=params)


async def get_entity_history(
    entity_type: str,
    entity_id: str,
    skip: int = 0,
    limit: int = 50,
) -> dict:
    """Get change history for a specific entity.

    Args:
        entity_type: Entity type ('tool', 'resource', 'prompt', 'connection').
        entity_id: UUID of the entity.
        skip: Pagination offset.
        limit: Maximum items to return.

    Returns:
        Object with 'items' array of history entries and 'total' count.
    """
    params: dict = {"skip": skip, "limit": limit}
    return await backend_client.get(f"/history/{entity_type}/{entity_id}", params=params)


async def rollback(history_id: str) -> dict:
    """Rollback an entity to a previous state using a history snapshot.

    Args:
        history_id: UUID of the history entry to rollback to.

    Returns:
        Restored entity object.
    """
    return await backend_client.post(f"/history/{history_id}/rollback")
