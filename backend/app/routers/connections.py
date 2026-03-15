"""CRUD endpoints for database connections."""

import asyncio
import json as _json
import logging
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.db_registry import PG_COMPATIBLE_TYPES, pg_ssl_context, validate_charset
from app.models.connection import Connection
from app.schemas.connection import (
    ConnectionCreate,
    ConnectionHealthList,
    ConnectionList,
    ConnectionResponse,
    ConnectionTestResult,
    ConnectionUpdate,
)
from app.services.crypto import decrypt, decrypt_sensitive_extra, encrypt, encrypt_sensitive_extra
from app.services.history import compute_changes, model_to_dict, record_change
from app.services.redis_client import get_redis

router = APIRouter()
logger = logging.getLogger(__name__)

_TEST_CONNECTION_TIMEOUT = 10  # seconds
_HEALTH_CHECK_TIMEOUT = 5  # seconds — shorter for batch health checks
_HEALTH_CACHE_TTL = 60  # seconds
_HEALTH_CACHE_PREFIX = "conn:health:"



@router.post("/connections", response_model=ConnectionResponse, status_code=201)
async def create_connection(
    data: ConnectionCreate, db: AsyncSession = Depends(get_db)
) -> Connection:
    """Create a new database connection."""
    connection = Connection(
        name=data.name,
        db_type=data.db_type,
        host=data.host,
        port=data.port,
        database=data.database,
        username=data.username,
        password_encrypted=encrypt(data.password) if data.password else encrypt(""),
        ssl_mode=data.ssl_mode,
        pool_min_size=data.pool_min_size,
        pool_max_size=data.pool_max_size,
        extra_params=encrypt_sensitive_extra(data.extra_params),
        is_active=data.is_active,
    )
    db.add(connection)
    await db.flush()
    await record_change(
        db, entity_type="connection", entity_id=connection.id,
        entity_name=connection.name, action="create",
    )
    await db.commit()
    await db.refresh(connection)
    return connection


@router.get("/connections", response_model=ConnectionList)
async def list_connections(
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List all connections with pagination."""
    total_result = await db.execute(select(func.count(Connection.id)))
    total = total_result.scalar_one()

    result = await db.execute(
        select(Connection).offset(skip).limit(limit).order_by(Connection.name)
    )
    items = list(result.scalars().all())
    return {"items": items, "total": total}


@router.get("/connections/health", response_model=ConnectionHealthList)
async def check_connections_health(
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Batch health check for all active connections (cached 60s in Redis)."""
    result = await db.execute(
        select(Connection).where(Connection.is_active.is_(True)).order_by(Connection.name)
    )
    connections = list(result.scalars().all())

    r = get_redis()
    items: list[dict] = []

    async def _check_one(conn: Connection) -> dict:
        cache_key = f"{_HEALTH_CACHE_PREFIX}{conn.id}"
        # Check Redis cache first
        try:
            cached = await r.get(cache_key)
            if cached:
                return _json.loads(cached)
        except Exception:
            pass  # Redis down — proceed without cache

        # Run actual health check
        password = decrypt(conn.password_encrypted)
        extra = decrypt_sensitive_extra(conn.extra_params)
        start = time.monotonic()

        try:
            if conn.db_type in PG_COMPATIBLE_TYPES:
                coro = _test_pg_compatible(
                    conn.host, conn.port, conn.database, conn.username, password,
                    conn.ssl_mode,
                )
            elif conn.db_type == "clickhouse":
                coro = _test_clickhouse(
                    conn.host, conn.port, conn.database, conn.username, password,
                )
            elif conn.db_type == "mysql":
                coro = _test_mysql(
                    conn.host, conn.port, conn.database, conn.username, password,
                )
            elif conn.db_type == "mssql":
                coro = _test_mssql(
                    conn.host, conn.port, conn.database, conn.username, password,
                    extra,
                )
            elif conn.db_type == "cassandra":
                coro = _test_cassandra(
                    conn.host, conn.port, extra, conn.username, password,
                )
            elif conn.db_type == "snowflake":
                coro = _test_snowflake(extra, conn.username, password)
            elif conn.db_type == "bigquery":
                coro = _test_bigquery(extra)
            else:
                return {
                    "id": conn.id, "name": conn.name, "db_type": conn.db_type,
                    "healthy": False, "latency_ms": 0.0,
                    "error": f"Unsupported db_type: {conn.db_type}",
                    "cached": False,
                }

            await asyncio.wait_for(coro, timeout=_HEALTH_CHECK_TIMEOUT)
            latency = (time.monotonic() - start) * 1000
            item = {
                "id": conn.id, "name": conn.name, "db_type": conn.db_type,
                "healthy": True, "latency_ms": round(latency, 1),
                "error": None, "cached": False,
            }
        except TimeoutError:
            latency = (time.monotonic() - start) * 1000
            item = {
                "id": conn.id, "name": conn.name, "db_type": conn.db_type,
                "healthy": False, "latency_ms": round(latency, 1),
                "error": f"Timed out after {_HEALTH_CHECK_TIMEOUT}s",
                "cached": False,
            }
        except Exception as e:
            latency = (time.monotonic() - start) * 1000
            logger.warning(
                "Health check failed for %s (%s): %s", conn.name, conn.id, type(e).__name__,
            )
            item = {
                "id": conn.id, "name": conn.name, "db_type": conn.db_type,
                "healthy": False, "latency_ms": round(latency, 1),
                "error": type(e).__name__, "cached": False,
            }

        # Cache result in Redis
        try:
            await r.set(cache_key, _json.dumps(item), ex=_HEALTH_CACHE_TTL)
        except Exception:
            pass  # Redis down — skip caching

        return item

    # Run all health checks concurrently (capped at 10 parallel)
    sem = asyncio.Semaphore(10)

    async def _check_limited(conn: Connection) -> dict:
        async with sem:
            return await _check_one(conn)

    results = await asyncio.gather(
        *[_check_limited(c) for c in connections], return_exceptions=True,
    )
    for i, res in enumerate(results):
        if isinstance(res, Exception):
            conn = connections[i]
            items.append({
                "id": conn.id, "name": conn.name, "db_type": conn.db_type,
                "healthy": False, "latency_ms": 0.0,
                "error": type(res).__name__, "cached": False,
            })
        else:
            items.append(res)

    healthy_count = sum(1 for it in items if it["healthy"])
    return {"items": items, "total": len(items), "healthy_count": healthy_count}


@router.get("/connections/{connection_id}", response_model=ConnectionResponse)
async def get_connection(
    connection_id: str, db: AsyncSession = Depends(get_db)
) -> Connection:
    """Get a connection by ID."""
    result = await db.execute(select(Connection).where(Connection.id == connection_id))
    connection = result.scalar_one_or_none()
    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")
    return connection


@router.patch("/connections/{connection_id}", response_model=ConnectionResponse)
async def update_connection(
    connection_id: str, data: ConnectionUpdate, db: AsyncSession = Depends(get_db)
) -> Connection:
    """Update a connection."""
    result = await db.execute(select(Connection).where(Connection.id == connection_id))
    connection = result.scalar_one_or_none()
    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")

    # OL-1: Optimistic locking — reject if client version is stale
    if data.version is not None and data.version != (connection.version or 1):
        raise HTTPException(
            status_code=409,
            detail=f"Version conflict: expected {data.version}, current {connection.version or 1}",
        )

    before = model_to_dict(connection)
    update_data = data.model_dump(exclude_unset=True)
    update_data.pop("version", None)  # Don't set version from client

    # Encrypt password if provided
    if "password" in update_data:
        update_data["password_encrypted"] = encrypt(update_data.pop("password"))

    # Encrypt sensitive extra_params fields
    if "extra_params" in update_data and update_data["extra_params"] is not None:
        update_data["extra_params"] = encrypt_sensitive_extra(update_data["extra_params"])

    for field, value in update_data.items():
        setattr(connection, field, value)

    # Auto-increment version on every update (OL-2)
    connection.version = (connection.version or 0) + 1

    changes = compute_changes(before, data.model_dump(exclude_unset=True))
    # Don't store raw password in changes
    changes.pop("password", None)
    await record_change(
        db, entity_type="connection", entity_id=connection.id,
        entity_name=connection.name, action="update", snapshot=before, changes=changes,
    )
    await db.commit()
    await db.refresh(connection)
    return connection


@router.delete("/connections/{connection_id}", status_code=204)
async def delete_connection(
    connection_id: str, db: AsyncSession = Depends(get_db)
) -> None:
    """Delete a connection and cascade to related tools."""
    result = await db.execute(select(Connection).where(Connection.id == connection_id))
    connection = result.scalar_one_or_none()
    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")

    before = model_to_dict(connection)
    await record_change(
        db, entity_type="connection", entity_id=connection.id,
        entity_name=connection.name, action="delete", snapshot=before,
    )
    await db.delete(connection)
    await db.commit()


# ---------------------------------------------------------------------------
# Connection test helpers
# ---------------------------------------------------------------------------

async def _test_pg_compatible(
    host, port, database, username, password, ssl_mode=None,
) -> None:
    """Test PostgreSQL-compatible connection (PostgreSQL, Greenplum, Supabase)."""
    import asyncpg
    kw = dict(
        host=host, port=port, database=database,
        user=username, password=password, timeout=10,
    )
    _ssl = pg_ssl_context(ssl_mode)
    if _ssl is not None:
        kw["ssl"] = _ssl
    conn = await asyncpg.connect(**kw)
    try:
        await conn.execute("SELECT 1")
    finally:
        await conn.close()


async def _test_clickhouse(host, port, database, username, password) -> None:
    import clickhouse_connect
    loop = asyncio.get_running_loop()
    def _do():
        client = clickhouse_connect.get_client(
            host=host, port=port, database=database,
            username=username, password=password,
            connect_timeout=10, send_receive_timeout=10,
        )
        try:
            client.query("SELECT 1")
        finally:
            client.close()
    await loop.run_in_executor(None, _do)


async def _test_mysql(host, port, database, username, password) -> None:
    import aiomysql
    conn = await aiomysql.connect(
        host=host, port=port, db=database,
        user=username, password=password,
        connect_timeout=10,
    )
    try:
        async with conn.cursor() as cur:
            await cur.execute("SELECT 1")
    finally:
        conn.close()


async def _test_cassandra(host, port, extra_params, username, password) -> None:
    from cassandra.auth import PlainTextAuthProvider
    from cassandra.cluster import Cluster
    loop = asyncio.get_running_loop()
    keyspace = (extra_params or {}).get("keyspace", "system")
    def _do():
        auth = PlainTextAuthProvider(username=username, password=password) if username else None
        cluster = Cluster(
            [host], port=port, auth_provider=auth,
            connect_timeout=10, control_connection_timeout=10,
        )
        try:
            session = cluster.connect(keyspace)
            try:
                session.execute("SELECT now() FROM system.local")
            finally:
                session.shutdown()
        finally:
            cluster.shutdown()
    await loop.run_in_executor(None, _do)


async def _test_mssql(host, port, database, username, password, extra_params=None):
    import pymssql
    loop = asyncio.get_running_loop()
    charset = validate_charset(extra_params)
    def _do():
        kw = dict(
            server=host, port=port, database=database,
            user=username, password=password, login_timeout=10, timeout=10,
        )
        if charset:
            kw["charset"] = charset
        conn = pymssql.connect(**kw)
        try:
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT 1")
                cursor.fetchone()
                hints = []
                if not charset:
                    try:
                        cursor.execute("SELECT SERVERPROPERTY('Collation')")
                        row = cursor.fetchone()
                        if row and row[0]:
                            collation = str(row[0])[:100]
                            cyrillic_markers = ("cyrillic", "russian", "ukrainian", "belarusian")
                            if any(m in collation.lower() for m in cyrillic_markers):
                                hints.append(
                                    f"Server collation is '{collation}'. "
                                    "Cyrillic varchar data may appear garbled. "
                                    'Set charset in Extra Params: {{"charset": "cp1251"}}'
                                )
                    except Exception:
                        pass
            finally:
                cursor.close()
        finally:
            conn.close()
        return hints or None
    return await loop.run_in_executor(None, _do)


async def _test_snowflake(extra_params, username, password) -> None:
    import snowflake.connector
    loop = asyncio.get_running_loop()
    ep = extra_params or {}
    def _do():
        conn = snowflake.connector.connect(
            account=ep.get("account", ""),
            user=username,
            password=password,
            warehouse=ep.get("warehouse", ""),
            database=ep.get("database", ""),
            schema=ep.get("schema", "PUBLIC"),
            role=ep.get("role", ""),
            login_timeout=10,
            network_timeout=30,
        )
        try:
            cur = conn.cursor()
            try:
                cur.execute("SELECT 1")
            finally:
                cur.close()
        finally:
            conn.close()
    await loop.run_in_executor(None, _do)


async def _test_bigquery(extra_params) -> None:
    import json

    from google.cloud import bigquery
    from google.oauth2 import service_account
    loop = asyncio.get_running_loop()
    ep = extra_params or {}
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
        try:
            query_job = client.query("SELECT 1")
            list(query_job.result(timeout=10))
        finally:
            client.close()
    await loop.run_in_executor(None, _do)


@router.post(
    "/connections/{connection_id}/test", response_model=ConnectionTestResult
)
async def test_connection(
    connection_id: str, db: AsyncSession = Depends(get_db)
) -> dict:
    """Test a database connection."""
    result = await db.execute(select(Connection).where(Connection.id == connection_id))
    connection = result.scalar_one_or_none()
    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")

    password = decrypt(connection.password_encrypted)
    extra = decrypt_sensitive_extra(connection.extra_params)
    start = time.monotonic()

    try:
        if connection.db_type in PG_COMPATIBLE_TYPES:
            coro = _test_pg_compatible(
                connection.host, connection.port, connection.database,
                connection.username, password, connection.ssl_mode,
            )
        elif connection.db_type == "clickhouse":
            coro = _test_clickhouse(
                connection.host, connection.port, connection.database,
                connection.username, password,
            )
        elif connection.db_type == "mysql":
            coro = _test_mysql(
                connection.host, connection.port, connection.database,
                connection.username, password,
            )
        elif connection.db_type == "mssql":
            coro = _test_mssql(
                connection.host, connection.port, connection.database,
                connection.username, password, extra,
            )
        elif connection.db_type == "cassandra":
            coro = _test_cassandra(
                connection.host, connection.port, extra,
                connection.username, password,
            )
        elif connection.db_type == "snowflake":
            coro = _test_snowflake(
                extra, connection.username, password,
            )
        elif connection.db_type == "bigquery":
            coro = _test_bigquery(extra)
        else:
            return {
                "success": False,
                "message": f"Unsupported db_type: {connection.db_type}",
                "latency_ms": 0.0,
            }

        # A4-16: Enforce connection test timeout
        result = await asyncio.wait_for(coro, timeout=_TEST_CONNECTION_TIMEOUT)

        latency = (time.monotonic() - start) * 1000
        resp = {"success": True, "message": "Connection successful", "latency_ms": latency}
        if isinstance(result, list):
            resp["hints"] = result
        return resp
    except TimeoutError:
        latency = (time.monotonic() - start) * 1000
        return {
            "success": False,
            "message": f"Connection test timed out after {_TEST_CONNECTION_TIMEOUT}s",
            "latency_ms": latency,
        }
    except Exception as e:
        # ERR-01: Log raw exception but return sanitized message (no IPs/ports/auth)
        logger.error("Connection test failed for %s: %s", connection_id, e)
        latency = (time.monotonic() - start) * 1000
        # Extract just the exception class name for a safe, informative message
        err_type = type(e).__name__
        return {
            "success": False,
            "message": f"Connection test failed: {err_type}. Check server logs for details.",
            "latency_ms": latency,
        }
