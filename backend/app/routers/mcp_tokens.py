"""CRUD endpoints for MCP bearer tokens (generated MCP server, port 8080)."""

import logging
import secrets
from typing import Annotated

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.mcp_token import McpBearerToken
from app.schemas.mcp_token import (
    McpTokenCreate,
    McpTokenCreated,
    McpTokenList,
    McpTokenResponse,
)
from app.services.auth import _BCRYPT_ROUNDS
from app.services.mcp_token_sync import (
    _remove_token_from_redis,
    _sync_mcp_token_usage_from_redis,
    _sync_token_to_redis,
    sync_all_mcp_tokens_to_redis,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_TOKEN_PREFIX = "mcp_"

# Re-export for backward compatibility (deployer, tests may import from here)
__all__ = [
    "_sync_token_to_redis",
    "_sync_mcp_token_usage_from_redis",
    "_remove_token_from_redis",
    "sync_all_mcp_tokens_to_redis",
]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/mcp-tokens", response_model=McpTokenCreated, status_code=201)
async def create_mcp_token(data: McpTokenCreate, db: AsyncSession = Depends(get_db)) -> dict:
    """Create a new MCP bearer token. The plaintext is returned only once."""
    plaintext = _TOKEN_PREFIX + secrets.token_urlsafe(48)
    prefix = plaintext[:12]
    salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    token_hash = bcrypt.hashpw(plaintext.encode(), salt).decode()

    token = McpBearerToken(
        name=data.name,
        token_hash=token_hash,
        token_prefix=prefix,
        idle_timeout_minutes=data.idle_timeout_minutes,
    )
    db.add(token)
    await db.commit()
    await db.refresh(token)

    await _sync_token_to_redis(token)

    return {
        "token": plaintext,
        "id": token.id,
        "name": token.name,
        "token_prefix": token.token_prefix,
        "idle_timeout_minutes": token.idle_timeout_minutes,
        "revoked": token.revoked,
        "last_used_at": token.last_used_at,
        "last_ip": token.last_ip,
        "last_country": token.last_country,
        "created_at": token.created_at,
    }


@router.get("/mcp-tokens", response_model=McpTokenList)
async def list_mcp_tokens(
    show_all: Annotated[bool, Query()] = False,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List MCP bearer tokens.

    Default: all active + 10 latest revoked. show_all=true for full list.
    """
    # Sync usage data from Redis to DB before returning
    await _sync_mcp_token_usage_from_redis(db)

    if show_all:
        result = await db.execute(select(McpBearerToken).order_by(desc(McpBearerToken.created_at)))
        items = list(result.scalars().all())
    else:
        # Active tokens
        active_result = await db.execute(
            select(McpBearerToken)
            .where(McpBearerToken.revoked == False)  # noqa: E712
            .order_by(desc(McpBearerToken.created_at))
        )
        active = list(active_result.scalars().all())
        # Latest 10 revoked
        revoked_result = await db.execute(
            select(McpBearerToken)
            .where(McpBearerToken.revoked == True)  # noqa: E712
            .order_by(desc(McpBearerToken.created_at))
            .limit(10)
        )
        revoked = list(revoked_result.scalars().all())
        items = active + revoked

    # Total count
    count_result = await db.execute(select(func.count(McpBearerToken.id)))
    total = count_result.scalar() or 0

    return {"items": items, "total": total}


@router.get("/mcp-tokens/{token_id}", response_model=McpTokenResponse)
async def get_mcp_token(token_id: str, db: AsyncSession = Depends(get_db)) -> McpTokenResponse:
    """Get a specific MCP bearer token."""
    result = await db.execute(select(McpBearerToken).where(McpBearerToken.id == token_id))
    token = result.scalar_one_or_none()
    if not token:
        raise HTTPException(status_code=404, detail="Token not found")
    return token  # type: ignore[return-value]


@router.delete("/mcp-tokens/{token_id}", status_code=204)
async def revoke_mcp_token(token_id: str, db: AsyncSession = Depends(get_db)) -> None:
    """Revoke an MCP bearer token."""
    result = await db.execute(select(McpBearerToken).where(McpBearerToken.id == token_id))
    token = result.scalar_one_or_none()
    if not token:
        raise HTTPException(status_code=404, detail="Token not found")
    token.revoked = True
    await db.commit()
    await _remove_token_from_redis(token.token_prefix)
