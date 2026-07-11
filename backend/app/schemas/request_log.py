"""Pydantic schemas for request logs."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class RequestLogResponse(BaseModel):
    """Schema for a single request log entry."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    tool_name: str
    connection_id: str
    duration_ms: float
    success: bool
    error_message: str | None = None
    created_at: datetime


class RequestLogList(BaseModel):
    """Paginated list of request log entries."""

    items: list[RequestLogResponse]
    total: int
