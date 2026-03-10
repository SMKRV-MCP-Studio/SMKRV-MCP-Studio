"""Request logs router — paginated, filtered access to tool execution logs."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.request_log import RequestLog
from app.schemas.request_log import RequestLogList, RequestLogResponse

router = APIRouter()


@router.get("/logs", response_model=RequestLogList)
async def list_request_logs(
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    tool_name: Annotated[str | None, Query()] = None,
    connection_id: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query(pattern="^(success|error)$")] = None,
    sort: Annotated[str, Query(pattern="^(created_at|duration_ms)$")] = "created_at",
    order: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    db: AsyncSession = Depends(get_db),
) -> RequestLogList:
    """Get paginated request logs with optional filters."""
    query = select(RequestLog)
    count_query = select(func.count(RequestLog.id))

    # Apply filters
    if tool_name:
        query = query.where(RequestLog.tool_name == tool_name)
        count_query = count_query.where(RequestLog.tool_name == tool_name)
    if connection_id:
        query = query.where(RequestLog.connection_id == connection_id)
        count_query = count_query.where(RequestLog.connection_id == connection_id)
    if status == "success":
        query = query.where(RequestLog.success.is_(True))
        count_query = count_query.where(RequestLog.success.is_(True))
    elif status == "error":
        query = query.where(RequestLog.success.is_(False))
        count_query = count_query.where(RequestLog.success.is_(False))
    if since:
        query = query.where(RequestLog.created_at >= since)
        count_query = count_query.where(RequestLog.created_at >= since)
    if until:
        query = query.where(RequestLog.created_at <= until)
        count_query = count_query.where(RequestLog.created_at <= until)

    # Sort
    sort_col = RequestLog.duration_ms if sort == "duration_ms" else RequestLog.created_at
    if order == "asc":
        query = query.order_by(sort_col)
    else:
        query = query.order_by(desc(sort_col))

    # Pagination
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    result = await db.execute(query.offset(skip).limit(limit))
    items = [RequestLogResponse.model_validate(row) for row in result.scalars().all()]

    return RequestLogList(items=items, total=total)
