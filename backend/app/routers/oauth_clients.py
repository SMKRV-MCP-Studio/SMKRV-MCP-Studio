"""CRUD endpoints for OAuth2 clients used by AI agents."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.oauth_client import OAuthClient
from app.schemas.oauth_client import (
    OAuthClientCreate,
    OAuthClientCreated,
    OAuthClientList,
    OAuthClientResponse,
)
from app.services.agent_auth import (
    create_oauth_client,
    list_oauth_clients,
    revoke_oauth_client,
)

router = APIRouter()


@router.post("/oauth-clients", response_model=OAuthClientCreated, status_code=201)
async def create_client(
    data: OAuthClientCreate, db: AsyncSession = Depends(get_db)
) -> dict:
    """Create an OAuth2 client. Secret is returned once."""
    client_id, client_secret, client = await create_oauth_client(
        db, name=data.name, idle_timeout_minutes=data.idle_timeout_minutes
    )
    return {
        "client_secret": client_secret,
        "id": client.id,
        "name": client.name,
        "client_id": client.client_id,
        "client_secret_prefix": client.client_secret_prefix,
        "idle_timeout_seconds": client.idle_timeout_seconds,
        "revoked": client.revoked,
        "last_used_at": client.last_used_at,
        "last_ip": client.last_ip,
        "last_country": client.last_country,
        "scopes": client.scopes,
        "created_at": client.created_at,
    }


@router.get("/oauth-clients", response_model=OAuthClientList)
async def list_clients(
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List all OAuth2 clients."""
    items, total = await list_oauth_clients(db, skip=skip, limit=limit)
    return {"items": items, "total": total}


@router.get("/oauth-clients/{client_db_id}", response_model=OAuthClientResponse)
async def get_client(
    client_db_id: str, db: AsyncSession = Depends(get_db)
) -> OAuthClientResponse:
    """Get a specific OAuth2 client."""
    result = await db.execute(
        select(OAuthClient).where(OAuthClient.id == client_db_id)
    )
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(status_code=404, detail="OAuth client not found")
    return client  # type: ignore[return-value]


@router.delete("/oauth-clients/{client_db_id}", status_code=204)
async def delete_client(
    client_db_id: str, db: AsyncSession = Depends(get_db)
) -> None:
    """Revoke an OAuth2 client and all its sessions."""
    success = await revoke_oauth_client(db, client_db_id)
    if not success:
        raise HTTPException(status_code=404, detail="OAuth client not found")
