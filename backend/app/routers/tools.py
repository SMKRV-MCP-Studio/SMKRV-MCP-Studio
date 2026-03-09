"""CRUD endpoints for MCP tools."""

import copy
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.parameter import Parameter
from app.models.tool import Tool
from app.schemas.tool import (
    ToolCreate,
    ToolList,
    ToolResponse,
    ToolUpdate,
)
from app.services.history import compute_changes, model_to_dict, record_change
from app.services.prompt_guard import guard_and_log

router = APIRouter()


def _tool_snapshot(tool: Tool) -> dict:
    """Snapshot a tool including its parameters."""
    data = model_to_dict(tool)
    data["parameters"] = [model_to_dict(p) for p in tool.parameters]
    return data


@router.post("/tools", response_model=ToolResponse, status_code=201)
async def create_tool(data: ToolCreate, db: AsyncSession = Depends(get_db)) -> Tool:
    """Create a new tool with inline parameters."""
    await guard_and_log("tool", data.model_dump(), db)
    tool = Tool(
        connection_id=data.connection_id,
        name=data.name,
        description=data.description,
        sql_query=data.sql_query,
        return_type=data.return_type,
        tags=data.tags,
        annotations=data.annotations,
        cache_ttl=data.cache_ttl,
        is_enabled=data.is_enabled,
        transform_template=data.transform_template,
    )

    for param_data in data.parameters:
        param = Parameter(**param_data.model_dump())
        tool.parameters.append(param)

    db.add(tool)
    await db.flush()

    await record_change(
        db, entity_type="tool", entity_id=tool.id, entity_name=tool.name, action="create",
    )
    await db.commit()

    # Reload with parameters
    result = await db.execute(
        select(Tool).where(Tool.id == tool.id).options(selectinload(Tool.parameters))
    )
    return result.scalar_one()


@router.get("/tools", response_model=ToolList)
async def list_tools(
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    connection_id: str | None = None,
    tags: Annotated[str | None, Query(description="Comma-separated tags")] = None,
    search: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List tools with optional filters."""
    query = select(Tool).options(selectinload(Tool.parameters))
    count_query = select(func.count(Tool.id))

    if connection_id:
        query = query.where(Tool.connection_id == connection_id)
        count_query = count_query.where(Tool.connection_id == connection_id)
    if search:
        pattern = f"%{search}%"
        query = query.where(Tool.name.ilike(pattern) | Tool.description.ilike(pattern))
        count_query = count_query.where(
            Tool.name.ilike(pattern) | Tool.description.ilike(pattern)
        )

    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    result = await db.execute(query.offset(skip).limit(limit).order_by(Tool.name))
    items = list(result.scalars().unique().all())

    # Filter by tags in Python (JSON array in SQLite doesn't support SQL filtering well)
    if tags:
        tag_list = [t.strip() for t in tags.split(",")]
        items = [t for t in items if any(tag in (t.tags or []) for tag in tag_list)]
        total = len(items)

    return {"items": items, "total": total}


@router.get("/tools/{tool_id}", response_model=ToolResponse)
async def get_tool(tool_id: str, db: AsyncSession = Depends(get_db)) -> Tool:
    """Get a tool by ID with parameters."""
    result = await db.execute(
        select(Tool).where(Tool.id == tool_id).options(selectinload(Tool.parameters))
    )
    tool = result.scalar_one_or_none()
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")
    return tool


@router.patch("/tools/{tool_id}", response_model=ToolResponse)
async def update_tool(
    tool_id: str, data: ToolUpdate, db: AsyncSession = Depends(get_db)
) -> Tool:
    """Update a tool. Parameters are replaced entirely if provided."""
    result = await db.execute(
        select(Tool).where(Tool.id == tool_id).options(selectinload(Tool.parameters))
    )
    tool = result.scalar_one_or_none()
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")

    # OL-1: Optimistic locking — reject if client version is stale
    if data.version is not None and data.version != tool.version:
        raise HTTPException(
            status_code=409,
            detail=f"Version conflict: expected {data.version}, current {tool.version}",
        )

    await guard_and_log("tool", data.model_dump(exclude_unset=True), db)

    before = _tool_snapshot(tool)
    update_data = data.model_dump(exclude_unset=True)
    update_data.pop("version", None)  # Don't set version from client

    # Handle parameters replacement
    if "parameters" in update_data:
        param_list = update_data.pop("parameters")
        # Clear the relationship collection (cascades deletes)
        tool.parameters.clear()
        await db.flush()
        # Add new parameters
        for param_data in param_list:
            param = Parameter(**param_data)
            param.tool_id = tool.id
            tool.parameters.append(param)

    for field, value in update_data.items():
        setattr(tool, field, value)

    # Auto-increment version on every update
    tool.version = (tool.version or 0) + 1

    changes = compute_changes(before, data.model_dump(exclude_unset=True))
    await record_change(
        db, entity_type="tool", entity_id=tool.id, entity_name=tool.name,
        action="update", snapshot=before, changes=changes,
    )
    await db.commit()

    # Reload
    result = await db.execute(
        select(Tool).where(Tool.id == tool.id).options(selectinload(Tool.parameters))
    )
    return result.scalar_one()


@router.delete("/tools/{tool_id}", status_code=204)
async def delete_tool(tool_id: str, db: AsyncSession = Depends(get_db)) -> None:
    """Delete a tool and its parameters."""
    result = await db.execute(
        select(Tool).where(Tool.id == tool_id).options(selectinload(Tool.parameters))
    )
    tool = result.scalar_one_or_none()
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")

    before = _tool_snapshot(tool)
    await record_change(
        db, entity_type="tool", entity_id=tool.id, entity_name=tool.name,
        action="delete", snapshot=before,
    )
    await db.delete(tool)
    await db.commit()


@router.post("/tools/{tool_id}/duplicate", response_model=ToolResponse, status_code=201)
async def duplicate_tool(tool_id: str, db: AsyncSession = Depends(get_db)) -> Tool:
    """Duplicate a tool with '_copy' suffix."""
    result = await db.execute(
        select(Tool).where(Tool.id == tool_id).options(selectinload(Tool.parameters))
    )
    original = result.scalar_one_or_none()
    if not original:
        raise HTTPException(status_code=404, detail="Tool not found")

    new_tool = Tool(
        connection_id=original.connection_id,
        name=f"{original.name}_copy",
        description=original.description,
        sql_query=original.sql_query,
        return_type=original.return_type,
        tags=copy.deepcopy(original.tags),
        version=1,
        annotations=copy.deepcopy(original.annotations),
        cache_ttl=original.cache_ttl,
        is_enabled=original.is_enabled,
        transform_template=original.transform_template,
    )

    for param in original.parameters:
        new_param = Parameter(
            name=param.name,
            param_type=param.param_type,
            description=param.description,
            is_required=param.is_required,
            default_value=param.default_value,
            enum_values=copy.deepcopy(param.enum_values),
            sort_order=param.sort_order,
        )
        new_tool.parameters.append(new_param)

    db.add(new_tool)
    await db.commit()

    result = await db.execute(
        select(Tool).where(Tool.id == new_tool.id).options(selectinload(Tool.parameters))
    )
    return result.scalar_one()
