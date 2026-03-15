"""Centralized Redis connection pool -- single pool for the entire backend."""

import redis.asyncio as aioredis

from app.config import settings

_pool: aioredis.ConnectionPool | None = None


def get_redis_pool() -> aioredis.ConnectionPool:
    """Get or create the shared Redis connection pool."""
    global _pool
    if _pool is None:
        _pool = aioredis.ConnectionPool.from_url(
            settings.redis_url,
            decode_responses=True,
            max_connections=20,
        )
    return _pool


def get_redis() -> aioredis.Redis:
    """Get a Redis client using the shared connection pool."""
    return aioredis.Redis(connection_pool=get_redis_pool())


async def close_redis_pool() -> None:
    """Close the shared Redis pool. Call during app shutdown."""
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None
