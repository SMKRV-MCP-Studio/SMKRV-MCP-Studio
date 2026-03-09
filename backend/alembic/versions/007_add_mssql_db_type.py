"""Add mssql to db_type check constraint.

Revision ID: 007_add_mssql
Revises: 006_add_oauth_config
Create Date: 2026-02-24 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op

revision: str = "007_add_mssql"
down_revision: Union[str, None] = "006_add_oauth_config"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("connections") as batch_op:
        batch_op.drop_constraint("ck_connections_db_type", type_="check")
        batch_op.create_check_constraint(
            "ck_connections_db_type",
            "db_type IN ('postgresql', 'clickhouse', 'mysql', 'cassandra', "
            "'greenplum', 'supabase', 'snowflake', 'bigquery', 'mssql')",
        )


def downgrade() -> None:
    with op.batch_alter_table("connections") as batch_op:
        batch_op.drop_constraint("ck_connections_db_type", type_="check")
        batch_op.create_check_constraint(
            "ck_connections_db_type",
            "db_type IN ('postgresql', 'clickhouse', 'mysql', 'cassandra', "
            "'greenplum', 'supabase', 'snowflake', 'bigquery')",
        )
