"""Add metrics_snapshots table for Redis fallback persistence.

Revision ID: 014
Revises: 013
Create Date: 2026-03-11
"""

import sqlalchemy as sa
from alembic import op

revision = "014"
down_revision = "013"


def upgrade() -> None:
    op.create_table(
        "metrics_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("date", sa.String(8), nullable=False),
        sa.Column("tool_name", sa.String(255), nullable=False, server_default="__all__"),
        sa.Column("calls", sa.Integer, nullable=False, server_default="0"),
        sa.Column("errors", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_duration_ms", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("tool_breakdown", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_snapshots_date_tool",
        "metrics_snapshots",
        ["date", "tool_name"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_snapshots_date_tool", table_name="metrics_snapshots")
    op.drop_table("metrics_snapshots")
