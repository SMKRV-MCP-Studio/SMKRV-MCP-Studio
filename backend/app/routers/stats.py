"""Aggregated statistics for the dashboard."""

import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter
from sqlalchemy import func, select

from app.database import async_session_factory
from app.models.change_history import ChangeHistory
from app.models.connection import Connection
from app.models.prompt import Prompt
from app.models.resource import Resource
from app.models.tool import Tool

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/stats/summary")
async def get_summary() -> dict:
    """Single-request dashboard summary: entity counts + injection blocked."""
    async with async_session_factory() as session:
        conn_count = (
            await session.execute(select(func.count(Connection.id)))
        ).scalar() or 0

        tools_total = (
            await session.execute(select(func.count(Tool.id)))
        ).scalar() or 0
        tools_enabled = (
            await session.execute(
                select(func.count(Tool.id)).where(Tool.is_enabled.is_(True))
            )
        ).scalar() or 0

        res_total = (
            await session.execute(select(func.count(Resource.id)))
        ).scalar() or 0
        res_enabled = (
            await session.execute(
                select(func.count(Resource.id)).where(
                    Resource.is_enabled.is_(True)
                )
            )
        ).scalar() or 0

        prompts_total = (
            await session.execute(select(func.count(Prompt.id)))
        ).scalar() or 0
        prompts_enabled = (
            await session.execute(
                select(func.count(Prompt.id)).where(
                    Prompt.is_enabled.is_(True)
                )
            )
        ).scalar() or 0

        injections_blocked = (
            await session.execute(
                select(func.count(ChangeHistory.id)).where(
                    ChangeHistory.action == "injection_blocked"
                )
            )
        ).scalar() or 0

    return {
        "connections": conn_count,
        "tools": {"total": tools_total, "enabled": tools_enabled},
        "resources": {"total": res_total, "enabled": res_enabled},
        "prompts": {"total": prompts_total, "enabled": prompts_enabled},
        "injections_blocked": injections_blocked,
    }


@router.get("/stats/sparklines")
async def get_sparklines() -> dict:
    """Daily event counts for the last 7 days, per entity type + injections."""
    now = datetime.now(UTC)
    since = now - timedelta(days=7)
    day_col = func.date(ChangeHistory.created_at)

    async with async_session_factory() as session:
        # Entity activity (create/update/delete) grouped by type + day
        rows = (
            await session.execute(
                select(
                    ChangeHistory.entity_type,
                    day_col.label("day"),
                    func.count().label("cnt"),
                )
                .where(
                    ChangeHistory.created_at >= since,
                    ChangeHistory.action.in_(
                        ["create", "update", "delete"]
                    ),
                    ChangeHistory.entity_type.in_(
                        ["tool", "resource", "prompt"]
                    ),
                )
                .group_by(ChangeHistory.entity_type, day_col)
            )
        ).all()

        # Injection blocked events grouped by day
        inj_rows = (
            await session.execute(
                select(
                    day_col.label("day"),
                    func.count().label("cnt"),
                )
                .where(
                    ChangeHistory.created_at >= since,
                    ChangeHistory.action == "injection_blocked",
                )
                .group_by(day_col)
            )
        ).all()

    # Build day-indexed maps
    today = now.date()
    days = [(today - timedelta(days=6 - i)) for i in range(7)]

    entity_map: dict[str, dict] = {}
    for entity_type, day, cnt in rows:
        entity_map.setdefault(entity_type, {})[str(day)] = cnt

    inj_map: dict[str, int] = {}
    for day, cnt in inj_rows:
        inj_map[str(day)] = cnt

    def to_series(m: dict[str, int]) -> list[int]:
        return [m.get(str(d), 0) for d in days]

    return {
        "tools": to_series(entity_map.get("tool", {})),
        "resources": to_series(entity_map.get("resource", {})),
        "prompts": to_series(entity_map.get("prompt", {})),
        "injections_blocked": to_series(inj_map),
        "days": [str(d) for d in days],
    }
