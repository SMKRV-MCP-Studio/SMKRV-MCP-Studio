"""ChangeHistory model — audit trail for entity mutations."""

import uuid

from sqlalchemy import JSON, CheckConstraint, DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ChangeHistory(Base):
    """Stores snapshots of entity state before each mutation."""

    __tablename__ = "change_history"
    __table_args__ = (
        CheckConstraint(
            "action IN ('create', 'update', 'delete', 'rollback', 'import', 'injection_blocked')",
            name="ck_change_history_action",
        ),
        Index("ix_change_history_entity", "entity_type", "entity_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    entity_type: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )  # tool, resource, prompt, connection, server_config
    entity_id: Mapped[str] = mapped_column(
        String(36), nullable=False, index=True
    )
    entity_name: Mapped[str] = mapped_column(
        String(255), nullable=False, default=""
    )
    action: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # create, update, delete
    snapshot: Mapped[dict | None] = mapped_column(
        JSON, nullable=True
    )  # full entity state BEFORE the change (null for create)
    changes: Mapped[dict | None] = mapped_column(
        JSON, nullable=True
    )  # {field: {old, new}} for updates; null for create/delete
    created_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
