"""Database schema introspection endpoints."""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.db_registry import PG_COMPATIBLE_TYPES, pg_ssl_context, validate_charset
from app.models.connection import Connection
from app.services.crypto import decrypt, decrypt_sensitive_extra

router = APIRouter()
logger = logging.getLogger(__name__)

_SCHEMA_TIMEOUT_SECONDS = 15


async def _get_connection_creds(connection_id: str, db: AsyncSession) -> Connection:
    """Get connection with decrypted credentials."""
    result = await db.execute(select(Connection).where(Connection.id == connection_id))
    connection = result.scalar_one_or_none()
    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")
    return connection


async def _introspect_tables(connection, password: str) -> list[dict]:
    """Execute table introspection for the given connection type."""
    if connection.db_type in PG_COMPATIBLE_TYPES:
        import asyncpg

        _pg_kw = dict(
            host=connection.host,
            port=connection.port,
            database=connection.database,
            user=connection.username,
            password=password,
            timeout=10,
        )
        _ssl = pg_ssl_context(connection.ssl_mode)
        if _ssl is not None:
            _pg_kw["ssl"] = _ssl
        conn = await asyncpg.connect(**_pg_kw)
        try:
            rows = await conn.fetch(
                "SELECT table_schema, table_name, table_type "
                "FROM information_schema.tables "
                "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
                "ORDER BY table_schema, table_name"
            )
        finally:
            await conn.close()
        return [dict(r) for r in rows]

    if connection.db_type == "clickhouse":
        import clickhouse_connect

        loop = asyncio.get_running_loop()
        def _do_ch_tables():
            client = clickhouse_connect.get_client(
                host=connection.host,
                port=connection.port,
                database=connection.database,
                username=connection.username,
                password=password,
                connect_timeout=10,
                send_receive_timeout=10,
            )
            try:
                result = client.query(
                    "SELECT database, name, engine "
                    "FROM system.tables "
                    "WHERE database = {db:String} "
                    "ORDER BY name",
                    parameters={"db": connection.database},
                )
                return [
                    {"database": r[0], "table_name": r[1], "engine": r[2]}
                    for r in result.result_rows
                ]
            finally:
                client.close()
        return await loop.run_in_executor(None, _do_ch_tables)

    if connection.db_type == "mssql":
        import pymssql

        extra = decrypt_sensitive_extra(connection.extra_params) or {}
        charset = validate_charset(extra)
        loop = asyncio.get_running_loop()
        def _do_tables():
            kw = dict(
                server=connection.host, port=connection.port,
                database=connection.database, user=connection.username,
                password=password, login_timeout=10, timeout=10,
            )
            if charset:
                kw["charset"] = charset
            conn = pymssql.connect(**kw)
            try:
                cursor = conn.cursor(as_dict=True)
                try:
                    cursor.execute(
                        "SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE "
                        "FROM INFORMATION_SCHEMA.TABLES "
                        "WHERE TABLE_TYPE = 'BASE TABLE' "
                        "ORDER BY TABLE_SCHEMA, TABLE_NAME"
                    )
                    rows = cursor.fetchall()
                finally:
                    cursor.close()
            finally:
                conn.close()
            return [
                {
                    "table_schema": r["TABLE_SCHEMA"],
                    "table_name": r["TABLE_NAME"],
                    "table_type": r["TABLE_TYPE"],
                }
                for r in rows
            ]
        return await loop.run_in_executor(None, _do_tables)

    if connection.db_type == "mysql":
        import aiomysql

        extra = decrypt_sensitive_extra(connection.extra_params) or {}
        charset = validate_charset(extra)
        _mysql_kw = dict(
            host=connection.host,
            port=connection.port,
            db=connection.database,
            user=connection.username,
            password=password,
            connect_timeout=10,
        )
        if charset:
            _mysql_kw["charset"] = charset
        conn = await aiomysql.connect(**_mysql_kw)
        try:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT TABLE_SCHEMA as table_schema, TABLE_NAME as table_name, "
                    "TABLE_TYPE as table_type "
                    "FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME",
                    (connection.database,),
                )
                return list(await cur.fetchall())
        finally:
            conn.close()

    if connection.db_type == "cassandra":
        from cassandra.auth import PlainTextAuthProvider
        from cassandra.cluster import Cluster

        extra = decrypt_sensitive_extra(connection.extra_params) or {}
        keyspace = extra.get("keyspace", "system")
        loop = asyncio.get_running_loop()

        def _do_cql_tables():
            auth = (
                PlainTextAuthProvider(username=connection.username, password=password)
                if connection.username
                else None
            )
            cluster = Cluster(
                [connection.host],
                port=connection.port,
                auth_provider=auth,
                connect_timeout=10,
                control_connection_timeout=10,
            )
            try:
                session = cluster.connect()
                try:
                    result = session.execute(
                        "SELECT table_name FROM system_schema.tables "
                        "WHERE keyspace_name = %s",
                        (keyspace,),
                    )
                    return [
                        {
                            "table_schema": keyspace,
                            "table_name": row.table_name,
                            "table_type": "BASE TABLE",
                        }
                        for row in result
                    ]
                finally:
                    session.shutdown()
            finally:
                cluster.shutdown()

        return await loop.run_in_executor(None, _do_cql_tables)

    if connection.db_type == "snowflake":
        import snowflake.connector

        extra = decrypt_sensitive_extra(connection.extra_params) or {}
        loop = asyncio.get_running_loop()

        def _do_sf_tables():
            conn = snowflake.connector.connect(
                account=extra.get("account", ""),
                user=connection.username,
                password=password,
                warehouse=extra.get("warehouse", ""),
                database=connection.database or extra.get("database", ""),
                schema=extra.get("schema", "PUBLIC"),
                role=extra.get("role", ""),
                login_timeout=10,
                network_timeout=30,
            )
            try:
                cur = conn.cursor(snowflake.connector.DictCursor)
                try:
                    cur.execute(
                        "SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE "
                        "FROM INFORMATION_SCHEMA.TABLES "
                        "WHERE TABLE_SCHEMA NOT IN ('INFORMATION_SCHEMA') "
                        "ORDER BY TABLE_SCHEMA, TABLE_NAME"
                    )
                    return [
                        {
                            "table_schema": r["TABLE_SCHEMA"],
                            "table_name": r["TABLE_NAME"],
                            "table_type": r["TABLE_TYPE"],
                        }
                        for r in cur.fetchall()
                    ]
                finally:
                    cur.close()
            finally:
                conn.close()

        return await loop.run_in_executor(None, _do_sf_tables)

    if connection.db_type == "bigquery":
        import json as _json

        from google.cloud import bigquery
        from google.oauth2 import service_account

        extra = decrypt_sensitive_extra(connection.extra_params) or {}
        loop = asyncio.get_running_loop()

        def _do_bq_tables():
            creds_json = extra.get("credentials_json", "{}")
            creds_info = _json.loads(creds_json) if isinstance(creds_json, str) else creds_json
            credentials = service_account.Credentials.from_service_account_info(creds_info)
            client = bigquery.Client(
                project=extra.get("project_id", ""),
                credentials=credentials,
            )
            try:
                dataset = extra.get("dataset", "")
                if not dataset:
                    return []
                tables = list(
                    client.list_tables(f"{extra.get('project_id', '')}.{dataset}")
                )
                return [
                    {
                        "table_schema": dataset,
                        "table_name": t.table_id,
                        "table_type": t.table_type or "TABLE",
                    }
                    for t in tables
                ]
            finally:
                client.close()

        return await loop.run_in_executor(None, _do_bq_tables)

    raise HTTPException(status_code=400, detail=f"Unsupported db_type: {connection.db_type}")


async def _introspect_columns(connection, password: str, table: str) -> list[dict]:
    """Execute column introspection for the given connection type."""
    if connection.db_type in PG_COMPATIBLE_TYPES:
        import asyncpg

        _pg_kw = dict(
            host=connection.host,
            port=connection.port,
            database=connection.database,
            user=connection.username,
            password=password,
            timeout=10,
        )
        _ssl = pg_ssl_context(connection.ssl_mode)
        if _ssl is not None:
            _pg_kw["ssl"] = _ssl
        conn = await asyncpg.connect(**_pg_kw)
        try:
            rows = await conn.fetch(
                "SELECT column_name, data_type, is_nullable, column_default "
                "FROM information_schema.columns "
                "WHERE table_name = $1 "
                "AND table_schema NOT IN ('pg_catalog', 'information_schema') "
                "ORDER BY ordinal_position",
                table,
            )
        finally:
            await conn.close()
        return [dict(r) for r in rows]

    if connection.db_type == "clickhouse":
        import clickhouse_connect

        loop = asyncio.get_running_loop()
        def _do_ch_columns():
            client = clickhouse_connect.get_client(
                host=connection.host,
                port=connection.port,
                database=connection.database,
                username=connection.username,
                password=password,
                connect_timeout=10,
                send_receive_timeout=10,
            )
            try:
                result = client.query(
                    "SELECT name, type, default_kind, default_expression "
                    "FROM system.columns "
                    "WHERE database = {db:String} AND table = {tbl:String} "
                    "ORDER BY position",
                    parameters={"db": connection.database, "tbl": table},
                )
                return [
                    {
                        "column_name": r[0],
                        "data_type": r[1],
                        "default_kind": r[2],
                        "default_expression": r[3],
                    }
                    for r in result.result_rows
                ]
            finally:
                client.close()
        return await loop.run_in_executor(None, _do_ch_columns)

    if connection.db_type == "mssql":
        import pymssql

        extra = decrypt_sensitive_extra(connection.extra_params) or {}
        charset = validate_charset(extra)
        loop = asyncio.get_running_loop()
        def _do_columns():
            kw = dict(
                server=connection.host, port=connection.port,
                database=connection.database, user=connection.username,
                password=password, login_timeout=10, timeout=10,
            )
            if charset:
                kw["charset"] = charset
            conn = pymssql.connect(**kw)
            try:
                cursor = conn.cursor(as_dict=True)
                try:
                    cursor.execute(
                        "SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT "
                        "FROM INFORMATION_SCHEMA.COLUMNS "
                        "WHERE TABLE_NAME = %s "
                        "AND TABLE_SCHEMA NOT IN ('sys', 'INFORMATION_SCHEMA') "
                        "ORDER BY ORDINAL_POSITION",
                        (table,),
                    )
                    rows = cursor.fetchall()
                finally:
                    cursor.close()
            finally:
                conn.close()
            return [
                {
                    "column_name": r["COLUMN_NAME"],
                    "data_type": r["DATA_TYPE"],
                    "is_nullable": r["IS_NULLABLE"],
                    "column_default": r["COLUMN_DEFAULT"],
                }
                for r in rows
            ]
        return await loop.run_in_executor(None, _do_columns)

    if connection.db_type == "mysql":
        import aiomysql

        extra = decrypt_sensitive_extra(connection.extra_params) or {}
        charset = validate_charset(extra)
        _mysql_kw = dict(
            host=connection.host,
            port=connection.port,
            db=connection.database,
            user=connection.username,
            password=password,
            connect_timeout=10,
        )
        if charset:
            _mysql_kw["charset"] = charset
        conn = await aiomysql.connect(**_mysql_kw)
        try:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT COLUMN_NAME as column_name, DATA_TYPE as data_type, "
                    "IS_NULLABLE as is_nullable, COLUMN_DEFAULT as column_default "
                    "FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
                    "ORDER BY ORDINAL_POSITION",
                    (connection.database, table),
                )
                return list(await cur.fetchall())
        finally:
            conn.close()

    if connection.db_type == "cassandra":
        from cassandra.auth import PlainTextAuthProvider
        from cassandra.cluster import Cluster

        extra = decrypt_sensitive_extra(connection.extra_params) or {}
        keyspace = extra.get("keyspace", "system")
        loop = asyncio.get_running_loop()

        def _do_cql_columns():
            auth = (
                PlainTextAuthProvider(username=connection.username, password=password)
                if connection.username
                else None
            )
            cluster = Cluster(
                [connection.host],
                port=connection.port,
                auth_provider=auth,
                connect_timeout=10,
                control_connection_timeout=10,
            )
            try:
                session = cluster.connect()
                try:
                    result = session.execute(
                        "SELECT column_name, type FROM system_schema.columns "
                        "WHERE keyspace_name = %s AND table_name = %s",
                        (keyspace, table),
                    )
                    return [
                        {
                            "column_name": row.column_name,
                            "data_type": row.type,
                            "is_nullable": "YES",
                            "column_default": None,
                        }
                        for row in result
                    ]
                finally:
                    session.shutdown()
            finally:
                cluster.shutdown()

        return await loop.run_in_executor(None, _do_cql_columns)

    if connection.db_type == "snowflake":
        import snowflake.connector

        extra = decrypt_sensitive_extra(connection.extra_params) or {}
        loop = asyncio.get_running_loop()

        def _do_sf_columns():
            conn = snowflake.connector.connect(
                account=extra.get("account", ""),
                user=connection.username,
                password=password,
                warehouse=extra.get("warehouse", ""),
                database=connection.database or extra.get("database", ""),
                schema=extra.get("schema", "PUBLIC"),
                role=extra.get("role", ""),
                login_timeout=10,
                network_timeout=30,
            )
            try:
                cur = conn.cursor(snowflake.connector.DictCursor)
                try:
                    cur.execute(
                        "SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT "
                        "FROM INFORMATION_SCHEMA.COLUMNS "
                        "WHERE TABLE_NAME = %s AND TABLE_SCHEMA = %s "
                        "ORDER BY ORDINAL_POSITION",
                        (table, extra.get("schema", "PUBLIC")),
                    )
                    return [
                        {
                            "column_name": r["COLUMN_NAME"],
                            "data_type": r["DATA_TYPE"],
                            "is_nullable": r["IS_NULLABLE"],
                            "column_default": r["COLUMN_DEFAULT"],
                        }
                        for r in cur.fetchall()
                    ]
                finally:
                    cur.close()
            finally:
                conn.close()

        return await loop.run_in_executor(None, _do_sf_columns)

    if connection.db_type == "bigquery":
        import json as _json

        from google.cloud import bigquery
        from google.oauth2 import service_account

        extra = decrypt_sensitive_extra(connection.extra_params) or {}
        loop = asyncio.get_running_loop()

        def _do_bq_columns():
            creds_json = extra.get("credentials_json", "{}")
            creds_info = _json.loads(creds_json) if isinstance(creds_json, str) else creds_json
            credentials = service_account.Credentials.from_service_account_info(creds_info)
            client = bigquery.Client(
                project=extra.get("project_id", ""),
                credentials=credentials,
            )
            try:
                dataset = extra.get("dataset", "")
                table_ref = client.get_table(
                    f"{extra.get('project_id', '')}.{dataset}.{table}"
                )
                return [
                    {
                        "column_name": field.name,
                        "data_type": field.field_type,
                        "is_nullable": "YES" if field.is_nullable else "NO",
                        "column_default": None,
                    }
                    for field in table_ref.schema
                ]
            finally:
                client.close()

        return await loop.run_in_executor(None, _do_bq_columns)

    raise HTTPException(status_code=400, detail=f"Unsupported db_type: {connection.db_type}")


@router.get("/schema/{connection_id}/tables")
async def list_tables(
    connection_id: str, db: AsyncSession = Depends(get_db)
) -> list[dict]:
    """List all tables in the user's database."""
    connection = await _get_connection_creds(connection_id, db)
    password = decrypt(connection.password_encrypted)

    try:
        return await asyncio.wait_for(
            _introspect_tables(connection, password),
            timeout=_SCHEMA_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"Schema introspection timed out after {_SCHEMA_TIMEOUT_SECONDS}s",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Schema introspection error: %s", e)
        raise HTTPException(
            status_code=502,
            detail="Database error. Check server logs for details.",
        ) from e


@router.get("/schema/{connection_id}/tables/{table}/columns")
async def list_columns(
    connection_id: str, table: str, db: AsyncSession = Depends(get_db)
) -> list[dict]:
    """List columns of a specific table."""
    connection = await _get_connection_creds(connection_id, db)
    password = decrypt(connection.password_encrypted)

    try:
        return await asyncio.wait_for(
            _introspect_columns(connection, password, table),
            timeout=_SCHEMA_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"Schema introspection timed out after {_SCHEMA_TIMEOUT_SECONDS}s",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Schema introspection error: %s", e)
        raise HTTPException(
            status_code=502,
            detail="Database error. Check server logs for details.",
        ) from e
