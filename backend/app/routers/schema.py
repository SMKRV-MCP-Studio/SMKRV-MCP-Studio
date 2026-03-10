"""Database schema introspection endpoints."""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.connection import Connection
from app.services.crypto import decrypt, decrypt_sensitive_extra

router = APIRouter()
logger = logging.getLogger(__name__)


async def _get_connection_creds(connection_id: str, db: AsyncSession) -> Connection:
    """Get connection with decrypted credentials."""
    result = await db.execute(select(Connection).where(Connection.id == connection_id))
    connection = result.scalar_one_or_none()
    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")
    return connection


@router.get("/schema/{connection_id}/tables")
async def list_tables(
    connection_id: str, db: AsyncSession = Depends(get_db)
) -> list[dict]:
    """List all tables in the user's database."""
    connection = await _get_connection_creds(connection_id, db)
    password = decrypt(connection.password_encrypted)

    try:
        if connection.db_type == "postgresql":
            import asyncpg

            conn = await asyncpg.connect(
                host=connection.host,
                port=connection.port,
                database=connection.database,
                user=connection.username,
                password=password,
                timeout=10,
            )
            rows = await conn.fetch(
                "SELECT table_schema, table_name, table_type "
                "FROM information_schema.tables "
                "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
                "ORDER BY table_schema, table_name"
            )
            await conn.close()
            return [dict(r) for r in rows]

        elif connection.db_type == "clickhouse":
            import clickhouse_connect

            client = clickhouse_connect.get_client(
                host=connection.host,
                port=connection.port,
                database=connection.database,
                username=connection.username,
                password=password,
            )
            result = client.query(
                "SELECT database, name, engine "
                "FROM system.tables "
                "WHERE database = {db:String} "
                "ORDER BY name",
                parameters={"db": connection.database},
            )
            client.close()
            return [
                {"database": r[0], "table_name": r[1], "engine": r[2]}
                for r in result.result_rows
            ]

        elif connection.db_type == "mssql":
            import pymssql

            extra = decrypt_sensitive_extra(connection.extra_params) or {}
            _cs = extra.get("charset")
            charset = _cs if isinstance(_cs, str) and 0 < len(_cs) <= 30 else None
            loop = asyncio.get_event_loop()
            def _do_tables():
                kw = dict(
                    server=connection.host, port=connection.port,
                    database=connection.database, user=connection.username,
                    password=password, login_timeout=10,
                )
                if charset:
                    kw["charset"] = charset
                conn = pymssql.connect(**kw)
                cursor = conn.cursor(as_dict=True)
                cursor.execute(
                    "SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE "
                    "FROM INFORMATION_SCHEMA.TABLES "
                    "WHERE TABLE_TYPE = 'BASE TABLE' "
                    "ORDER BY TABLE_SCHEMA, TABLE_NAME"
                )
                rows = cursor.fetchall()
                cursor.close()
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

        raise HTTPException(status_code=400, detail=f"Unsupported db_type: {connection.db_type}")
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
        if connection.db_type == "postgresql":
            import asyncpg

            conn = await asyncpg.connect(
                host=connection.host,
                port=connection.port,
                database=connection.database,
                user=connection.username,
                password=password,
                timeout=10,
            )
            rows = await conn.fetch(
                "SELECT column_name, data_type, is_nullable, column_default "
                "FROM information_schema.columns "
                "WHERE table_name = $1 "
                "ORDER BY ordinal_position",
                table,
            )
            await conn.close()
            return [dict(r) for r in rows]

        elif connection.db_type == "clickhouse":
            import clickhouse_connect

            client = clickhouse_connect.get_client(
                host=connection.host,
                port=connection.port,
                database=connection.database,
                username=connection.username,
                password=password,
            )
            result = client.query(
                "SELECT name, type, default_kind, default_expression "
                "FROM system.columns "
                "WHERE database = {db:String} AND table = {tbl:String} "
                "ORDER BY position",
                parameters={"db": connection.database, "tbl": table},
            )
            client.close()
            return [
                {
                    "column_name": r[0],
                    "data_type": r[1],
                    "default_kind": r[2],
                    "default_expression": r[3],
                }
                for r in result.result_rows
            ]

        elif connection.db_type == "mssql":
            import pymssql

            extra = decrypt_sensitive_extra(connection.extra_params) or {}
            _cs = extra.get("charset")
            charset = _cs if isinstance(_cs, str) and 0 < len(_cs) <= 30 else None
            loop = asyncio.get_event_loop()
            def _do_columns():
                kw = dict(
                    server=connection.host, port=connection.port,
                    database=connection.database, user=connection.username,
                    password=password, login_timeout=10,
                )
                if charset:
                    kw["charset"] = charset
                conn = pymssql.connect(**kw)
                cursor = conn.cursor(as_dict=True)
                cursor.execute(
                    "SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT "
                    "FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_NAME = %s "
                    "ORDER BY ORDINAL_POSITION",
                    (table,),
                )
                rows = cursor.fetchall()
                cursor.close()
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

        raise HTTPException(status_code=400, detail=f"Unsupported db_type: {connection.db_type}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Schema introspection error: %s", e)
        raise HTTPException(
            status_code=502,
            detail="Database error. Check server logs for details.",
        ) from e
