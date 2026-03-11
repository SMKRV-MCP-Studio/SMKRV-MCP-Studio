"""Add country columns for GeoIP tracking.

Revision ID: 009
"""

import sqlalchemy as sa
from alembic import op

revision = "009"
down_revision = "008_add_jinja_transform"


def upgrade() -> None:
    op.add_column("server_config", sa.Column("auth_bearer_last_country", sa.String(2), nullable=True))
    op.add_column("agent_tokens", sa.Column("last_country", sa.String(2), nullable=True))
    op.add_column("oauth_clients", sa.Column("last_country", sa.String(2), nullable=True))
    op.add_column("agent_sessions", sa.Column("client_country", sa.String(2), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_sessions", "client_country")
    op.drop_column("oauth_clients", "last_country")
    op.drop_column("agent_tokens", "last_country")
    op.drop_column("server_config", "auth_bearer_last_country")
