"""Pydantic schemas for queue metrics."""

from pydantic import BaseModel


class ConnectionQueueMetrics(BaseModel):
    """Per-connection queue metrics from Redis."""

    active_queries: int = 0
    queue_depth: int = 0
    total_queries: int = 0
    total_wait_ms: float = 0.0
    avg_wait_ms: float = 0.0
    total_errors: int = 0
    peak_active: int = 0


class QueueMetricsResponse(BaseModel):
    """Response from the queue metrics endpoint."""

    redis_connected: bool = False
    connections: dict[str, ConnectionQueueMetrics] = {}
    error: str | None = None
