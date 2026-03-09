"""MCP tools for managing Studio server configuration and global variables."""

from __future__ import annotations

from agent_mcp import backend_client


async def get_server_config() -> dict:
    """Get the current MCP server configuration.

    Returns server settings including name, transport, host, port,
    auth type, CORS origins, SSL settings, and global variables.

    Returns:
        Full server configuration object.
    """
    return await backend_client.get("/server/config")


async def update_server_config(
    server_name: str | None = None,
    transport: str | None = None,
    host: str | None = None,
    port: int | None = None,
    log_level: str | None = None,
    auth_type: str | None = None,
    cors_origins: list[str] | None = None,
    global_variables: dict | None = None,
) -> dict:
    """Update MCP server configuration. Only provided fields are changed.

    Args:
        server_name: Display name for the MCP server.
        transport: Transport protocol — 'http', 'sse', or 'stdio'.
        host: Bind address (e.g. '0.0.0.0').
        port: MCP server port (e.g. 8080).
        log_level: FastMCP log level — 'DEBUG', 'INFO', 'WARNING', 'ERROR'.
        auth_type: Authentication type — 'none', 'bearer', 'oauth_credentials', 'oauth_introspection'.
        cors_origins: Allowed CORS origins (array of URLs).
        global_variables: Server-level key-value pairs accessible in Jinja2 transform templates as {{ vars.key }}.
            Max 100 variables. Names: letters, digits, underscores (max 64 chars). Values: string, int, float, bool.

    Returns:
        Updated server configuration object.
    """
    payload: dict = {}
    if server_name is not None:
        payload["server_name"] = server_name
    if transport is not None:
        payload["transport"] = transport
    if host is not None:
        payload["host"] = host
    if port is not None:
        payload["port"] = port
    if log_level is not None:
        payload["log_level"] = log_level
    if auth_type is not None:
        payload["auth_type"] = auth_type
    if cors_origins is not None:
        payload["cors_origins"] = cors_origins
    if global_variables is not None:
        payload["global_variables"] = global_variables
    return await backend_client.patch("/server/config", data=payload)


async def get_global_variables() -> dict:
    """Get all server-level global variables.

    Global variables are key-value pairs accessible in all Jinja2
    transform templates as {{ vars.key_name }}.

    Returns:
        Object with 'variables' dict and 'count' integer.
    """
    config = await backend_client.get("/server/config")
    variables = config.get("global_variables", {})
    return {"variables": variables, "count": len(variables)}


async def set_global_variables(variables: dict) -> dict:
    """Replace all global variables with the provided set.

    This replaces the entire global_variables dict. To add or update
    individual variables, use update_global_variables instead.

    Args:
        variables: Complete set of key-value pairs. Max 100 variables.
            Keys: alphanumeric + underscores, max 64 chars (must start with letter or underscore).
            Values: string, integer, float, or boolean.

    Returns:
        Object with 'variables' dict and 'count' integer.
    """
    config = await backend_client.patch(
        "/server/config", data={"global_variables": variables}
    )
    new_vars = config.get("global_variables", {})
    return {"variables": new_vars, "count": len(new_vars)}


async def update_global_variables(
    set_vars: dict | None = None,
    delete_keys: list[str] | None = None,
) -> dict:
    """Add, update, or delete individual global variables.

    Merges changes with existing variables — does not replace the whole set.

    Args:
        set_vars: Key-value pairs to add or update.
        delete_keys: List of variable names to remove.

    Returns:
        Object with 'variables' dict and 'count' integer.
    """
    config = await backend_client.get("/server/config")
    current = dict(config.get("global_variables", {}))

    if delete_keys:
        for key in delete_keys:
            current.pop(key, None)
    if set_vars:
        current.update(set_vars)

    updated = await backend_client.patch(
        "/server/config", data={"global_variables": current}
    )
    new_vars = updated.get("global_variables", {})
    return {"variables": new_vars, "count": len(new_vars)}


async def get_server_health() -> dict:
    """Check the health of the deployed MCP server.

    Returns status (running/stopped/error), tools count, and other info.

    Returns:
        Health status object with 'status', 'message', and counts.
    """
    return await backend_client.get("/server/health")


async def get_generated_code() -> dict:
    """Get the generated MCP server Python code files.

    Returns the contents of all auto-generated server files (read-only).

    Returns:
        Object with 'files' array (path, content, size_bytes) and 'total'.
    """
    result = await backend_client.get("/server/generated")
    if isinstance(result, list):
        return {"files": result, "total": len(result)}
    return result
