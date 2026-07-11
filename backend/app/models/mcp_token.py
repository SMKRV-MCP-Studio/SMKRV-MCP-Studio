"""McpBearerToken model — named bearer tokens for the generated MCP server."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class McpBearerToken(TimestampMixin, Base):
    """Named bearer token for authenticating requests to the generated MCP server.

    Tokens are long-lived (no expiry), but support idle timeout and manual revocation.
    The generated MCP server validates tokens via Redis lookup + bcrypt check.
    """

    __tablename__ = "mcp_bearer_tokens"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    idle_timeout_minutes: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=None
    )
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    last_ip: Mapped[str | None] = mapped_column(
        String(45), nullable=True, default=None
    )
    last_country: Mapped[str | None] = mapped_column(
        String(2), nullable=True, default=None
    )
