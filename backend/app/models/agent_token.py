"""AgentToken model — temporary bearer tokens for agent MCP access."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class AgentToken(TimestampMixin, Base):
    """Temporary bearer token for AI agent access to Studio via MCP.

    Tokens are temporary (max 7 days), created by the admin in the UI,
    and used by agents in the Authorization header.
    """

    __tablename__ = "agent_tokens"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    expires_at: Mapped["datetime"] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_used_at: Mapped["datetime | None"] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    last_ip: Mapped[str | None] = mapped_column(String(45), nullable=True, default=None)
    last_country: Mapped[str | None] = mapped_column(String(2), nullable=True, default=None)
    scopes: Mapped[list] = mapped_column(JSON, default=lambda: ["*"])
