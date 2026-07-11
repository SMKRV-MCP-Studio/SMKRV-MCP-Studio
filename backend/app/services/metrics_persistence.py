"""Metrics persistence — periodic snapshots from Redis to DB.

Saves daily rollup keys to SQLite so metrics survive complete Redis data loss.
Also provides a fallback read path when MCP/Redis is unavailable.
"""

import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.metrics_snapshot import AGGREGATE_ALL, MetricsSnapshot
from app.services.redis_client import get_redis

logger = logging.getLogger(__name__)


async def persist_daily_snapshots(db: AsyncSession) -> int:
    """Read completed daily rollup keys from Redis and upsert into DB.

    Only persists days that are already complete (not today).
    Idempotent: uses upsert via unique index on (date, tool_name).
    """
    today = datetime.now(UTC).strftime("%Y%m%d")
    persisted = 0

    try:
        r = get_redis()

        # Scan for daily keys
        daily_keys: list[str] = []
        async for key in r.scan_iter("smkrv:ts_d:*", count=200):
            date_part = key.replace("smkrv:ts_d:", "")
            # Validate date format
            if len(date_part) != 8 or not date_part.isdigit():
                continue
            if date_part == today:
                continue  # Skip incomplete today
            daily_keys.append(key)

        for key in sorted(daily_keys):
            date_str = key.replace("smkrv:ts_d:", "")
            data = await r.hgetall(key)
            if not data:
                continue

            calls = int(data.get("calls", 0))
            errors = int(data.get("errors", 0))
            total_duration = float(data.get("total_duration_ms", 0.0))

            # Extract per-tool breakdown
            tool_breakdown = {}
            for field, val in data.items():
                if field.startswith("tool:"):
                    tool_breakdown[field] = int(val)

            # Upsert aggregate row
            existing = await db.execute(
                select(MetricsSnapshot).where(
                    MetricsSnapshot.date == date_str,
                    MetricsSnapshot.tool_name == AGGREGATE_ALL,
                )
            )
            snapshot = existing.scalar_one_or_none()
            if snapshot:
                snapshot.calls = calls
                snapshot.errors = errors
                snapshot.total_duration_ms = total_duration
                snapshot.tool_breakdown = (
                    json.dumps(tool_breakdown) if tool_breakdown else None
                )
            else:
                db.add(MetricsSnapshot(
                    date=date_str,
                    tool_name=AGGREGATE_ALL,
                    calls=calls,
                    errors=errors,
                    total_duration_ms=total_duration,
                    tool_breakdown=(
                        json.dumps(tool_breakdown) if tool_breakdown else None
                    ),
                ))
            persisted += 1

        if persisted:
            await db.commit()
            logger.info("Persisted %d daily metric snapshots to DB", persisted)

    except Exception:
        await db.rollback()
        logger.warning("Failed to persist daily metric snapshots", exc_info=True)

    return persisted


async def get_daily_timeseries_from_db(
    db: AsyncSession, days: int = 90,
) -> list[dict]:
    """Read daily snapshots from DB as fallback when Redis/MCP is unavailable."""
    cutoff = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y%m%d")
    result = await db.execute(
        select(MetricsSnapshot)
        .where(
            MetricsSnapshot.date >= cutoff,
            MetricsSnapshot.tool_name == AGGREGATE_ALL,
        )
        .order_by(MetricsSnapshot.date)
    )
    snapshots = result.scalars().all()

    points = []
    for s in snapshots:
        avg_ms = (s.total_duration_ms / s.calls) if s.calls > 0 else 0.0
        try:
            dt = datetime.strptime(s.date, "%Y%m%d").replace(tzinfo=UTC)
            ts = dt.isoformat()
        except ValueError:
            ts = s.date
        points.append({
            "timestamp": ts,
            "calls": s.calls,
            "errors": s.errors,
            "avg_duration_ms": round(avg_ms, 2),
        })
    return points
