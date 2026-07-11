"""Add injection_blocked to change_history action CHECK constraint.

Revision ID: 010
"""

from alembic import op

revision = "010"
down_revision = "009"


def upgrade() -> None:
    # SQLite ignores CHECK constraints at runtime, but for consistency
    # and for future Postgres support, drop and recreate the constraint.
    with op.batch_alter_table("change_history") as batch_op:
        batch_op.drop_constraint("ck_change_history_action", type_="check")
        batch_op.create_check_constraint(
            "ck_change_history_action",
            "action IN ('create', 'update', 'delete', 'rollback', 'import', 'injection_blocked')",
        )


def downgrade() -> None:
    with op.batch_alter_table("change_history") as batch_op:
        batch_op.drop_constraint("ck_change_history_action", type_="check")
        batch_op.create_check_constraint(
            "ck_change_history_action",
            "action IN ('create', 'update', 'delete', 'rollback', 'import')",
        )
