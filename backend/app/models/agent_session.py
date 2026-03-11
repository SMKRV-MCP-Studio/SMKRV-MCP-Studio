"""AgentSession model — active OAuth2 sessions for agent MCP access."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.oauth_client import OAuthClient


class AgentSession(TimestampMixin, Base):
    """Active OAuth2 session with sliding-window expiry.

    Created when an agent exchanges client credentials for an access token.
    Each successful request extends the session TTL up to idle_timeout_seconds.
    """

    __tablename__ = "agent_sessions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    oauth_client_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("oauth_clients.id", ondelete="CASCADE"), nullable=False
    )
    access_token_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    refresh_token_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    client_ip: Mapped[str | None] = mapped_column(String(45), nullable=True, default=None)
    client_country: Mapped[str | None] = mapped_column(String(2), nullable=True, default=None)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    oauth_client: Mapped[OAuthClient] = relationship(
        "OAuthClient", back_populates="sessions"
    )
