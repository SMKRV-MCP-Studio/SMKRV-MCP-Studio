"""MCP tools for managing Studio MCP tools (SQL-backed endpoints)."""

from __future__ import annotations

from agent_mcp import backend_client


async def list_tools(
    skip: int = 0,
    limit: int = 50,
    connection_id: str | None = None,
    search: str | None = None,
    tags: str | None = None,
    compact: bool = False,
    fields: str | None = None,
) -> dict:
    """List MCP tools configured in Studio.

    Args:
        skip: Pagination offset.
        limit: Maximum items to return (1-100).
        connection_id: Filter by database connection UUID.
        search: Search string to filter by name or description.
        tags: Comma-separated tag filter.
        compact: If true, return only id, name, connection_name, tags (smaller payload).
        fields: Comma-separated field names to return per item
            (e.g. "id,name,sql_query"). Overrides compact.

    Returns:
        Object with 'items' array and 'total' count.
    """
    params: dict = {"skip": skip, "limit": limit}
    if connection_id:
        params["connection_id"] = connection_id
    if search:
        params["search"] = search
    if tags:
        params["tags"] = tags
    result = await backend_client.get("/tools", params=params)
    if (fields or compact) and isinstance(result.get("items"), list):
        field_set = (
            {f.strip() for f in fields.split(",") if f.strip()}
            if fields
            else {"id", "name", "connection_name", "tags"}
        )
        # Apply server-side allowlist (empty = no restriction)
        if fields:
            allowlist = await backend_client.get_fields_allowlist()
            if allowlist:
                field_set = field_set & allowlist
                if not field_set:
                    field_set = {"id", "name"}
        result["items"] = [
            {k: item.get(k) for k in field_set if k in item}
            for item in result["items"]
        ]
    return result


async def get_tool(tool_id: str) -> dict:
    """Get a tool by its ID, including parameters.

    Args:
        tool_id: UUID of the tool.

    Returns:
        Full tool object with parameters, SQL query, etc.
    """
    return await backend_client.get(f"/tools/{tool_id}")


async def create_tool(
    name: str,
    connection_id: str,
    sql_query: str,
    description: str = "",
    parameters: list[dict] | None = None,
    tags: list[str] | None = None,
    transform_template: str | None = None,
) -> dict:
    """Create a new MCP tool with inline parameters.

    Args:
        name: Tool name (used as the MCP tool identifier).
        connection_id: UUID of the database connection this tool uses.
        sql_query: SQL query template. Use :param_name for parameter placeholders.
        description: Human-readable description of what this tool does.
        parameters: List of parameter definitions. Each has: name,
            param_type, description, is_required, default_value.
        tags: Optional list of tags for organization.
        transform_template: Optional Jinja2 template to
            post-process SQL results (rows, vars, params).

    Returns:
        Created tool object.
    """
    payload: dict = {
        "name": name,
        "connection_id": connection_id,
        "sql_query": sql_query,
        "description": description,
    }
    if parameters is not None:
        payload["parameters"] = parameters
    if tags is not None:
        payload["tags"] = tags
    if transform_template is not None:
        payload["transform_template"] = transform_template
    return await backend_client.post("/tools", data=payload)


async def update_tool(
    tool_id: str,
    name: str | None = None,
    connection_id: str | None = None,
    sql_query: str | None = None,
    description: str | None = None,
    parameters: list[dict] | None = None,
    tags: list[str] | None = None,
    transform_template: str | None = None,
) -> dict:
    """Update an existing tool. Only provided fields are changed. Version auto-increments.

    Args:
        tool_id: UUID of the tool to update.
        name: New tool name.
        connection_id: New connection UUID.
        sql_query: New SQL query template.
        description: New description.
        parameters: New parameter definitions (replaces all existing).
        tags: New tags (replaces all existing).
        transform_template: Jinja2 template to post-process results.
            Set to empty string to remove.

    Returns:
        Updated tool object.
    """
    payload: dict = {}
    if name is not None:
        payload["name"] = name
    if connection_id is not None:
        payload["connection_id"] = connection_id
    if sql_query is not None:
        payload["sql_query"] = sql_query
    if description is not None:
        payload["description"] = description
    if parameters is not None:
        payload["parameters"] = parameters
    if tags is not None:
        payload["tags"] = tags
    if transform_template is not None:
        payload["transform_template"] = transform_template
    return await backend_client.patch(f"/tools/{tool_id}", data=payload)


async def delete_tool(tool_id: str) -> dict:
    """Delete a tool by ID.

    Args:
        tool_id: UUID of the tool to delete.

    Returns:
        Confirmation message.
    """
    await backend_client.delete(f"/tools/{tool_id}")
    return {"deleted": True, "id": tool_id}


async def duplicate_tool(tool_id: str) -> dict:
    """Duplicate an existing tool (creates a copy with ' (copy)' suffix).

    Args:
        tool_id: UUID of the tool to duplicate.

    Returns:
        Newly created tool object.
    """
    return await backend_client.post(f"/tools/{tool_id}/duplicate")


_MAX_BATCH_SIZE = 50


async def create_tools_batch(
    tools: list[dict],
) -> dict:
    """Create multiple tools in a single call (max 50).

    Args:
        tools: List of tool definitions (max 50). Each must have:
            name, connection_id, sql_query. Optional: description,
            parameters, tags, transform_template.

    Returns:
        Object with 'created' (list), 'errors' (list),
        'total_created' and 'total_errors' counts.
    """
    if len(tools) > _MAX_BATCH_SIZE:
        return {
            "created": [],
            "errors": [{
                "index": 0,
                "name": "batch",
                "error": f"Batch size {len(tools)} exceeds"
                         f" maximum of {_MAX_BATCH_SIZE}",
            }],
            "total_created": 0,
            "total_errors": 1,
        }
    created = []
    errors = []
    for i, tool_data in enumerate(tools):
        try:
            result = await backend_client.post("/tools", data=tool_data)
            created.append(result)
        except Exception as e:
            msg = str(e)[:200] if str(e) else "Internal error"
            errors.append({
                "index": i,
                "name": tool_data.get("name", "?"),
                "error": msg,
            })
    return {
        "created": created,
        "errors": errors,
        "total_created": len(created),
        "total_errors": len(errors),
    }


async def preview_sql(
    connection_id: str,
    sql_query: str,
    params: dict | None = None,
    limit: int = 10,
    transform_template: str | None = None,
) -> dict:
    """Execute a read-only SQL query and return results (for testing/preview).

    Args:
        connection_id: UUID of the database connection to run against.
        sql_query: SQL query to execute (read-only).
        params: Query parameter values as key-value pairs.
        limit: Maximum rows to return (1-1000, default 10).
        transform_template: Optional Jinja2 template to post-process results.

    Returns:
        Object with 'columns', 'rows', 'row_count', 'execution_time_ms',
        and optionally 'transformed_result', 'transform_error'.
    """
    payload: dict = {
        "connection_id": connection_id,
        "sql_query": sql_query,
        "limit": limit,
    }
    if params:
        payload["params"] = params
    if transform_template:
        payload["transform_template"] = transform_template
    return await backend_client.post("/preview/execute", data=payload)
