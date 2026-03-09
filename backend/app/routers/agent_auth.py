"""Internal agent auth endpoints — used by agent-mcp container only.

These endpoints handle OAuth2 token exchange and introspection.
They require the X-Agent-Service-Token header for authentication.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_agent_or_admin
from app.services.agent_auth import (
    exchange_credentials,
    introspect_token,
    refresh_access_token,
)

router = APIRouter()


# --- Schemas ---


class TokenExchangeRequest(BaseModel):
    """Body for /agent-auth/token endpoint."""

    grant_type: str = Field(..., pattern=r"^(client_credentials|refresh_token)$")
    client_id: str | None = Field(default=None, max_length=255)
    client_secret: str | None = Field(default=None, max_length=1024)
    refresh_token: str | None = Field(default=None, max_length=1024)
    client_ip: str | None = Field(default=None, max_length=45)
    client_country: str | None = Field(default=None, max_length=2)


class IntrospectRequest(BaseModel):
    """Body for /agent-auth/introspect endpoint."""

    token: str = Field(default="", max_length=2048)


# --- Endpoints ---


@router.post("/agent-auth/token")
async def token_exchange(
    body: TokenExchangeRequest,
    _admin=Depends(get_agent_or_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """OAuth2 token exchange — client_credentials or refresh_token grant.

    Called internally by the agent-mcp container.
    """
    if body.grant_type == "client_credentials":
        if not body.client_id or not body.client_secret:
            raise HTTPException(status_code=400, detail="client_id and client_secret required")

        result = await exchange_credentials(
            db, body.client_id, body.client_secret, body.client_ip, body.client_country,
        )
        if result is None:
            raise HTTPException(status_code=401, detail="Invalid client credentials")

        access_token, refresh_token, expires_in = result
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "expires_in": expires_in,
            "scope": "*",
        }

    elif body.grant_type == "refresh_token":
        if not body.refresh_token:
            raise HTTPException(status_code=400, detail="refresh_token required")

        result = await refresh_access_token(
            db, body.refresh_token, body.client_ip, body.client_country,
        )
        if result is None:
            raise HTTPException(status_code=401, detail="Invalid refresh token")

        access_token, refresh_token, expires_in = result
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "expires_in": expires_in,
            "scope": "*",
        }

    else:
        raise HTTPException(status_code=400, detail="Unsupported grant_type")


@router.post("/agent-auth/introspect")
async def token_introspect(
    body: IntrospectRequest,
    _admin=Depends(get_agent_or_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """RFC 7662 token introspection — called internally by agent-mcp."""
    return await introspect_token(db, body.token)
