"""Agent activity log endpoints."""

import logging
from typing import Annotated

from fastapi import APIRouter, Query

from app.schemas.oauth_client import AgentActivityList
from app.services.agent_auth import get_activity_log, get_activity_stats

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/agent-activity", response_model=AgentActivityList)
async def list_activity(
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict:
    """List recent agent activity from Redis."""
    items, total = await get_activity_log(skip=skip, limit=limit)
    return {"items": items, "total": total}


@router.get("/agent-activity/stats")
async def get_agent_activity_stats() -> dict:
    """Server-side aggregation of agent activity for the dashboard."""
    return await get_activity_stats()
