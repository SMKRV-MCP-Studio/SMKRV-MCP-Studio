"""Agent authentication — token validation, rate limiting, activity logging.

This module validates agent tokens against Redis/backend and enforces
rate limiting. It runs inside the agent-mcp container.

Token lookup is O(1) via prefix index keys instead of O(n) SCAN + bcrypt.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime

import bcrypt
import redis.asyncio as aioredis

from agent_mcp import config

logger = logging.getLogger(__name__)

# Cap the legacy fallback scan so an unknown bearer token cannot force an
# unbounded number of bcrypt verifications. Every current token is written with
# a prefix index (agent:token_idx / agent:oauth_idx) on create/sync/refresh, so
# the fallback only ever needs to cover a handful of un-indexed legacy tokens.
_FALLBACK_SCAN_MAX_KEYS = 25


async def _checkpw(token: str, stored_hash: str) -> bool:
    """bcrypt.checkpw off the event loop (it is CPU-bound, ~150ms at cost 12)."""
    if not stored_hash:
        return False
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, bcrypt.checkpw, token.encode(), stored_hash.encode()
    )

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


async def validate_token(
    bearer_token: str, client_ip: str = "", client_country: str = "",
) -> dict | None:
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
                if await _checkpw(bearer_token, stored_hash):
                    # Check expiry
                    expires = data.get("expires_at", "")
                    if expires:
                        exp_dt = datetime.fromisoformat(expires)
                        if exp_dt.tzinfo is None:
                            exp_dt = exp_dt.replace(tzinfo=UTC)
                        if exp_dt <= datetime.now(UTC):
                            return None
                    # Update usage tracking in Redis hash
                    try:
                        usage = {"last_used_at": datetime.now(UTC).isoformat()}
                        if client_ip:
                            usage["last_ip"] = client_ip
                        if client_country:
                            usage["last_country"] = client_country
                        await r.hset(token_key, mapping=usage)
                    except Exception:
                        pass
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
                if await _checkpw(bearer_token, stored_hash):
                    idle_timeout = int(data.get("idle_timeout", "3600"))
                    # Refresh TTL (sliding window)
                    await r.expire(oauth_key, idle_timeout)
                    await r.expire(oauth_idx_key, idle_timeout)
                    # Update usage tracking
                    try:
                        usage = {"last_used_at": datetime.now(UTC).isoformat()}
                        if client_ip:
                            usage["last_ip"] = client_ip
                        if client_country:
                            usage["last_country"] = client_country
                        await r.hset(oauth_key, mapping=usage)
                    except Exception:
                        pass
                    return {
                        "token_prefix": data.get("client_id", "")[:12],
                        "token_type": "oauth",
                        "client_id": data.get("client_id", ""),
                    }
            except Exception:
                pass

    # 3. Fallback: scan for tokens without index (backwards compat)
    # This handles tokens created before the index was added.
    result = await _fallback_scan_validate(r, bearer_token, client_ip, client_country)
    return result


async def _fallback_scan_validate(
    r: aioredis.Redis, bearer_token: str,
    client_ip: str = "", client_country: str = "",
) -> dict | None:
    """Bounded fallback scan for legacy tokens stored without a prefix index.

    Every current token gets a prefix index on create/sync/refresh, so this only
    covers a handful of pre-index legacy tokens. The scan is capped at
    _FALLBACK_SCAN_MAX_KEYS per key class and each bcrypt runs off the event loop,
    so an unknown bearer token (which always misses both indexes) cannot force an
    unbounded, event-loop-blocking number of verifications.
    """
    scanned = 0
    # Try agent tokens
    async for key in r.scan_iter(match="agent:token:*", count=100):
        scanned += 1
        if scanned > _FALLBACK_SCAN_MAX_KEYS:
            logger.warning("Agent token fallback scan hit the %d-key cap",
                           _FALLBACK_SCAN_MAX_KEYS)
            break
        data = await r.hgetall(key)
        if not data:
            continue
        stored_hash = data.get("token_hash", "")
        try:
            if await _checkpw(bearer_token, stored_hash):
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
                # Update usage tracking
                try:
                    usage = {"last_used_at": datetime.now(UTC).isoformat()}
                    if client_ip:
                        usage["last_ip"] = client_ip
                    if client_country:
                        usage["last_country"] = client_country
                    await r.hset(key, mapping=usage)
                except Exception:
                    pass
                return {
                    "token_prefix": data.get("token_prefix", ""),
                    "token_type": "agent_token",
                    "name": data.get("name", ""),
                }
        except Exception:
            continue

    # Try OAuth access tokens
    scanned = 0
    async for key in r.scan_iter(match="agent:oauth:*", count=100):
        scanned += 1
        if scanned > _FALLBACK_SCAN_MAX_KEYS:
            logger.warning("OAuth fallback scan hit the %d-key cap",
                           _FALLBACK_SCAN_MAX_KEYS)
            break
        data = await r.hgetall(key)
        if not data:
            continue
        stored_hash = data.get("access_token_hash", "")
        try:
            if await _checkpw(bearer_token, stored_hash):
                idle_timeout = int(data.get("idle_timeout", "3600"))
                await r.expire(key, idle_timeout)
                # Create index for future lookups
                prefix = bearer_token[:12]
                await r.set(f"agent:oauth_idx:{prefix}", key, ex=idle_timeout)
                # Update usage tracking
                try:
                    usage = {"last_used_at": datetime.now(UTC).isoformat()}
                    if client_ip:
                        usage["last_ip"] = client_ip
                    if client_country:
                        usage["last_country"] = client_country
                    await r.hset(key, mapping=usage)
                except Exception:
                    pass
                return {
                    "token_prefix": data.get("client_id", "")[:12],
                    "token_type": "oauth",
                    "client_id": data.get("client_id", ""),
                }
        except Exception:
            continue

    return None


async def check_rate_limit(token_prefix: str, max_per_minute: int = 0) -> bool:
    """Check per-token rate limit. Returns True if allowed.

    Uses a Redis pipeline to make INCR + EXPIRE atomic (H-06).
    """
    if max_per_minute <= 0:
        max_per_minute = config.DEFAULT_RATE_LIMIT
    try:
        r = _get_redis()
        key = f"agent:rate:{token_prefix}"
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.expire(key, 60)
        results = await pipe.execute()
        current = results[0]
        return current <= max_per_minute
    except Exception:
        # Fail closed: a request already past token validation (which itself
        # requires Redis) should be denied rather than let through unmetered
        # if the rate-limit op errors.
        logger.warning("Rate-limit check failed; denying request", exc_info=True)
        return False


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
