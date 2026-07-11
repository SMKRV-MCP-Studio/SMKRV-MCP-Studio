"""MCP tools for managing database connections."""

from __future__ import annotations

from agent_mcp import backend_client


async def list_connections(
    skip: int = 0,
    limit: int = 50,
    search: str | None = None,
) -> dict:
    """List all database connections configured in Studio.

    Args:
        skip: Number of items to skip (pagination offset).
        limit: Maximum number of items to return (1-100).
        search: Optional search string to filter by name.

    Returns:
        Object with 'items' array and 'total' count.
    """
    params: dict = {"skip": skip, "limit": limit}
    if search:
        params["search"] = search
    return await backend_client.get("/connections", params=params)


async def get_connection(connection_id: str) -> dict:
    """Get a database connection by its ID.

    Args:
        connection_id: UUID of the connection.

    Returns:
        Full connection object with all fields.
    """
    return await backend_client.get(f"/connections/{connection_id}")


async def create_connection(
    name: str,
    db_type: str,
    host: str,
    port: int,
    database: str,
    username: str = "",
    password: str = "",
    extra_params: dict | None = None,
) -> dict:
    """Create a new database connection.

    Args:
        name: Human-readable connection name.
        db_type: Database type (postgresql, mysql, mssql, clickhouse, cassandra, greenplum, supabase, snowflake, bigquery).
        host: Database host address.
        port: Database port number.
        database: Database name or path.
        username: Database username.
        password: Database password (will be encrypted at rest).
        extra_params: Optional extra connection parameters as key-value pairs.

    Returns:
        Created connection object.
    """
    payload: dict = {
        "name": name,
        "db_type": db_type,
        "host": host,
        "port": port,
        "database": database,
        "username": username,
        "password": password,
    }
    if extra_params:
        payload["extra_params"] = extra_params
    return await backend_client.post("/connections", data=payload)


async def update_connection(
    connection_id: str,
    name: str | None = None,
    db_type: str | None = None,
    host: str | None = None,
    port: int | None = None,
    database: str | None = None,
    username: str | None = None,
    password: str | None = None,
    extra_params: dict | None = None,
) -> dict:
    """Update an existing database connection. Only provided fields are changed.

    Args:
        connection_id: UUID of the connection to update.
        name: New connection name.
        db_type: New database type.
        host: New host address.
        port: New port number.
        database: New database name.
        username: New username.
        password: New password (will be encrypted at rest).
        extra_params: New extra parameters.

    Returns:
        Updated connection object.
    """
    payload: dict = {}
    if name is not None:
        payload["name"] = name
    if db_type is not None:
        payload["db_type"] = db_type
    if host is not None:
        payload["host"] = host
    if port is not None:
        payload["port"] = port
    if database is not None:
        payload["database"] = database
    if username is not None:
        payload["username"] = username
    if password is not None:
        payload["password"] = password
    if extra_params is not None:
        payload["extra_params"] = extra_params
    return await backend_client.patch(f"/connections/{connection_id}", data=payload)


async def delete_connection(connection_id: str) -> dict:
    """Delete a database connection by ID.

    Args:
        connection_id: UUID of the connection to delete.

    Returns:
        Confirmation message.
    """
    await backend_client.delete(f"/connections/{connection_id}")
    return {"deleted": True, "id": connection_id}


async def test_connection(connection_id: str) -> dict:
    """Test connectivity for a database connection.

    Args:
        connection_id: UUID of the connection to test.

    Returns:
        Object with 'success' boolean and optional 'error' message.
    """
    return await backend_client.post(f"/connections/{connection_id}/test")


async def list_tables(connection_id: str) -> dict:
    """List all tables in a database connection's schema.

    Args:
        connection_id: UUID of the connection.

    Returns:
        Object with 'tables' array of {table_schema, table_name, table_type}.
    """
    tables = await backend_client.get(f"/schema/{connection_id}/tables")
    if isinstance(tables, list):
        return {"tables": tables, "total": len(tables)}
    return tables


async def list_columns(connection_id: str, table_name: str) -> dict:
    """List all columns of a specific table.

    Args:
        connection_id: UUID of the connection.
        table_name: Name of the table to inspect.

    Returns:
        Object with 'columns' array containing name, type, nullable, etc.
    """
    columns = await backend_client.get(
        f"/schema/{connection_id}/tables/{table_name}/columns",
    )
    if isinstance(columns, list):
        return {"columns": columns, "total": len(columns)}
    return columns
