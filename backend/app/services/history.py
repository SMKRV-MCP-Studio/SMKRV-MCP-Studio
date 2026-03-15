"""History service — record entity mutations for audit trail / rollback."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.change_history import ChangeHistory

logger = logging.getLogger(__name__)

# Fields to exclude from snapshots (internal / auto-managed)
_EXCLUDE_FIELDS = {"_sa_instance_state"}


def model_to_dict(instance: Any) -> dict[str, Any]:
    """Serialize a SQLAlchemy model instance to a plain dict.

    Handles datetime, list, and dict fields. Skips internal SA state.
    """
    result: dict[str, Any] = {}
    mapper = inspect(type(instance))

    for col in mapper.columns:
        key = col.key
        if key in _EXCLUDE_FIELDS:
            continue

        value = getattr(instance, key, None)
        if isinstance(value, datetime):
            value = value.isoformat()
        result[key] = value

    return result


def compute_changes(
    before: dict[str, Any], after_data: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """Compute a diff between the snapshot and update payload.

    Returns ``{field: {"old": ..., "new": ...}}`` only for fields that changed.
    """
    changes: dict[str, dict[str, Any]] = {}

    for field, new_value in after_data.items():
        old_value = before.get(field)

        # Normalize datetime strings for comparison
        if isinstance(old_value, str) and isinstance(new_value, str):
            if old_value == new_value:
                continue
        elif old_value == new_value:
            continue

        changes[field] = {"old": old_value, "new": new_value}

    return changes


_MAX_HISTORY_PER_ENTITY = 100


async def record_change(
    db: AsyncSession,
    *,
    entity_type: str,
    entity_id: str,
    entity_name: str,
    action: str,
    snapshot: dict[str, Any] | None = None,
    changes: dict[str, dict[str, Any]] | None = None,
) -> ChangeHistory:
    """Persist a history entry and prune oldest entries beyond retention limit."""
    entry = ChangeHistory(
        entity_type=entity_type,
        entity_id=entity_id,
        entity_name=entity_name,
        action=action,
        snapshot=snapshot,
        changes=changes,
    )
    db.add(entry)
    # Flush so the entry gets an ID, but don't commit — let the caller commit.
    await db.flush()

    # Prune oldest entries beyond retention limit
    await _prune_history(db, entity_type, entity_id)

    logger.debug(
        "Recorded %s on %s/%s (%s)",
        action,
        entity_type,
        entity_id,
        entity_name,
    )
    return entry


async def _prune_history(
    db: AsyncSession, entity_type: str, entity_id: str
) -> None:
    """Delete oldest history entries if count exceeds _MAX_HISTORY_PER_ENTITY."""
    from sqlalchemy import delete, func
    from sqlalchemy import select as sa_select

    count_q = (
        sa_select(func.count(ChangeHistory.id))
        .where(ChangeHistory.entity_type == entity_type)
        .where(ChangeHistory.entity_id == entity_id)
    )
    result = await db.execute(count_q)
    total = result.scalar_one()

    if total <= _MAX_HISTORY_PER_ENTITY:
        return

    # Find the IDs to keep (newest N)
    keep_q = (
        sa_select(ChangeHistory.id)
        .where(ChangeHistory.entity_type == entity_type)
        .where(ChangeHistory.entity_id == entity_id)
        .order_by(ChangeHistory.created_at.desc())
        .limit(_MAX_HISTORY_PER_ENTITY)
    )
    keep_result = await db.execute(keep_q)
    keep_ids = {row[0] for row in keep_result.all()}

    # Delete entries not in the keep set
    del_q = (
        delete(ChangeHistory)
        .where(ChangeHistory.entity_type == entity_type)
        .where(ChangeHistory.entity_id == entity_id)
        .where(ChangeHistory.id.notin_(keep_ids))
    )
    await db.execute(del_q)
    logger.debug(
        "Pruned history for %s/%s: kept %d, removed %d",
        entity_type, entity_id, _MAX_HISTORY_PER_ENTITY, total - _MAX_HISTORY_PER_ENTITY,
    )
