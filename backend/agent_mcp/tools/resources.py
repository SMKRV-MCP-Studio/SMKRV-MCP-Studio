"""MCP tools for managing Studio MCP resources."""

from __future__ import annotations

from agent_mcp import backend_client


async def list_resources(
    skip: int = 0,
    limit: int = 50,
    connection_id: str | None = None,
    search: str | None = None,
) -> dict:
    """List MCP resources configured in Studio.

    Args:
        skip: Pagination offset.
        limit: Maximum items to return (1-100).
        connection_id: Filter by database connection UUID.
        search: Search string to filter by name or description.

    Returns:
        Object with 'items' array and 'total' count.
    """
    params: dict = {"skip": skip, "limit": limit}
    if connection_id:
        params["connection_id"] = connection_id
    if search:
        params["search"] = search
    return await backend_client.get("/resources", params=params)


async def get_resource(resource_id: str) -> dict:
    """Get a resource by its ID.

    Args:
        resource_id: UUID of the resource.

    Returns:
        Full resource object.
    """
    return await backend_client.get(f"/resources/{resource_id}")


async def create_resource(
    name: str,
    uri_template: str,
    connection_id: str | None = None,
    sql_query: str | None = None,
    static_content: str | None = None,
    description: str = "",
    mime_type: str = "application/json",
) -> dict:
    """Create a new MCP resource.

    Provide either (connection_id + sql_query) for a SQL-backed resource,
    or static_content for a static text resource.

    Args:
        name: Resource name (used as the MCP resource identifier).
        uri_template: URI template for the resource (e.g. 'resource://users/{id}').
        connection_id: UUID of the database connection (for SQL resources).
        sql_query: SQL query template for fetching the resource data.
        static_content: Static text content (alternative to SQL).
        description: Human-readable description.
        mime_type: MIME type of the resource output (default: application/json).

    Returns:
        Created resource object.
    """
    payload: dict = {
        "name": name,
        "uri_template": uri_template,
        "description": description,
        "mime_type": mime_type,
    }
    if connection_id is not None:
        payload["connection_id"] = connection_id
    if sql_query is not None:
        payload["sql_query"] = sql_query
    if static_content is not None:
        payload["static_content"] = static_content
    return await backend_client.post("/resources", data=payload)


async def update_resource(
    resource_id: str,
    name: str | None = None,
    connection_id: str | None = None,
    uri_template: str | None = None,
    sql_query: str | None = None,
    static_content: str | None = None,
    description: str | None = None,
    mime_type: str | None = None,
) -> dict:
    """Update an existing resource. Only provided fields are changed. Version auto-increments.

    Args:
        resource_id: UUID of the resource to update.
        name: New resource name.
        connection_id: New connection UUID.
        uri_template: New URI template.
        sql_query: New SQL query template.
        static_content: Static text content (set to empty string to clear).
        description: New description.
        mime_type: New MIME type.

    Returns:
        Updated resource object.
    """
    payload: dict = {}
    if name is not None:
        payload["name"] = name
    if connection_id is not None:
        payload["connection_id"] = connection_id
    if uri_template is not None:
        payload["uri_template"] = uri_template
    if sql_query is not None:
        payload["sql_query"] = sql_query
    if static_content is not None:
        payload["static_content"] = static_content
    if description is not None:
        payload["description"] = description
    if mime_type is not None:
        payload["mime_type"] = mime_type
    return await backend_client.patch(f"/resources/{resource_id}", data=payload)


async def delete_resource(resource_id: str) -> dict:
    """Delete a resource by ID.

    Args:
        resource_id: UUID of the resource to delete.

    Returns:
        Confirmation message.
    """
    await backend_client.delete(f"/resources/{resource_id}")
    return {"deleted": True, "id": resource_id}
