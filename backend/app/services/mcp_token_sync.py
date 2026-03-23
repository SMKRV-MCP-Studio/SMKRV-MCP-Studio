"""MCP bearer token ↔ Redis synchronization service.

Extracted from routers/mcp_tokens.py so that both the router and the
deployer (and lifespan startup) can call these without circular imports.
"""

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mcp_token import McpBearerToken
from app.services.redis_client import get_redis

logger = logging.getLogger(__name__)


async def _sync_token_to_redis(token: McpBearerToken) -> None:
    """Push a single active token into Redis for the generated MCP server."""
    try:
        r = get_redis()
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
    except Exception:
        logger.warning(
            "Failed to sync MCP token %s to Redis", token.token_prefix
        )


async def _sync_mcp_token_usage_from_redis(db: AsyncSession) -> int:
    """Sync last_used_at and last_ip from Redis usage hashes to DB.

    The generated MCP server stores usage in mcp:bearer_usage:{prefix}.
    This function reads those values and updates the DB so the UI shows them.
    """
    result = await db.execute(
        select(McpBearerToken).where(
            McpBearerToken.revoked == False  # noqa: E712
        )
    )
    tokens = list(result.scalars().all())
    if not tokens:
        return 0

    updated = 0
    try:
        r = get_redis()
        for token in tokens:
            usage_key = f"mcp:bearer_usage:{token.token_prefix}"
            data = await r.hgetall(usage_key)
            if not data:
                continue
            changed = False
            last_used_raw = data.get("last_used_at")
            if last_used_raw:
                try:
                    from datetime import UTC, datetime

                    used_ts = float(last_used_raw)
                    used_dt = datetime.fromtimestamp(used_ts, tz=UTC)
                    # Normalize to naive for SQLite comparison (bug #7)
                    used_naive = used_dt.replace(tzinfo=None)
                    db_used = token.last_used_at
                    if db_used is not None and db_used.tzinfo is not None:
                        db_used = db_used.replace(tzinfo=None)
                    if db_used is None or used_naive > db_used:
                        token.last_used_at = used_naive
                        changed = True
                except (ValueError, TypeError):
                    pass
            last_ip = data.get("last_ip")
            if last_ip and last_ip != token.last_ip:
                token.last_ip = last_ip
                changed = True
            last_country = data.get("last_country")
            if last_country and last_country != token.last_country:
                token.last_country = last_country
                changed = True
            if changed:
                updated += 1
        if updated:
            await db.commit()
    except Exception:
        logger.warning("Failed to sync MCP token usage from Redis")

    return updated


async def _remove_token_from_redis(token_prefix: str) -> None:
    """Remove a revoked token from Redis."""
    try:
        r = get_redis()
        await r.delete(f"mcp:bearer:{token_prefix}")
    except Exception:
        logger.warning(
            "Failed to remove MCP token %s from Redis", token_prefix
        )


async def sync_all_mcp_tokens_to_redis(db: AsyncSession) -> int:
    """Sync all active MCP bearer tokens to Redis. Called during deploy."""
    try:
        r = get_redis()
        # Remove all existing mcp:bearer:* keys (but NOT mcp:bearer_usage:* usage tracking)
        async for key in r.scan_iter("mcp:bearer:*", count=100):
            if "_usage" in key:
                continue
            await r.delete(key)
        # Add all active tokens
        result = await db.execute(
            select(McpBearerToken).where(
                McpBearerToken.revoked == False  # noqa: E712
            )
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
        logger.info("Synced %d MCP bearer tokens to Redis", len(tokens))
        return len(tokens)
    except Exception:
        logger.warning("Failed to sync MCP tokens to Redis")
        return 0
