"""Add agent_mcp_fields_allowlist to server_config.

Revision ID: 013
"""

import sqlalchemy as sa

from alembic import op

revision = "013"
down_revision = "012"


def upgrade() -> None:
    op.add_column(
        "server_config",
        sa.Column(
            "agent_mcp_fields_allowlist",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("server_config", "agent_mcp_fields_allowlist")
