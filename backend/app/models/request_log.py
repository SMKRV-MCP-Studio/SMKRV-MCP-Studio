"""RequestLog model — individual tool execution log entries."""

import uuid

from sqlalchemy import Boolean, DateTime, Float, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class RequestLog(Base):
    """Stores individual tool execution log entries ingested from Redis pub/sub."""

    __tablename__ = "request_logs"
    __table_args__ = (
        Index("ix_request_logs_created", "created_at"),
        Index("ix_request_logs_tool", "tool_name"),
        Index("ix_request_logs_conn", "connection_id"),
        Index("ix_request_logs_success", "success"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    connection_id: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
