"""Queue metrics router — proxies to MCP container's /queue/metrics endpoint."""

import logging

import httpx
from fastapi import APIRouter

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/queue/metrics")
async def queue_metrics() -> dict:
    """Get per-connection queue metrics from the MCP server."""
    url = f"http://{settings.fastmcp_host}:{settings.fastmcp_port}/queue/metrics"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.json()
    except Exception as exc:
        logger.debug("Failed to fetch queue metrics: %s", exc)
    return {"redis_connected": False, "connections": {}}
