"""MCP tools for monitoring MCP server metrics and Redis queue status."""

from __future__ import annotations

from agent_mcp import backend_client


async def get_metrics_stats(
    connection_id: str | None = None,
) -> dict:
    """Get aggregate operational metrics for MCP tools.

    Shows per-tool call counts, response times, p95 latency, etc.

    Args:
        connection_id: Optional filter by connection UUID.

    Returns:
        Object with per-tool metrics arrays.
    """
    params: dict = {}
    if connection_id:
        params["connection_id"] = connection_id
    return await backend_client.get("/metrics/stats", params=params)


async def get_metrics_timeseries(
    tool_name: str | None = None,
    period: str = "1h",
) -> dict:
    """Get time-series metrics data for charting.

    Args:
        tool_name: Optional tool name to filter metrics.
        period: Time period — '1h', '6h', '24h', '7d'.

    Returns:
        Object with time-series data points (timestamp, value arrays).
    """
    params: dict = {"period": period}
    if tool_name:
        params["tool_name"] = tool_name
    return await backend_client.get("/metrics/timeseries", params=params)


async def get_queue_metrics() -> dict:
    """Get Redis queue status and semaphore metrics.

    Shows per-connection concurrency info, queue depths, wait times.

    Returns:
        Object with queue metrics per connection.
    """
    return await backend_client.get("/queue/metrics")
