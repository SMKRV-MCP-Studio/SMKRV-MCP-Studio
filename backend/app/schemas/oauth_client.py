"""Pydantic schemas for OAuthClient entity and OAuth2 token exchange."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class OAuthClientCreate(BaseModel):
    """Schema for creating an OAuth2 client."""

    name: str = Field(..., min_length=1, max_length=255)
    idle_timeout_minutes: int = Field(..., ge=15, le=10080)


class OAuthClientResponse(BaseModel):
    """Schema for returning an OAuth2 client (no secret)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    client_id: str
    client_secret_prefix: str
    idle_timeout_seconds: int
    revoked: bool
    last_used_at: datetime | None = None
    last_ip: str | None = None
    last_country: str | None = None
    scopes: list[str] = ["*"]
    created_at: datetime


class OAuthClientCreated(OAuthClientResponse):
    """One-time response after creating a client (includes plaintext secret)."""

    client_secret: str


class OAuthClientList(BaseModel):
    """Paginated list of OAuth2 clients."""

    items: list[OAuthClientResponse]
    total: int


# ---------------------------------------------------------------------------
# OAuth2 token exchange schemas
# ---------------------------------------------------------------------------

class TokenRequest(BaseModel):
    """OAuth2 token request (client_credentials or refresh_token grant)."""

    grant_type: str = Field(..., pattern=r"^(client_credentials|refresh_token)$")
    client_id: str | None = None
    client_secret: str | None = None
    refresh_token: str | None = None
    scope: str | None = None


class TokenResponse(BaseModel):
    """OAuth2 token response."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    scope: str = "*"


class IntrospectionRequest(BaseModel):
    """RFC 7662 token introspection request."""

    token: str


class IntrospectionResponse(BaseModel):
    """RFC 7662 token introspection response."""

    active: bool
    client_id: str | None = None
    scope: str | None = None
    exp: int | None = None
    iat: int | None = None
    token_type: str | None = None


# ---------------------------------------------------------------------------
# Agent activity log
# ---------------------------------------------------------------------------

class AgentActivity(BaseModel):
    """Single agent activity log entry."""

    timestamp: str
    token_prefix: str
    tool_name: str
    ip: str
    success: bool


class AgentActivityList(BaseModel):
    """Paginated list of agent activity entries."""

    items: list[AgentActivity]
    total: int
