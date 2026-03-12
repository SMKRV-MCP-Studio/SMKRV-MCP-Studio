"""Initial schema — all tables.

Revision ID: 001_initial
Revises:
Create Date: 2025-01-01 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "connections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), unique=True, nullable=False, index=True),
        sa.Column("db_type", sa.String(20), nullable=False),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("port", sa.Integer, nullable=False, server_default="5432"),
        sa.Column("database", sa.String(255), nullable=False),
        sa.Column("username", sa.String(255), nullable=False),
        sa.Column("password_encrypted", sa.String(1024), nullable=False),
        sa.Column("ssl_mode", sa.String(20), server_default="prefer"),
        sa.Column("pool_min_size", sa.Integer, server_default="2"),
        sa.Column("pool_max_size", sa.Integer, server_default="10"),
        sa.Column("extra_params", sa.JSON, nullable=True),
        sa.Column("is_active", sa.Boolean, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "tools",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "connection_id",
            sa.String(36),
            sa.ForeignKey("connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), unique=True, nullable=False, index=True),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("sql_query", sa.Text, nullable=False),
        sa.Column("return_type", sa.String(50), server_default="list[dict]"),
        sa.Column("tags", sa.JSON, server_default="[]"),
        sa.Column("version", sa.String(20), server_default="1.0"),
        sa.Column("annotations", sa.JSON, server_default="{}"),
        sa.Column("cache_ttl", sa.Integer, server_default="0"),
        sa.Column("is_enabled", sa.Boolean, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "parameters",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tool_id",
            sa.String(36),
            sa.ForeignKey("tools.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("param_type", sa.String(20), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("is_required", sa.Boolean, server_default="1"),
        sa.Column("default_value", sa.String(1024), nullable=True),
        sa.Column("enum_values", sa.JSON, nullable=True),
        sa.Column("sort_order", sa.Integer, server_default="0"),
    )

    op.create_table(
        "resources",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "connection_id",
            sa.String(36),
            sa.ForeignKey("connections.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("name", sa.String(255), unique=True, nullable=False, index=True),
        sa.Column("uri_template", sa.String(1024), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("sql_query", sa.Text, nullable=True),
        sa.Column("static_content", sa.Text, nullable=True),
        sa.Column("mime_type", sa.String(100), server_default="application/json"),
        sa.Column("tags", sa.JSON, server_default="[]"),
        sa.Column("version", sa.String(20), server_default="1.0"),
        sa.Column("is_enabled", sa.Boolean, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "prompts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), unique=True, nullable=False, index=True),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("template", sa.Text, nullable=False),
        sa.Column("arguments", sa.JSON, server_default="[]"),
        sa.Column("tags", sa.JSON, server_default="[]"),
        sa.Column("version", sa.String(20), server_default="1.0"),
        sa.Column("is_enabled", sa.Boolean, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "server_config",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("server_name", sa.String(255), server_default="SMKRV Analytics MCP"),
        sa.Column("transport", sa.String(20), server_default="http"),
        sa.Column("host", sa.String(255), server_default="0.0.0.0"),
        sa.Column("port", sa.Integer, server_default="8080"),
        sa.Column("auth_type", sa.String(20), server_default="none"),
        sa.Column("auth_bearer_token", sa.String(1024), nullable=True),
        sa.Column("cors_origins", sa.JSON, server_default='["*"]'),
        sa.Column("otel_enabled", sa.Boolean, server_default="0"),
        sa.Column("log_level", sa.String(20), server_default="INFO"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("server_config")
    op.drop_table("prompts")
    op.drop_table("resources")
    op.drop_table("parameters")
    op.drop_table("tools")
    op.drop_table("connections")
