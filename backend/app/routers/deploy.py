"""Deploy endpoints — code generation trigger, MCP status, and log streaming."""

import asyncio
import logging
from collections import deque
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_agent_or_admin, require_admin_ws
from app.schemas.server import DeployResponse, DeployStatus
from app.services.deployer import Deployer

router = APIRouter()
logger = logging.getLogger(__name__)

# In-memory ring buffer for deploy logs (last 200 lines)
_deploy_logs: deque[dict] = deque(maxlen=200)
_log_subscribers: set[WebSocket] = set()

# Deploy mutex — prevents concurrent deploys from corrupting generated files (DR-1)
_deploy_lock = asyncio.Lock()

# WebSocket heartbeat interval (seconds) (ERR-12)
_WS_HEARTBEAT_INTERVAL = 30


async def _safe_send(ws: WebSocket, entry: dict) -> None:
    """Send a JSON message to a WebSocket, discarding on failure (ERR-09)."""
    try:
        await ws.send_json(entry)
    except Exception:
        _log_subscribers.discard(ws)


def _add_log(level: str, message: str) -> None:
    """Append a log entry and broadcast to WebSocket subscribers."""
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "level": level,
        "message": message,
    }
    _deploy_logs.append(entry)
    # Non-blocking broadcast to connected clients (WS-4: use create_task directly)
    for ws in list(_log_subscribers):
        asyncio.create_task(_safe_send(ws, entry))


@router.post("/deploy", response_model=DeployResponse, dependencies=[Depends(get_agent_or_admin)])
async def deploy(db: AsyncSession = Depends(get_db)) -> dict:
    """Generate all files from current configuration.

    Runs the code generator and writes files to the generated/ directory.
    The MCP container with reload=True picks up changes automatically.
    Returns 409 if another deploy is already in progress.
    """
    # DR-1: Reject concurrent deploys
    if _deploy_lock.locked():
        raise HTTPException(
            status_code=409,
            detail="Deploy already in progress. Please wait for it to complete.",
        )

    async with _deploy_lock:
        _add_log("INFO", "Deploy started — generating MCP server files...")

        deployer = Deployer()
        result = await deployer.deploy(db)

        if result.deployed:
            _add_log("INFO", f"Deploy successful — {result.files_generated} files generated")
            for warning in result.warnings:
                _add_log("WARNING", warning)

            # Wait for FastMCP container to pick up new files / restart
            _add_log("INFO", "Waiting for FastMCP to reload...")
            running = False
            for _ in range(5):
                await asyncio.sleep(2)
                status = await deployer.get_status()
                if status.get("status") == "running":
                    running = True
                    break

            if running:
                _add_log("INFO", "FastMCP server is running and healthy")
            else:
                _add_log("WARNING", f"FastMCP status: {status.get('message', 'unknown')}")
                _add_log("INFO", "Server may still be starting — check status in a moment")
        else:
            _add_log("ERROR", f"Deploy failed: {result.message}")
            for error in result.errors:
                _add_log("ERROR", error)

        return {
            "deployed": result.deployed,
            "message": result.message,
            "files_generated": result.files_generated,
            "errors": result.errors,
            "warnings": result.warnings,
        }


@router.post("/deploy/stop", dependencies=[Depends(get_agent_or_admin)])
async def stop_server() -> dict:
    """Stop the MCP server by writing a stop flag.

    The MCP container's watcher thread detects the flag within ~2s,
    then the container auto-restarts but exits immediately on start.
    """
    _add_log("INFO", "Stopping MCP server...")
    deployer = Deployer()
    result = await deployer.stop()

    # Poll for up to 8 seconds to confirm the server stopped
    for _ in range(4):
        await asyncio.sleep(2)
        status = await deployer.get_status()
        if status.get("status") == "stopped":
            _add_log("INFO", "MCP server stopped successfully")
            result["status"] = "stopped"
            return result

    status = await deployer.get_status()
    if status.get("status") == "stopped":
        _add_log("INFO", "MCP server stopped successfully")
    else:
        _add_log("WARNING", f"Server may still be running: {status.get('message', '')}")

    return result


@router.get(
    "/deploy/status",
    response_model=DeployStatus,
    dependencies=[Depends(get_agent_or_admin)],
)
async def deploy_status() -> dict:
    """Get MCP server status (health check)."""
    deployer = Deployer()
    return await deployer.get_status()


@router.websocket("/deploy/logs")
async def deploy_logs_ws(
    websocket: WebSocket,
    _admin=Depends(require_admin_ws),
) -> None:
    """WebSocket endpoint for streaming deploy logs.

    Sends existing log history on connect, then streams new entries.
    Includes heartbeat ping/pong to detect half-open connections (ERR-12).
    """
    await websocket.accept()
    _log_subscribers.add(websocket)

    try:
        # Send existing log history
        for entry in list(_deploy_logs):
            await websocket.send_json(entry)

        # Keep connection alive with heartbeat (ERR-12)
        while True:
            try:
                # Wait for client message with heartbeat timeout
                await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=_WS_HEARTBEAT_INTERVAL,
                )
            except TimeoutError:
                # No message received — send ping to check connection
                try:
                    await websocket.send_json({"type": "ping"})
                except Exception:
                    break
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.debug("WebSocket connection error in deploy logs")
    finally:
        _log_subscribers.discard(websocket)
