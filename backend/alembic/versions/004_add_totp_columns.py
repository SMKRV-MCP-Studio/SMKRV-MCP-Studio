"""Add TOTP 2FA columns to admin_users.

Revision ID: 004_add_totp_columns
Revises: 003_version_to_integer
Create Date: 2026-02-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004_add_totp_columns"
down_revision: Union[str, None] = "003_version_to_integer"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("admin_users") as batch_op:
        batch_op.add_column(sa.Column("totp_secret_encrypted", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("totp_enabled", sa.Boolean(), nullable=False, server_default="0")
        )
        batch_op.add_column(sa.Column("recovery_codes_hash", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("admin_users") as batch_op:
        batch_op.drop_column("recovery_codes_hash")
        batch_op.drop_column("totp_enabled")
        batch_op.drop_column("totp_secret_encrypted")
