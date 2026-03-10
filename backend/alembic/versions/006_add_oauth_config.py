"""Add OAuth2 auth columns to server_config.

Revision ID: 006_add_oauth_config
Revises: 005_add_agent_access_tables
Create Date: 2026-02-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "006_add_oauth_config"
down_revision: Union[str, None] = "005_add_agent_access_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("server_config") as batch_op:
        # Widen auth_type to accommodate oauth_credentials / oauth_introspection
        batch_op.alter_column("auth_type", type_=sa.String(30))

        # OAuth2 Client Credentials (self-contained)
        batch_op.add_column(
            sa.Column("oauth_clients_json", sa.String(4096), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "oauth_token_ttl_seconds",
                sa.Integer(),
                nullable=False,
                server_default="3600",
            )
        )

        # OAuth2 Token Introspection (external)
        batch_op.add_column(
            sa.Column("oauth_introspection_url", sa.String(1024), nullable=True)
        )
        batch_op.add_column(
            sa.Column("oauth_introspection_client_id", sa.String(255), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "oauth_introspection_client_secret", sa.String(1024), nullable=True
            )
        )
        batch_op.add_column(
            sa.Column(
                "oauth_introspection_cache_seconds",
                sa.Integer(),
                nullable=False,
                server_default="60",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("server_config") as batch_op:
        batch_op.drop_column("oauth_introspection_cache_seconds")
        batch_op.drop_column("oauth_introspection_client_secret")
        batch_op.drop_column("oauth_introspection_client_id")
        batch_op.drop_column("oauth_introspection_url")
        batch_op.drop_column("oauth_token_ttl_seconds")
        batch_op.drop_column("oauth_clients_json")
        batch_op.alter_column("auth_type", type_=sa.String(20))
