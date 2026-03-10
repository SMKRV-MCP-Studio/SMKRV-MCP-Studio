"""MCP tools for deploying and managing the generated MCP server."""

from __future__ import annotations

import asyncio
import logging

from agent_mcp import backend_client

logger = logging.getLogger(__name__)

# Track background deploy task and its result
_deploy_task: asyncio.Task | None = None
_last_deploy_result: dict | None = None

# Deploy needs a longer timeout — codegen + nginx regen + up to 10s health polling
_DEPLOY_TIMEOUT = 120.0


async def _do_deploy() -> None:
    """Background deploy — stores result for retrieval via get_deploy_status."""
    global _last_deploy_result
    try:
        result = await backend_client.post("/deploy", timeout=_DEPLOY_TIMEOUT)
        if result is None:
            _last_deploy_result = {
                "deployed": False,
                "message": "Backend returned empty response",
                "errors": ["Empty response from /deploy endpoint"],
            }
            logger.warning("Backend deploy returned empty response")
            return
        _last_deploy_result = result
        deployed = result.get("deployed", False)
        msg = result.get("message", "")
        logger.info("Background deploy finished: deployed=%s message=%s", deployed, msg)
    except backend_client.BackendError as exc:
        _last_deploy_result = {
            "deployed": False,
            "message": f"Deploy failed (HTTP {exc.status_code})",
            "errors": [f"Backend returned {exc.status_code}"],
        }
        logger.exception("Background deploy failed")
    except Exception:
        _last_deploy_result = {
            "deployed": False,
            "message": "Deploy failed unexpectedly",
            "errors": ["Internal error — check server logs"],
        }
        logger.exception("Background deploy failed")


async def deploy_server() -> dict:
    """Trigger code generation and MCP server deploy (non-blocking).

    Starts deploy in the background and returns immediately.
    Use get_deploy_status() to check progress and result.

    Returns:
        Object with 'status' ('deploying' or 'already_in_progress') and 'message'.
    """
    global _deploy_task, _last_deploy_result
    if _deploy_task is not None and not _deploy_task.done():
        return {
            "status": "already_in_progress",
            "message": "Deploy is already running. Use get_deploy_status to check progress.",
        }

    _last_deploy_result = None
    _deploy_task = asyncio.create_task(_do_deploy())
    return {
        "status": "deploying",
        "message": "Deploy started in background. Use get_deploy_status to check progress.",
    }


async def stop_server() -> dict:
    """Stop the running MCP server.

    Returns:
        Object with 'status' and 'message'.
    """
    result = await backend_client.post("/deploy/stop")
    if result is None:
        return {"status": "error", "message": "Backend returned empty response"}
    return result


async def get_deploy_status() -> dict:
    """Get the current status of the deployed MCP server and last deploy result.

    Returns:
        Object with 'status' (running/stopped/error), 'message', 'pid',
        and optionally 'last_deploy' with the result of the last deploy_server() call.
    """
    status = await backend_client.get("/deploy/status")
    if _deploy_task is not None and not _deploy_task.done():
        status["deploy_in_progress"] = True
    if _last_deploy_result is not None:
        status["last_deploy"] = _last_deploy_result
    return status
