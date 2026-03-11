"""CRUD endpoints for MCP prompts."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.prompt import Prompt
from app.schemas.prompt import (
    PromptCreate,
    PromptList,
    PromptResponse,
    PromptUpdate,
)
from app.services.history import compute_changes, model_to_dict, record_change
from app.services.prompt_guard import guard_and_log

router = APIRouter()


@router.post("/prompts", response_model=PromptResponse, status_code=201)
async def create_prompt(
    data: PromptCreate, db: AsyncSession = Depends(get_db)
) -> Prompt:
    """Create a new prompt."""
    await guard_and_log("prompt", data.model_dump(), db)
    prompt = Prompt(**data.model_dump())
    db.add(prompt)
    await db.flush()
    await record_change(
        db, entity_type="prompt", entity_id=prompt.id,
        entity_name=prompt.name, action="create",
    )
    await db.commit()
    await db.refresh(prompt)
    return prompt


@router.get("/prompts", response_model=PromptList)
async def list_prompts(
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List all prompts with pagination."""
    total_result = await db.execute(select(func.count(Prompt.id)))
    total = total_result.scalar_one()

    result = await db.execute(
        select(Prompt).offset(skip).limit(limit).order_by(Prompt.name)
    )
    items = list(result.scalars().all())
    return {"items": items, "total": total}


@router.get("/prompts/{prompt_id}", response_model=PromptResponse)
async def get_prompt(
    prompt_id: str, db: AsyncSession = Depends(get_db)
) -> Prompt:
    """Get a prompt by ID."""
    result = await db.execute(select(Prompt).where(Prompt.id == prompt_id))
    prompt = result.scalar_one_or_none()
    if not prompt:
        raise HTTPException(status_code=404, detail="Prompt not found")
    return prompt


@router.patch("/prompts/{prompt_id}", response_model=PromptResponse)
async def update_prompt(
    prompt_id: str, data: PromptUpdate, db: AsyncSession = Depends(get_db)
) -> Prompt:
    """Update a prompt."""
    result = await db.execute(select(Prompt).where(Prompt.id == prompt_id))
    prompt = result.scalar_one_or_none()
    if not prompt:
        raise HTTPException(status_code=404, detail="Prompt not found")

    # OL-1: Optimistic locking — reject if client version is stale
    if data.version is not None and data.version != prompt.version:
        raise HTTPException(
            status_code=409,
            detail=f"Version conflict: expected {data.version}, current {prompt.version}",
        )

    await guard_and_log("prompt", data.model_dump(exclude_unset=True), db)

    before = model_to_dict(prompt)
    update_data = data.model_dump(exclude_unset=True)
    update_data.pop("version", None)  # Don't set version from client
    for field, value in update_data.items():
        setattr(prompt, field, value)

    # Auto-increment version on every update
    prompt.version = (prompt.version or 0) + 1

    changes = compute_changes(before, data.model_dump(exclude_unset=True))
    await record_change(
        db, entity_type="prompt", entity_id=prompt.id,
        entity_name=prompt.name, action="update", snapshot=before, changes=changes,
    )
    await db.commit()
    await db.refresh(prompt)
    return prompt


@router.delete("/prompts/{prompt_id}", status_code=204)
async def delete_prompt(
    prompt_id: str, db: AsyncSession = Depends(get_db)
) -> None:
    """Delete a prompt."""
    result = await db.execute(select(Prompt).where(Prompt.id == prompt_id))
    prompt = result.scalar_one_or_none()
    if not prompt:
        raise HTTPException(status_code=404, detail="Prompt not found")

    before = model_to_dict(prompt)
    await record_change(
        db, entity_type="prompt", entity_id=prompt.id,
        entity_name=prompt.name, action="delete", snapshot=before,
    )
    await db.delete(prompt)
    await db.commit()
