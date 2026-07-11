"""Pydantic schemas for AgentToken entity."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AgentTokenCreate(BaseModel):
    """Schema for creating an agent token."""

    name: str = Field(..., min_length=1, max_length=255)
    duration_minutes: int = Field(..., ge=15, le=10080)


class AgentTokenResponse(BaseModel):
    """Schema for returning an agent token (no plaintext)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    token_prefix: str
    expires_at: datetime
    revoked: bool
    last_used_at: datetime | None = None
    last_ip: str | None = None
    last_country: str | None = None
    scopes: list[str] = ["*"]
    created_at: datetime


class AgentTokenCreated(AgentTokenResponse):
    """One-time response after creating a token (includes plaintext)."""

    token: str


class AgentTokenList(BaseModel):
    """Paginated list of agent tokens."""

    items: list[AgentTokenResponse]
    total: int
