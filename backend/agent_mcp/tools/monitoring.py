"""MCP tools for monitoring MCP server metrics and Redis queue status."""

from __future__ import annotations

from agent_mcp import backend_client


async def get_metrics_stats() -> dict:
    """Get aggregate operational metrics for MCP tools.

    Shows per-tool call counts, response times, p95 latency, etc.

    Returns:
        Object with per-tool metrics arrays.
    """
    return await backend_client.get("/metrics/stats")


_PERIOD_TO_HOURS = {"1h": 1, "6h": 6, "24h": 24, "7d": 168}


async def get_metrics_timeseries(
    period: str = "1h",
) -> dict:
    """Get time-series metrics data for charting.

    Args:
        period: Time period, one of '1h', '6h', '24h', '7d'.

    Returns:
        Object with time-series data points (timestamp, value arrays).
    """
    params: dict = {"hours": _PERIOD_TO_HOURS.get(period, 1)}
    return await backend_client.get("/metrics/timeseries", params=params)


async def get_queue_metrics() -> dict:
    """Get Redis queue status and semaphore metrics.

    Shows per-connection concurrency info, queue depths, wait times.

    Returns:
        Object with queue metrics per connection.
    """
    return await backend_client.get("/queue/metrics")
