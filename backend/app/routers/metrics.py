"""Operational metrics router — proxies to MCP container's /metrics/* endpoints."""

import logging
from typing import Annotated

import httpx
from fastapi import APIRouter, Query

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/metrics/stats")
async def metrics_stats() -> dict:
    """Get per-tool aggregate metrics from the MCP server."""
    url = f"http://{settings.fastmcp_host}:{settings.fastmcp_port}/metrics/stats"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.json()
    except Exception as exc:
        logger.debug("Failed to fetch metrics stats: %s", exc)
    return {"tools": {}}


@router.get("/metrics/timeseries")
async def metrics_timeseries(hours: Annotated[int, Query(ge=1, le=2160)] = 1) -> dict:
    """Get time-series metrics from the MCP server (up to 90 days)."""
    url = f"http://{settings.fastmcp_host}:{settings.fastmcp_port}/metrics/timeseries"
    timeout = 5.0 if hours <= 24 else 15.0
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, params={"hours": hours})
            if resp.status_code == 200:
                return resp.json()
    except Exception as exc:
        logger.debug("Failed to fetch timeseries metrics: %s", exc)
    return {"points": [], "hours": hours, "granularity": "minute"}


@router.get("/metrics/entity-summary")
async def metrics_entity_summary() -> dict:
    """Get per-tool call/error summaries for 1d/30d/90d windows."""
    url = f"http://{settings.fastmcp_host}:{settings.fastmcp_port}/metrics/entity-summary"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.json()
    except Exception as exc:
        logger.debug("Failed to fetch entity summary: %s", exc)
    return {"tools": {}, "total_calls_1d": 0, "total_calls_30d": 0, "total_calls_90d": 0}
