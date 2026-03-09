"""FastAPI dependencies for authentication."""

import hmac
import logging

from fastapi import Depends, HTTPException, Request, WebSocket, WebSocketException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.admin_user import AdminUser
from app.services.auth import decode_access_token, get_cookie_name

logger = logging.getLogger(__name__)


async def get_agent_or_admin(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AdminUser:
    """Accept either admin cookie OR X-Agent-Service-Token header.

    The agent-mcp container sends an internal service token when proxying
    agent requests to the backend. This allows the backend to authenticate
    both UI admin sessions and agent-mcp internal calls.
    """
    # 1. Check for internal service token (agent-mcp → backend)
    service_token = request.headers.get("X-Agent-Service-Token")
    has_token = service_token and settings.agent_service_token
    if has_token and hmac.compare_digest(
        service_token, settings.agent_service_token
    ):
        result = await db.execute(select(AdminUser).limit(1))
        admin = result.scalar_one_or_none()
        if admin:
            return admin

    # 2. Fallback to cookie auth (normal UI access)
    return await get_current_admin(request, db)


async def get_current_admin(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AdminUser:
    """Validate JWT from httpOnly cookie and return the admin user.

    Raises 401 if cookie is missing, token is invalid/expired, or user not found.
    """
    token = request.cookies.get(get_cookie_name())
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    username = decode_access_token(token)
    if username is None:
        raise HTTPException(status_code=401, detail="Session expired")

    result = await db.execute(
        select(AdminUser).where(AdminUser.username == username)
    )
    admin = result.scalar_one_or_none()
    if admin is None:
        raise HTTPException(status_code=401, detail="User not found")

    return admin


async def require_admin_ws(
    websocket: WebSocket,
    db: AsyncSession = Depends(get_db),
) -> AdminUser:
    """Validate JWT from httpOnly cookie on a WebSocket connection.

    Raises WebSocketException to reject the connection before accept().
    """
    token = websocket.cookies.get(get_cookie_name())
    if not token:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Not authenticated")

    username = decode_access_token(token)
    if username is None:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Session expired")

    result = await db.execute(
        select(AdminUser).where(AdminUser.username == username)
    )
    admin = result.scalar_one_or_none()
    if admin is None:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="User not found")

    return admin
