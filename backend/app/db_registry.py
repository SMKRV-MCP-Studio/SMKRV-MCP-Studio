"""Centralized database type registry — single source of truth for all supported DB types."""

from __future__ import annotations

import re as _re

DB_TYPES: dict[str, dict] = {
    "postgresql": {
        "label": "PostgreSQL",
        "port": 5432,
        "driver": "asyncpg",
        "param_style": "positional",
        "category": "sql",
    },
    "clickhouse": {
        "label": "ClickHouse",
        "port": 8123,
        "driver": "clickhouse-connect",
        "param_style": "named_pct",
        "category": "sql",
    },
    "mysql": {
        "label": "MySQL / MariaDB",
        "port": 3306,
        "driver": "aiomysql",
        "param_style": "named_pct",
        "category": "sql",
    },
    "cassandra": {
        "label": "Cassandra / ScyllaDB",
        "port": 9042,
        "driver": "cassandra-driver",
        "param_style": "named_pct",
        "category": "cql",
    },
    "greenplum": {
        "label": "Greenplum",
        "port": 5432,
        "driver": "asyncpg",
        "param_style": "positional",
        "category": "sql",
    },
    "supabase": {
        "label": "Supabase",
        "port": 5432,
        "driver": "asyncpg",
        "param_style": "positional",
        "category": "sql",
    },
    "snowflake": {
        "label": "Snowflake",
        "port": None,
        "driver": "snowflake-connector-python",
        "param_style": "named_pct",
        "category": "cloud_sql",
    },
    "bigquery": {
        "label": "Google BigQuery",
        "port": None,
        "driver": "google-cloud-bigquery",
        "param_style": "named_at",
        "category": "cloud_sql",
    },
    "mssql": {
        "label": "Microsoft SQL Server",
        "port": 1433,
        "driver": "pymssql",
        "param_style": "named_pct",
        "category": "sql",
    },
}

DB_TYPE_PATTERN: str = "^(" + "|".join(DB_TYPES.keys()) + ")$"

# DB types that use asyncpg (PostgreSQL wire protocol)
PG_COMPATIBLE_TYPES: set[str] = {"postgresql", "greenplum", "supabase"}

# Extra fields stored in extra_params JSON per db_type
EXTRA_FIELDS: dict[str, list[str]] = {
    "snowflake": ["account", "warehouse", "schema", "role"],
    "bigquery": ["project_id", "dataset", "credentials_json"],
    "cassandra": ["keyspace"],
    "supabase": ["project_ref"],
}

# Required extra_params fields per db_type
REQUIRED_EXTRA_FIELDS: dict[str, list[str]] = {
    "snowflake": ["account"],
    "bigquery": ["project_id", "credentials_json"],
    "cassandra": ["keyspace"],
}

# Sensitive extra_params fields that must be encrypted at rest (Fernet).
# Values are Fernet-encrypted with "__enc__:" prefix in the JSON column.
SENSITIVE_EXTRA_FIELDS: frozenset[str] = frozenset({"credentials_json"})


def pg_ssl_context(ssl_mode: str | None):
    """Convert ssl_mode string to asyncpg ``ssl`` parameter value.

    - ``require``: returns ``True`` (encrypted, no cert verification)
    - ``verify-ca``: returns ``ssl.SSLContext`` with CA verification (no hostname check)
    - ``verify-full``: returns ``ssl.SSLContext`` with CA + hostname verification
    - ``disable``: returns ``False``
    - ``prefer`` / ``None``: returns ``None`` (asyncpg default — try SSL, fall back)
    """
    import ssl

    mode = (ssl_mode or "prefer").lower()
    if mode == "verify-full":
        return ssl.create_default_context()
    if mode == "verify-ca":
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        return ctx
    if mode == "require":
        return True
    if mode == "disable":
        return False
    return None  # "prefer" — asyncpg default


_CHARSET_RE = _re.compile(r"^[a-zA-Z0-9_-]{1,30}$")


def validate_charset(extra: dict | None) -> str | None:
    """Extract and validate charset from extra_params.

    Returns the charset string if valid (alphanumeric + hyphens/underscores,
    1–30 chars), else ``None``.
    """
    _cs = (extra or {}).get("charset")
    if isinstance(_cs, str) and _CHARSET_RE.match(_cs):
        return _cs
    return None
