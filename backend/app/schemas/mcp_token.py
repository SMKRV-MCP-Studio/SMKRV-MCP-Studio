"""Pydantic schemas for McpBearerToken entity."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class McpTokenCreate(BaseModel):
    """Schema for creating an MCP bearer token."""

    name: str = Field(..., min_length=1, max_length=255)
    idle_timeout_minutes: int | None = Field(
        default=None, ge=1, le=43200, description="Idle timeout in minutes (None = no timeout)"
    )


class McpTokenResponse(BaseModel):
    """Schema for returning an MCP bearer token (no plaintext)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    token_prefix: str
    idle_timeout_minutes: int | None = None
    revoked: bool
    last_used_at: datetime | None = None
    last_ip: str | None = None
    last_country: str | None = None
    created_at: datetime


class McpTokenCreated(McpTokenResponse):
    """One-time response after creating a token (includes plaintext)."""

    token: str


class McpTokenList(BaseModel):
    """Paginated list of MCP bearer tokens."""

    items: list[McpTokenResponse]
    total: int
