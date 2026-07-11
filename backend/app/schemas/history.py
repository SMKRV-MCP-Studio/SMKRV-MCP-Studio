"""Pydantic schemas for change history."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ChangeHistoryResponse(BaseModel):
    """Schema for a single change history entry."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    entity_type: str
    entity_id: str
    entity_name: str
    action: str
    snapshot: dict[str, Any] | None = None
    changes: dict[str, Any] | None = None
    created_at: datetime


class ChangeHistoryList(BaseModel):
    """Paginated list of change history entries."""

    items: list[ChangeHistoryResponse]
    total: int
