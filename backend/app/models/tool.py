"""Tool model — SQL-based MCP tools."""

import uuid

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Tool(TimestampMixin, Base):
    """MCP tool backed by a SQL query."""

    __tablename__ = "tools"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    connection_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("connections.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    sql_query: Mapped[str] = mapped_column(Text, nullable=False)
    return_type: Mapped[str] = mapped_column(String(50), default="list[dict]")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)
    annotations: Mapped[dict] = mapped_column(JSON, default=dict)
    cache_ttl: Mapped[int] = mapped_column(Integer, default=0)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    transform_template: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    connection = relationship("Connection", back_populates="tools")
    parameters = relationship(
        "Parameter",
        back_populates="tool",
        cascade="all, delete-orphan",
        order_by="Parameter.sort_order",
    )
