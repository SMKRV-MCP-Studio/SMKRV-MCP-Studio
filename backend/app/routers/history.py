"""Change history endpoints — audit trail and rollback."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.change_history import ChangeHistory
from app.models.connection import Connection
from app.models.parameter import Parameter
from app.models.prompt import Prompt
from app.models.resource import Resource
from app.models.server_config import ServerConfig
from app.models.tool import Tool
from app.schemas.history import ChangeHistoryList, ChangeHistoryResponse
from app.services.history import model_to_dict, record_change

router = APIRouter()
logger = logging.getLogger(__name__)

# Map entity_type → SQLAlchemy model
_MODEL_MAP: dict[str, type] = {
    "tool": Tool,
    "resource": Resource,
    "prompt": Prompt,
    "connection": Connection,
    "server_config": ServerConfig,
}


@router.get("/history", response_model=ChangeHistoryList)
async def list_history(
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    entity_type: str | None = None,
    entity_id: str | None = None,
    action: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List change history with optional filters, newest first."""
    query = select(ChangeHistory)
    count_query = select(func.count(ChangeHistory.id))

    if entity_type:
        query = query.where(ChangeHistory.entity_type == entity_type)
        count_query = count_query.where(ChangeHistory.entity_type == entity_type)
    if entity_id:
        query = query.where(ChangeHistory.entity_id == entity_id)
        count_query = count_query.where(ChangeHistory.entity_id == entity_id)
    if action:
        query = query.where(ChangeHistory.action == action)
        count_query = count_query.where(ChangeHistory.action == action)

    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    result = await db.execute(
        query.order_by(ChangeHistory.created_at.desc()).offset(skip).limit(limit)
    )
    items = list(result.scalars().all())

    return {"items": items, "total": total}


@router.get(
    "/history/{entity_type}/{entity_id}",
    response_model=ChangeHistoryList,
)
async def entity_history(
    entity_type: str,
    entity_id: str,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get change history for a specific entity."""
    if entity_type not in _MODEL_MAP:
        raise HTTPException(status_code=400, detail=f"Unknown entity_type: {entity_type}")

    query = select(ChangeHistory).where(
        ChangeHistory.entity_type == entity_type,
        ChangeHistory.entity_id == entity_id,
    )
    count_query = select(func.count(ChangeHistory.id)).where(
        ChangeHistory.entity_type == entity_type,
        ChangeHistory.entity_id == entity_id,
    )

    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    result = await db.execute(
        query.order_by(ChangeHistory.created_at.desc()).offset(skip).limit(limit)
    )
    items = list(result.scalars().all())

    return {"items": items, "total": total}


@router.post("/history/{history_id}/rollback", response_model=ChangeHistoryResponse)
async def rollback_change(
    history_id: str,
    db: AsyncSession = Depends(get_db),
) -> ChangeHistory:
    """Rollback an entity to its state from a history snapshot.

    Only works for 'update' and 'delete' actions (which have snapshots).
    Restores all fields from the snapshot and records a new 'rollback' history entry.
    """
    result = await db.execute(
        select(ChangeHistory).where(ChangeHistory.id == history_id)
    )
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="History entry not found")

    if not entry.snapshot:
        raise HTTPException(
            status_code=400,
            detail="Cannot rollback — no snapshot available (create actions have no prior state)",
        )

    entity_type = entry.entity_type
    entity_id = entry.entity_id
    model_cls = _MODEL_MAP.get(entity_type)

    if not model_cls:
        raise HTTPException(status_code=400, detail=f"Unknown entity_type: {entity_type}")

    # Check if entity still exists
    if model_cls is Tool:
        existing_result = await db.execute(
            select(Tool).where(Tool.id == entity_id).options(selectinload(Tool.parameters))
        )
    else:
        existing_result = await db.execute(
            select(model_cls).where(model_cls.id == entity_id)
        )
    existing = existing_result.scalar_one_or_none()

    snapshot = dict(entry.snapshot)

    # Remove fields that shouldn't be restored
    snapshot.pop("id", None)
    snapshot.pop("created_at", None)
    snapshot.pop("updated_at", None)

    if existing:
        # Entity exists — update it back to snapshot state
        before_snapshot = model_to_dict(existing)

        # Special handling for Tool parameters
        if model_cls is Tool and "parameters" in snapshot:
            params_data = snapshot.pop("parameters", [])
            existing.parameters.clear()
            await db.flush()
            for p in params_data:
                p.pop("id", None)
                p.pop("tool_id", None)
                param = Parameter(**p)
                param.tool_id = entity_id
                existing.parameters.append(param)

        for field, value in snapshot.items():
            if hasattr(existing, field):
                setattr(existing, field, value)

        # A4-07: Increment entity version after rollback
        if hasattr(existing, "version"):
            existing.version = (existing.version or 0) + 1

        await db.flush()

        # Record the rollback as a new history entry
        rollback_entry = await record_change(
            db,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_name=before_snapshot.get("name", ""),
            action="rollback",
            snapshot=before_snapshot,
            changes={"rolled_back_to": history_id},
        )
    else:
        # Entity was deleted — recreate it from snapshot
        if model_cls is Tool:
            params_data = snapshot.pop("parameters", [])
            entity = Tool(id=entity_id, **snapshot)
            for p in params_data:
                p.pop("id", None)
                p.pop("tool_id", None)
                param = Parameter(**p)
                entity.parameters.append(param)
        else:
            entity = model_cls(id=entity_id, **snapshot)

        # A4-07: Set version=1 on recreated entities
        if hasattr(entity, "version"):
            entity.version = 1

        db.add(entity)
        await db.flush()

        rollback_entry = await record_change(
            db,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_name=snapshot.get("name", ""),
            action="rollback",
            snapshot=None,
            changes={"rolled_back_to": history_id, "recreated": True},
        )

    await db.commit()
    return rollback_entry
