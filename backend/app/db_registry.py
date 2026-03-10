"""Centralized database type registry — single source of truth for all supported DB types."""

from __future__ import annotations

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
