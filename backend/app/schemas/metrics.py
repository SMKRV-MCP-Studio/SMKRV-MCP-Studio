"""Pydantic schemas for operational metrics."""

from pydantic import BaseModel


class ToolStats(BaseModel):
    """Per-tool aggregate statistics."""

    call_count: int = 0
    avg_duration_ms: float = 0.0
    min_duration_ms: float = 0.0
    max_duration_ms: float = 0.0
    error_count: int = 0
    error_rate: float = 0.0
    last_called_at: float = 0.0
    p95_bucket: str = "N/A"
    histogram: dict[str, int] = {}


class MetricsStatsResponse(BaseModel):
    """Response from the /metrics/stats endpoint."""

    tools: dict[str, ToolStats] = {}
    error: str | None = None


class TimeseriesPoint(BaseModel):
    """Single minute-level data point."""

    timestamp: str
    calls: int = 0
    errors: int = 0
    avg_duration_ms: float = 0.0
    tools: dict[str, int] = {}


class MetricsTimeseriesResponse(BaseModel):
    """Response from the /metrics/timeseries endpoint."""

    points: list[TimeseriesPoint] = []
    hours: int = 1
    error: str | None = None
