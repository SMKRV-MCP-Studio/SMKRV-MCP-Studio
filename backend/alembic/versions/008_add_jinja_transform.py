"""Add Jinja2 transform_template to tools and global_variables to server_config.

Revision ID: 008_add_jinja_transform
Revises: 007_add_mssql
Create Date: 2026-02-27 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "008_add_jinja_transform"
down_revision: Union[str, None] = "007_add_mssql"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("tools") as batch_op:
        batch_op.add_column(sa.Column("transform_template", sa.Text(), nullable=True))
    with op.batch_alter_table("server_config") as batch_op:
        batch_op.add_column(
            sa.Column("global_variables", sa.JSON(), nullable=True, server_default="{}")
        )


def downgrade() -> None:
    with op.batch_alter_table("tools") as batch_op:
        batch_op.drop_column("transform_template")
    with op.batch_alter_table("server_config") as batch_op:
        batch_op.drop_column("global_variables")
