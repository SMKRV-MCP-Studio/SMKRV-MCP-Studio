"""CRUD endpoints for agent access tokens."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.agent_token import (
    AgentTokenCreate,
    AgentTokenCreated,
    AgentTokenList,
    AgentTokenResponse,
)
from app.services.agent_auth import (
    generate_agent_token,
    list_agent_tokens,
    revoke_agent_token,
    sync_agent_token_usage_from_redis,
)

router = APIRouter()


@router.post("/agent-tokens", response_model=AgentTokenCreated, status_code=201)
async def create_token(
    data: AgentTokenCreate, db: AsyncSession = Depends(get_db)
) -> dict:
    """Create a temporary agent token. The plaintext is returned once."""
    plaintext, token = await generate_agent_token(
        db, name=data.name, duration_minutes=data.duration_minutes
    )
    return {
        "token": plaintext,
        "id": token.id,
        "name": token.name,
        "token_prefix": token.token_prefix,
        "expires_at": token.expires_at,
        "revoked": token.revoked,
        "last_used_at": token.last_used_at,
        "last_ip": token.last_ip,
        "last_country": token.last_country,
        "scopes": token.scopes,
        "created_at": token.created_at,
    }


@router.get("/agent-tokens", response_model=AgentTokenList)
async def list_tokens(
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    show_all: Annotated[bool, Query()] = False,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List agent tokens. Default: all active + 10 latest inactive. show_all=true for full list."""
    # Sync usage data from Redis to DB before returning
    await sync_agent_token_usage_from_redis(db)
    items, total = await list_agent_tokens(db, skip=skip, limit=limit, show_all=show_all)
    return {"items": items, "total": total}


@router.get("/agent-tokens/{token_id}", response_model=AgentTokenResponse)
async def get_token(
    token_id: str, db: AsyncSession = Depends(get_db)
) -> AgentTokenResponse:
    """Get a specific agent token."""
    from sqlalchemy import select

    from app.models.agent_token import AgentToken

    result = await db.execute(
        select(AgentToken).where(AgentToken.id == token_id)
    )
    token = result.scalar_one_or_none()
    if not token:
        raise HTTPException(status_code=404, detail="Token not found")
    return token  # type: ignore[return-value]


@router.delete("/agent-tokens/{token_id}", status_code=204)
async def delete_token(
    token_id: str, db: AsyncSession = Depends(get_db)
) -> None:
    """Revoke an agent token."""
    success = await revoke_agent_token(db, token_id)
    if not success:
        raise HTTPException(status_code=404, detail="Token not found")
