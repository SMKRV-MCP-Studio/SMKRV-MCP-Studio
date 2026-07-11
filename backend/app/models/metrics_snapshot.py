"""MetricsSnapshot model — daily metrics aggregates persisted from Redis."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Sentinel value for aggregate rows (SQLite treats NULL as distinct in unique indexes)
AGGREGATE_ALL = "__all__"


class MetricsSnapshot(Base):
    """Daily metrics snapshots persisted from Redis for fallback on data loss.

    Written periodically by the backend from Redis daily rollup keys.
    Read as fallback when MCP/Redis is unavailable.
    """

    __tablename__ = "metrics_snapshots"
    __table_args__ = (
        Index("ix_snapshots_date_tool", "date", "tool_name", unique=True),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # Date string "YYYYMMDD" matching Redis key smkrv:ts_d:YYYYMMDD
    date: Mapped[str] = mapped_column(String(8), nullable=False)
    # Per-tool row or AGGREGATE_ALL ("__all__") for totals
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False, default=AGGREGATE_ALL)
    calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    errors: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_duration_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    # JSON: per-tool breakdown {"tool:my_tool": 10, ...}
    tool_breakdown: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
