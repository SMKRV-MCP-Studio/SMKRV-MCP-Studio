"""CRUD endpoints for MCP resources."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.resource import Resource
from app.schemas.resource import (
    ResourceCreate,
    ResourceList,
    ResourceResponse,
    ResourceUpdate,
)
from app.services.history import compute_changes, model_to_dict, record_change
from app.services.prompt_guard import guard_and_log

router = APIRouter()


@router.post("/resources", response_model=ResourceResponse, status_code=201)
async def create_resource(
    data: ResourceCreate, db: AsyncSession = Depends(get_db)
) -> Resource:
    """Create a new resource."""
    await guard_and_log("resource", data.model_dump(), db)
    resource = Resource(**data.model_dump())
    db.add(resource)
    await db.flush()
    await record_change(
        db, entity_type="resource", entity_id=resource.id,
        entity_name=resource.name, action="create",
    )
    await db.commit()
    await db.refresh(resource)
    return resource


@router.get("/resources", response_model=ResourceList)
async def list_resources(
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List all resources with pagination."""
    total_result = await db.execute(select(func.count(Resource.id)))
    total = total_result.scalar_one()

    result = await db.execute(
        select(Resource).offset(skip).limit(limit).order_by(Resource.name)
    )
    items = list(result.scalars().all())
    return {"items": items, "total": total}


@router.get("/resources/{resource_id}", response_model=ResourceResponse)
async def get_resource(
    resource_id: str, db: AsyncSession = Depends(get_db)
) -> Resource:
    """Get a resource by ID."""
    result = await db.execute(select(Resource).where(Resource.id == resource_id))
    resource = result.scalar_one_or_none()
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    return resource


@router.patch("/resources/{resource_id}", response_model=ResourceResponse)
async def update_resource(
    resource_id: str, data: ResourceUpdate, db: AsyncSession = Depends(get_db)
) -> Resource:
    """Update a resource."""
    result = await db.execute(select(Resource).where(Resource.id == resource_id))
    resource = result.scalar_one_or_none()
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    # OL-1: Optimistic locking — reject if client version is stale
    if data.version is not None and data.version != resource.version:
        raise HTTPException(
            status_code=409,
            detail=f"Version conflict: expected {data.version}, current {resource.version}",
        )

    await guard_and_log("resource", data.model_dump(exclude_unset=True), db)

    before = model_to_dict(resource)
    update_data = data.model_dump(exclude_unset=True)
    update_data.pop("version", None)  # Don't set version from client
    for field, value in update_data.items():
        setattr(resource, field, value)

    # Auto-increment version on every update
    resource.version = (resource.version or 0) + 1

    changes = compute_changes(before, data.model_dump(exclude_unset=True))
    await record_change(
        db, entity_type="resource", entity_id=resource.id,
        entity_name=resource.name, action="update", snapshot=before, changes=changes,
    )
    await db.commit()
    await db.refresh(resource)
    return resource


@router.delete("/resources/{resource_id}", status_code=204)
async def delete_resource(
    resource_id: str, db: AsyncSession = Depends(get_db)
) -> None:
    """Delete a resource."""
    result = await db.execute(select(Resource).where(Resource.id == resource_id))
    resource = result.scalar_one_or_none()
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    before = model_to_dict(resource)
    await record_change(
        db, entity_type="resource", entity_id=resource.id,
        entity_name=resource.name, action="delete", snapshot=before,
    )
    await db.delete(resource)
    await db.commit()
