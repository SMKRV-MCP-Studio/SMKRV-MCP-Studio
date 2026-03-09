"""SQL preview execution with safety checks."""

import asyncio
import json
import logging
import re
import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.db_registry import PG_COMPATIBLE_TYPES
from app.models.connection import Connection
from app.models.server_config import ServerConfig
from app.schemas.server import PreviewRequest, PreviewResponse
from app.services.crypto import decrypt, decrypt_sensitive_extra
from app.services.jinja_transform import apply_transform

router = APIRouter()
logger = logging.getLogger(__name__)

_PREVIEW_TIMEOUT_SECONDS = 10

# Allowlist: preview SQL must begin with SELECT or WITH (after stripping).
# This is stronger than a denylist of forbidden keywords.
_ALLOWED_START = re.compile(
    r"^\s*(?:--[^\n]*\n\s*)*(SELECT|WITH)\b",
    re.IGNORECASE,
)

# Secondary denylist as defense-in-depth (catches subqueries with DDL/DML/admin commands)
_FORBIDDEN_PATTERNS = re.compile(
    r"\b(ALTER|DROP|CREATE|TRUNCATE|INSERT|UPDATE|DELETE|GRANT|REVOKE"
    r"|EXEC|EXECUTE|CALL|DECLARE|SET|COPY|MERGE|REPLACE|LOAD"
    r"|SHUTDOWN|KILL|BACKUP|RESTORE|DBCC|RECONFIGURE)\b",
    re.IGNORECASE,
)


def _validate_sql(sql: str) -> None:
    """Validate that SQL is read-only using allowlist + denylist."""
    if not _ALLOWED_START.match(sql):
        raise HTTPException(
            status_code=400,
            detail="Only SELECT/WITH queries are allowed in preview mode",
        )
    if _FORBIDDEN_PATTERNS.search(sql):
        raise HTTPException(
            status_code=400,
            detail="Only SELECT queries are allowed in preview mode",
        )


def _ensure_limit(sql: str, limit: int, *, db_type: str = "postgresql") -> str:
    """Add row limit to query if not present.

    MSSQL uses ``SELECT TOP N`` instead of ``LIMIT N``.
    """
    if db_type == "mssql":
        if not re.search(r"\bTOP\b", sql, re.IGNORECASE):
            sql = re.sub(
                r"(?i)^\s*(SELECT)\b",
                rf"\1 TOP {limit}",
                sql.rstrip().rstrip(";"),
                count=1,
            )
        return sql

    if not re.search(r"\bLIMIT\b", sql, re.IGNORECASE):
        sql = sql.rstrip().rstrip(";")
        sql = f"{sql} LIMIT {limit}"
    return sql


def _substitute_params(sql: str, params: dict, *, db_type: str) -> tuple[str, dict | tuple | None]:
    """Convert ``:param_name`` placeholders to driver-specific format.

    Returns ``(sql, driver_params)`` ready for ``cursor.execute(sql, driver_params)``.
    If there are no params, returns ``(sql, None)``.
    """
    if not params:
        return sql, None

    if db_type == "mssql":
        # pymssql: %(name)s with dict
        # MSSQL TOP does not accept bind parameters — inline-substitute
        # any :param that appears in TOP(:param) or TOP :param context.
        remaining = dict(params)

        def _inline_top(m: re.Match) -> str:
            name = m.group(1)
            if name in remaining:
                try:
                    val = int(remaining.pop(name))
                except (ValueError, TypeError):
                    val = 100  # safe fallback
                return f"TOP {val} "
            return m.group(0)

        sql = re.sub(
            r"(?i)\bTOP\s*\(?\s*:(\w+)\s*\)?",
            _inline_top,
            sql,
        )
        converted = re.sub(r":(\w+)", r"%(\1)s", sql)
        return converted, remaining if remaining else None

    if db_type == "mysql":
        # aiomysql: %(name)s with dict
        converted = re.sub(r":(\w+)", r"%(\1)s", sql)
        return converted, params

    if db_type == "clickhouse":
        # clickhouse-connect: %(name)s with dict
        converted = re.sub(r":(\w+)", r"%(\1)s", sql)
        return converted, params

    if db_type in PG_COMPATIBLE_TYPES:
        # asyncpg: $1, $2, ... with positional args
        param_names: list[str] = []
        def _replace(m: re.Match) -> str:
            name = m.group(1)
            if name not in param_names:
                param_names.append(name)
            return f"${param_names.index(name) + 1}"
        converted = re.sub(r":(\w+)", _replace, sql)
        values = tuple(params.get(n, "") for n in param_names)
        return converted, values

    # Fallback: pass params as-is (some drivers may handle :name natively)
    return sql, params


async def _preview_pg(connection, password: str, sql: str, params=None):
    """Execute preview on PostgreSQL-compatible DB (PostgreSQL, Greenplum, Supabase)."""
    import asyncpg
    conn = await asyncpg.connect(
        host=connection.host, port=connection.port,
        database=connection.database, user=connection.username,
        password=password, timeout=10,
    )
    try:
        await conn.execute("SET TRANSACTION READ ONLY")
        if params:
            rows = await conn.fetch(sql, *params)
        else:
            rows = await conn.fetch(sql)
        columns = list(rows[0].keys()) if rows else []
        data_rows = [dict(r) for r in rows]
    finally:
        await conn.close()
    return columns, data_rows


async def _preview_clickhouse(connection, password: str, sql: str, params=None):
    import clickhouse_connect
    loop = asyncio.get_event_loop()
    def _do():
        client = clickhouse_connect.get_client(
            host=connection.host, port=connection.port,
            database=connection.database, username=connection.username,
            password=password,
        )
        try:
            query_result = client.query(sql, parameters=params or {})
            columns = list(query_result.column_names)
            data_rows = [dict(zip(columns, row)) for row in query_result.result_rows]
        finally:
            client.close()
        return columns, data_rows
    return await loop.run_in_executor(None, _do)


async def _preview_mysql(connection, password: str, sql: str, params=None):
    import aiomysql
    conn = await aiomysql.connect(
        host=connection.host, port=connection.port,
        db=connection.database, user=connection.username,
        password=password, connect_timeout=10,
    )
    try:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SET SESSION TRANSACTION READ ONLY")
            await cur.execute("START TRANSACTION")
            await cur.execute(sql, params)
            data_rows = await cur.fetchall()
            columns = [desc[0] for desc in cur.description] if cur.description else []
            await cur.execute("ROLLBACK")
    finally:
        conn.close()
    return columns, list(data_rows)


async def _preview_mssql(connection, password: str, sql: str, params=None):
    import pymssql
    loop = asyncio.get_event_loop()
    def _do():
        conn = pymssql.connect(
            server=connection.host, port=connection.port,
            database=connection.database, user=connection.username,
            password=password, login_timeout=10,
        )
        cursor = conn.cursor(as_dict=True)
        try:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")
            cursor.execute("BEGIN TRANSACTION")
            cursor.execute(sql, params)
            data_rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            cursor.execute("ROLLBACK TRANSACTION")
        finally:
            cursor.close()
            conn.close()
        return columns, list(data_rows)
    return await loop.run_in_executor(None, _do)


async def _preview_cassandra(connection, password: str, sql: str, extra: dict):
    from cassandra.auth import PlainTextAuthProvider
    from cassandra.cluster import Cluster
    loop = asyncio.get_event_loop()
    keyspace = extra.get("keyspace", "system")
    def _do():
        auth = PlainTextAuthProvider(
            username=connection.username, password=password
        ) if connection.username else None
        cluster = Cluster([connection.host], port=connection.port, auth_provider=auth)
        session = cluster.connect(keyspace)
        result = session.execute(sql)
        columns = list(result.column_names) if result.column_names else []
        data_rows = [dict(zip(columns, row)) for row in result]
        session.shutdown()
        cluster.shutdown()
        return columns, data_rows
    return await loop.run_in_executor(None, _do)


async def _preview_snowflake(connection, password: str, sql: str, extra: dict):
    import snowflake.connector
    loop = asyncio.get_event_loop()
    ep = extra
    def _do():
        conn = snowflake.connector.connect(
            account=ep.get("account", ""),
            user=connection.username,
            password=password,
            warehouse=ep.get("warehouse", ""),
            database=connection.database or ep.get("database", ""),
            schema=ep.get("schema", "PUBLIC"),
            role=ep.get("role", ""),
            login_timeout=10,
        )
        cur = conn.cursor(snowflake.connector.DictCursor)
        try:
            cur.execute(sql)
            data_rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description] if cur.description else []
        finally:
            cur.close()
            conn.close()
        return columns, list(data_rows)
    return await loop.run_in_executor(None, _do)


async def _preview_bigquery(connection, sql: str, extra: dict):
    from google.cloud import bigquery
    from google.oauth2 import service_account
    loop = asyncio.get_event_loop()
    ep = extra
    def _do():
        creds_json = ep.get("credentials_json", "{}")
        if isinstance(creds_json, str):
            creds_info = json.loads(creds_json)
        else:
            creds_info = creds_json
        credentials = service_account.Credentials.from_service_account_info(creds_info)
        client = bigquery.Client(
            project=ep.get("project_id", ""), credentials=credentials,
        )
        query_job = client.query(sql)
        result = query_job.result()
        columns = [field.name for field in result.schema]
        data_rows = [dict(row) for row in result]
        client.close()
        return columns, data_rows
    return await loop.run_in_executor(None, _do)


@router.post("/preview/execute", response_model=PreviewResponse)
async def execute_preview(
    data: PreviewRequest, db: AsyncSession = Depends(get_db)
) -> dict:
    """Execute a SQL query in read-only mode with safety checks."""
    _validate_sql(data.sql_query)

    result = await db.execute(select(Connection).where(Connection.id == data.connection_id))
    connection = result.scalar_one_or_none()
    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")

    sql = _ensure_limit(data.sql_query, data.limit, db_type=connection.db_type)
    sql, driver_params = _substitute_params(sql, data.params, db_type=connection.db_type)

    password = decrypt(connection.password_encrypted)
    extra = decrypt_sensitive_extra(connection.extra_params) or {}
    start = time.monotonic()

    try:
        if connection.db_type in PG_COMPATIBLE_TYPES:
            coro = _preview_pg(connection, password, sql, driver_params)
        elif connection.db_type == "clickhouse":
            coro = _preview_clickhouse(connection, password, sql, driver_params)
        elif connection.db_type == "mysql":
            coro = _preview_mysql(connection, password, sql, driver_params)
        elif connection.db_type == "mssql":
            coro = _preview_mssql(connection, password, sql, driver_params)
        elif connection.db_type == "cassandra":
            coro = _preview_cassandra(connection, password, sql, extra)
        elif connection.db_type == "snowflake":
            coro = _preview_snowflake(connection, password, sql, extra)
        elif connection.db_type == "bigquery":
            coro = _preview_bigquery(connection, sql, extra)
        else:
            raise HTTPException(
                status_code=400, detail=f"Unsupported db_type: {connection.db_type}"
            )

        # A4-06: Enforce execution timeout
        columns, data_rows = await asyncio.wait_for(coro, timeout=_PREVIEW_TIMEOUT_SECONDS)

        execution_time = (time.monotonic() - start) * 1000

        transformed_result = None
        transform_error = None

        if data.transform_template:
            try:
                sc_result = await db.execute(select(ServerConfig))
                sc = sc_result.scalar_one_or_none()
                global_vars = (sc.global_variables or {}) if sc else {}

                transformed_result = apply_transform(
                    rows=data_rows,
                    template_str=data.transform_template,
                    global_vars=global_vars,
                    params=data.params,
                )
            except Exception as e:
                logger.warning("Jinja transform error: %s", e)
                transform_error = str(e)

        return {
            "columns": columns,
            "rows": data_rows,
            "row_count": len(data_rows),
            "execution_time_ms": execution_time,
            "transformed_result": transformed_result,
            "transform_error": transform_error,
        }

    except TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"Query execution timed out after {_PREVIEW_TIMEOUT_SECONDS}s",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Query execution error: %s", e)
        raise HTTPException(
            status_code=502,
            detail="Query execution error. Check server logs for details.",
        ) from e
