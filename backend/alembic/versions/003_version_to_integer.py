"""Convert version columns from String to Integer.

Revision ID: 003_version_to_integer
Revises: 002_add_queue_settings
Create Date: 2026-02-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003_version_to_integer"
down_revision: Union[str, None] = "002_add_queue_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("tools", "resources", "prompts")


def upgrade() -> None:
    for table in _TABLES:
        # Extract major version number from strings like "1.0", "1.0.0", "2.0"
        op.execute(
            f"UPDATE {table} SET version = "
            f"CAST(SUBSTR(version, 1, INSTR(version || '.', '.') - 1) AS TEXT) "
            f"WHERE version LIKE '%.%'"
        )
        # Convert any remaining non-numeric values to '1'
        op.execute(
            f"UPDATE {table} SET version = '1' "
            f"WHERE CAST(version AS INTEGER) = 0 AND version != '0'"
        )
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column(
                "version",
                type_=sa.Integer,
                existing_type=sa.String(20),
                server_default="1",
                postgresql_using="version::integer",
            )


def downgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column(
                "version",
                type_=sa.String(20),
                existing_type=sa.Integer,
                server_default="1.0",
            )
        # Convert integers back to semver-ish strings
        op.execute(f"UPDATE {table} SET version = version || '.0'")
