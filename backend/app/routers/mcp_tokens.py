"""CRUD endpoints for MCP bearer tokens (generated MCP server, port 8080)."""

import json
import logging
import secrets
from typing import Annotated

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.mcp_token import McpBearerToken
from app.schemas.mcp_token import (
    McpTokenCreate,
    McpTokenCreated,
    McpTokenList,
    McpTokenResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_TOKEN_PREFIX = "mcp_"
_BCRYPT_ROUNDS = 12


# ---------------------------------------------------------------------------
# Redis helpers
# ---------------------------------------------------------------------------

async def _get_redis():  # noqa: ANN202
    import redis.asyncio as aioredis

    return aioredis.from_url(
        settings.redis_url, decode_responses=True, socket_connect_timeout=2
    )


async def _sync_token_to_redis(token: McpBearerToken) -> None:
    """Push a single active token into Redis for the generated MCP server."""
    try:
        r = await _get_redis()
        key = f"mcp:bearer:{token.token_prefix}"
        await r.set(
            key,
            json.dumps(
                {
                    "token_hash": token.token_hash,
                    "idle_timeout_minutes": token.idle_timeout_minutes,
                    "name": token.name,
                }
            ),
        )
        await r.aclose()
    except Exception:
        logger.warning("Failed to sync MCP token %s to Redis", token.token_prefix)


async def _remove_token_from_redis(token_prefix: str) -> None:
    """Remove a revoked token from Redis."""
    try:
        r = await _get_redis()
        await r.delete(f"mcp:bearer:{token_prefix}")
        await r.aclose()
    except Exception:
        logger.warning("Failed to remove MCP token %s from Redis", token_prefix)


async def sync_all_mcp_tokens_to_redis(db: AsyncSession) -> int:
    """Sync all active MCP bearer tokens to Redis. Called during deploy."""
    try:
        r = await _get_redis()
        # Remove all existing mcp:bearer:* keys
        async for key in r.scan_iter("mcp:bearer:*", count=100):
            await r.delete(key)
        # Add all active tokens
        result = await db.execute(
            select(McpBearerToken).where(McpBearerToken.revoked == False)  # noqa: E712
        )
        tokens = list(result.scalars().all())
        for token in tokens:
            key = f"mcp:bearer:{token.token_prefix}"
            await r.set(
                key,
                json.dumps(
                    {
                        "token_hash": token.token_hash,
                        "idle_timeout_minutes": token.idle_timeout_minutes,
                        "name": token.name,
                    }
                ),
            )
        await r.aclose()
        logger.info("Synced %d MCP bearer tokens to Redis", len(tokens))
        return len(tokens)
    except Exception:
        logger.warning("Failed to sync MCP tokens to Redis")
        return 0


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/mcp-tokens", response_model=McpTokenCreated, status_code=201)
async def create_mcp_token(
    data: McpTokenCreate, db: AsyncSession = Depends(get_db)
) -> dict:
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
    if show_all:
        result = await db.execute(
            select(McpBearerToken).order_by(desc(McpBearerToken.created_at))
        )
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
async def get_mcp_token(
    token_id: str, db: AsyncSession = Depends(get_db)
) -> McpTokenResponse:
    """Get a specific MCP bearer token."""
    result = await db.execute(
        select(McpBearerToken).where(McpBearerToken.id == token_id)
    )
    token = result.scalar_one_or_none()
    if not token:
        raise HTTPException(status_code=404, detail="Token not found")
    return token  # type: ignore[return-value]


@router.delete("/mcp-tokens/{token_id}", status_code=204)
async def revoke_mcp_token(
    token_id: str, db: AsyncSession = Depends(get_db)
) -> None:
    """Revoke an MCP bearer token."""
    result = await db.execute(
        select(McpBearerToken).where(McpBearerToken.id == token_id)
    )
    token = result.scalar_one_or_none()
    if not token:
        raise HTTPException(status_code=404, detail="Token not found")
    token.revoked = True
    await db.commit()
    await _remove_token_from_redis(token.token_prefix)
