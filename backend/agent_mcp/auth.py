"""Agent authentication — token validation, rate limiting, activity logging.

This module validates agent tokens against Redis/backend and enforces
rate limiting. It runs inside the agent-mcp container.

Token lookup is O(1) via prefix index keys instead of O(n) SCAN + bcrypt.
"""

import json
import logging
from datetime import UTC, datetime

import bcrypt
import redis.asyncio as aioredis

from agent_mcp import config

logger = logging.getLogger(__name__)

# Persistent connection pool (not a new connection per call)
_pool: aioredis.ConnectionPool | None = None


def _get_pool() -> aioredis.ConnectionPool:
    """Get or create the shared Redis connection pool."""
    global _pool
    if _pool is None:
        _pool = aioredis.ConnectionPool.from_url(
            config.REDIS_URL, decode_responses=True, max_connections=10
        )
    return _pool


def _get_redis() -> aioredis.Redis:
    """Get a Redis client backed by the shared connection pool."""
    return aioredis.Redis(connection_pool=_get_pool())


async def close() -> None:
    """Close the Redis connection pool (call on shutdown)."""
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


async def validate_token(bearer_token: str, client_ip: str = "") -> dict | None:
    """Validate a bearer token (either agent token or OAuth access token).

    Uses O(1) prefix-based lookup instead of scanning all keys.
    Returns token info dict if valid, None if invalid.
    """
    r = _get_redis()

    # 1. Try agent token — prefix index lookup
    token_prefix = bearer_token[:12]
    idx_key = f"agent:token_idx:{token_prefix}"
    token_key = await r.get(idx_key)

    if token_key:
        data = await r.hgetall(token_key)
        if data:
            stored_hash = data.get("token_hash", "")
            try:
                if bcrypt.checkpw(bearer_token.encode(), stored_hash.encode()):
                    # Check expiry
                    expires = data.get("expires_at", "")
                    if expires:
                        exp_dt = datetime.fromisoformat(expires)
                        if exp_dt.tzinfo is None:
                            exp_dt = exp_dt.replace(tzinfo=UTC)
                        if exp_dt <= datetime.now(UTC):
                            return None
                    return {
                        "token_prefix": data.get("token_prefix", ""),
                        "token_type": "agent_token",
                        "name": data.get("name", ""),
                    }
            except Exception:
                pass

    # 2. Try OAuth access token — prefix index lookup
    oauth_idx_key = f"agent:oauth_idx:{token_prefix}"
    oauth_key = await r.get(oauth_idx_key)

    if oauth_key:
        data = await r.hgetall(oauth_key)
        if data:
            stored_hash = data.get("access_token_hash", "")
            try:
                if bcrypt.checkpw(bearer_token.encode(), stored_hash.encode()):
                    idle_timeout = int(data.get("idle_timeout", "3600"))
                    # Refresh TTL (sliding window)
                    await r.expire(oauth_key, idle_timeout)
                    await r.expire(oauth_idx_key, idle_timeout)
                    return {
                        "token_prefix": data.get("client_id", "")[:12],
                        "token_type": "oauth",
                        "client_id": data.get("client_id", ""),
                    }
            except Exception:
                pass

    # 3. Fallback: scan for tokens without index (backwards compat)
    # This handles tokens created before the index was added.
    result = await _fallback_scan_validate(r, bearer_token)
    return result


async def _fallback_scan_validate(r: aioredis.Redis, bearer_token: str) -> dict | None:
    """Fallback O(n) scan for tokens without prefix index.

    This is only used for tokens stored before the prefix index was added.
    Once all old tokens expire, this code path is never hit.
    """
    # Try agent tokens
    async for key in r.scan_iter(match="agent:token:*", count=100):
        data = await r.hgetall(key)
        if not data:
            continue
        stored_hash = data.get("token_hash", "")
        try:
            if bcrypt.checkpw(bearer_token.encode(), stored_hash.encode()):
                # Check expiry
                expires = data.get("expires_at", "")
                if expires:
                    exp_dt = datetime.fromisoformat(expires)
                    if exp_dt.tzinfo is None:
                        exp_dt = exp_dt.replace(tzinfo=UTC)
                    if exp_dt <= datetime.now(UTC):
                        return None
                # Create index for future lookups
                prefix = bearer_token[:12]
                ttl = await r.ttl(key)
                if ttl > 0:
                    await r.set(f"agent:token_idx:{prefix}", key, ex=ttl)
                return {
                    "token_prefix": data.get("token_prefix", ""),
                    "token_type": "agent_token",
                    "name": data.get("name", ""),
                }
        except Exception:
            continue

    # Try OAuth access tokens
    async for key in r.scan_iter(match="agent:oauth:*", count=100):
        data = await r.hgetall(key)
        if not data:
            continue
        stored_hash = data.get("access_token_hash", "")
        try:
            if bcrypt.checkpw(bearer_token.encode(), stored_hash.encode()):
                idle_timeout = int(data.get("idle_timeout", "3600"))
                await r.expire(key, idle_timeout)
                # Create index for future lookups
                prefix = bearer_token[:12]
                await r.set(f"agent:oauth_idx:{prefix}", key, ex=idle_timeout)
                return {
                    "token_prefix": data.get("client_id", "")[:12],
                    "token_type": "oauth",
                    "client_id": data.get("client_id", ""),
                }
        except Exception:
            continue

    return None


async def check_rate_limit(token_prefix: str, max_per_minute: int = 0) -> bool:
    """Check per-token rate limit. Returns True if allowed."""
    if max_per_minute <= 0:
        max_per_minute = config.DEFAULT_RATE_LIMIT
    try:
        r = _get_redis()
        key = f"agent:rate:{token_prefix}"
        current = await r.incr(key)
        if current == 1:
            await r.expire(key, 60)
        return current <= max_per_minute
    except Exception:
        return True


async def record_activity(
    token_prefix: str,
    tool_name: str,
    ip: str,
    success: bool,
) -> None:
    """Record an agent activity entry in Redis."""
    try:
        r = _get_redis()
        entry = json.dumps({
            "timestamp": datetime.now(UTC).isoformat(),
            "token_prefix": token_prefix,
            "tool_name": tool_name,
            "ip": ip,
            "success": success,
        })
        await r.lpush("agent:activity", entry)
        await r.ltrim("agent:activity", 0, 499)
    except Exception:
        logger.warning("Failed to record agent activity")
