"""Redis pub/sub consumer — ingests request log entries into SQLite.

Subscribes to the ``smkrv:request_logs`` channel published by each MCP tool
execution.  Batches writes (up to 50 per flush or every 2 seconds) for efficiency.
Best-effort: if the backend is down, pub/sub messages are lost — but aggregate
metrics in Redis are still accurate.
"""

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime

from app.config import settings

logger = logging.getLogger(__name__)

_CHANNEL = "smkrv:request_logs"
_BATCH_SIZE = 50
_FLUSH_INTERVAL = 2.0  # seconds


async def run_log_consumer() -> None:
    """Subscribe to Redis pub/sub and batch-insert log rows into SQLite."""
    import redis.asyncio as aioredis

    from app.database import async_session_factory
    from app.models.request_log import RequestLog

    redis_url = settings.redis_url
    buffer: list[dict] = []
    last_flush = asyncio.get_running_loop().time()

    async def _flush() -> None:
        nonlocal buffer, last_flush
        if not buffer:
            return
        batch = buffer[:_BATCH_SIZE]
        buffer = buffer[_BATCH_SIZE:]
        try:
            async with async_session_factory() as db:
                for entry in batch:
                    # Parse ISO timestamp from recorder, fall back to now
                    ts_str = entry.get("timestamp")
                    if ts_str:
                        try:
                            created = datetime.fromisoformat(ts_str)
                        except (ValueError, TypeError):
                            created = datetime.now(UTC)
                    else:
                        created = datetime.now(UTC)

                    log = RequestLog(
                        id=str(uuid.uuid4()),
                        tool_name=entry.get("tool_name", "unknown"),
                        connection_id=entry.get("connection_id", ""),
                        duration_ms=float(entry.get("duration_ms", 0)),
                        success=bool(entry.get("success", True)),
                        error_message=entry.get("error_message") or None,
                        created_at=created,
                    )
                    db.add(log)
                await db.commit()
        except Exception:
            logger.exception("Failed to flush %d log entries", len(batch))
        last_flush = asyncio.get_running_loop().time()

    while True:
        try:
            # NOTE: Pub/sub requires a dedicated connection that blocks on listen().
            # Cannot use redis_client.get_redis() — the shared pool's connections
            # would be blocked by the subscribe loop.
            r = aioredis.from_url(redis_url, decode_responses=True)
            pubsub = r.pubsub()
            await pubsub.subscribe(_CHANNEL)
            logger.info("Log consumer subscribed to %s", _CHANNEL)

            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    data = json.loads(message["data"])
                    buffer.append(data)
                except (json.JSONDecodeError, TypeError):
                    continue

                now = asyncio.get_running_loop().time()
                if len(buffer) >= _BATCH_SIZE or (now - last_flush) >= _FLUSH_INTERVAL:
                    await _flush()

        except asyncio.CancelledError:
            # Final flush before shutdown
            await _flush()
            break
        except Exception:
            logger.exception("Log consumer error — reconnecting in 5s")
            await asyncio.sleep(5)


async def prune_old_logs(max_age_days: int = 90) -> int:
    """Delete request logs older than max_age_days. Returns count deleted."""
    from datetime import timedelta

    from sqlalchemy import delete

    from app.database import async_session_factory
    from app.models.request_log import RequestLog

    cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
    try:
        async with async_session_factory() as db:
            # SQLite stores naive datetimes — compare accordingly
            result = await db.execute(
                delete(RequestLog).where(
                    RequestLog.created_at < cutoff.replace(tzinfo=None)
                )
            )
            await db.commit()
            return result.rowcount  # type: ignore[return-value]
    except Exception:
        logger.exception("Failed to prune old logs")
        return 0


async def run_log_pruner(interval_hours: int = 6, max_age_days: int = 90) -> None:
    """Background loop that prunes old logs periodically."""
    while True:
        await asyncio.sleep(interval_hours * 3600)
        try:
            removed = await prune_old_logs(max_age_days)
            if removed > 0:
                logger.info("Pruned %d request logs older than %dd", removed, max_age_days)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Error in log pruner loop")
