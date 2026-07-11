"""OAuthClient model — OAuth2 client credentials for agent MCP access."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.agent_session import AgentSession


class OAuthClient(TimestampMixin, Base):
    """OAuth2 client for AI agent access to Studio via MCP.

    Clients use the client_credentials grant to obtain access tokens
    with a configurable idle timeout (max 24 hours).
    """

    __tablename__ = "oauth_clients"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    client_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    client_secret_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    client_secret_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    idle_timeout_seconds: Mapped[int] = mapped_column(Integer, default=3600, nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    last_ip: Mapped[str | None] = mapped_column(String(45), nullable=True, default=None)
    last_country: Mapped[str | None] = mapped_column(String(2), nullable=True, default=None)
    scopes: Mapped[list] = mapped_column(JSON, default=lambda: ["*"])

    sessions: Mapped[list[AgentSession]] = relationship(
        "AgentSession", back_populates="oauth_client", cascade="all, delete-orphan"
    )
