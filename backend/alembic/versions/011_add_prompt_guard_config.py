"""Add prompt guard configuration columns to server_config.

Revision ID: 011
"""

import sqlalchemy as sa
from alembic import op

revision = "011"
down_revision = "010"


def upgrade() -> None:
    with op.batch_alter_table("server_config") as batch_op:
        batch_op.add_column(sa.Column("prompt_guard_enabled", sa.Boolean(), server_default="1"))
        batch_op.add_column(sa.Column("prompt_guard_l0_enabled", sa.Boolean(), server_default="1"))
        batch_op.add_column(sa.Column("prompt_guard_l1_enabled", sa.Boolean(), server_default="1"))
        batch_op.add_column(sa.Column("prompt_guard_l0_entity_types", sa.JSON(), server_default='["tool","prompt","resource"]'))
        batch_op.add_column(sa.Column("prompt_guard_l1_entity_types", sa.JSON(), server_default='["tool","prompt","resource"]'))
        batch_op.add_column(sa.Column("prompt_guard_block_severity", sa.String(20), server_default="'HIGH'"))
        batch_op.add_column(sa.Column("prompt_guard_ml_threshold", sa.Float(), server_default="0.5"))
        batch_op.add_column(sa.Column("prompt_guard_custom_patterns", sa.JSON(), server_default="[]"))
        batch_op.add_column(sa.Column("prompt_guard_disabled_patterns", sa.JSON(), server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("server_config") as batch_op:
        batch_op.drop_column("prompt_guard_disabled_patterns")
        batch_op.drop_column("prompt_guard_custom_patterns")
        batch_op.drop_column("prompt_guard_ml_threshold")
        batch_op.drop_column("prompt_guard_block_severity")
        batch_op.drop_column("prompt_guard_l1_entity_types")
        batch_op.drop_column("prompt_guard_l0_entity_types")
        batch_op.drop_column("prompt_guard_l1_enabled")
        batch_op.drop_column("prompt_guard_l0_enabled")
        batch_op.drop_column("prompt_guard_enabled")
