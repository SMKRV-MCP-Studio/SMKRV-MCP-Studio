"""Add queue/semaphore settings to connections.

Revision ID: 002_add_queue_settings
Revises: 001_initial
Create Date: 2026-02-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002_add_queue_settings"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("connections") as batch_op:
        batch_op.add_column(
            sa.Column("max_concurrent_queries", sa.Integer, server_default="5")
        )
        batch_op.add_column(
            sa.Column("queue_timeout_seconds", sa.Integer, server_default="30")
        )
        batch_op.add_column(
            sa.Column("queue_enabled", sa.Boolean, server_default="1")
        )


def downgrade() -> None:
    with op.batch_alter_table("connections") as batch_op:
        batch_op.drop_column("queue_enabled")
        batch_op.drop_column("queue_timeout_seconds")
        batch_op.drop_column("max_concurrent_queries")
