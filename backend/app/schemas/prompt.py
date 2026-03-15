"""Pydantic schemas for Prompt entity."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.constants import ENTITY_NAME_PATTERN as _ENTITY_NAME_PATTERN


class PromptArgumentSchema(BaseModel):
    """Schema for a prompt argument (inline JSON)."""

    name: str = Field(..., min_length=1, pattern=_ENTITY_NAME_PATTERN)
    description: str = Field(..., min_length=1, max_length=1000)
    required: bool = True


class PromptCreate(BaseModel):
    """Schema for creating a prompt."""

    name: str = Field(..., min_length=1, max_length=255, pattern=_ENTITY_NAME_PATTERN)
    title: str | None = Field(default=None, max_length=255)
    description: str = Field(..., min_length=1, max_length=5000)
    template: str = Field(..., min_length=1, max_length=100_000)
    arguments: list[PromptArgumentSchema] = Field(default_factory=list, max_length=50)
    tags: list[str] = Field(default_factory=list, max_length=50)
    is_enabled: bool = True


class PromptUpdate(BaseModel):
    """Schema for updating a prompt. All fields optional."""

    name: str | None = Field(
        default=None, min_length=1, max_length=255, pattern=_ENTITY_NAME_PATTERN,
    )
    title: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=5000)
    template: str | None = Field(default=None, max_length=100_000)
    arguments: list[PromptArgumentSchema] | None = Field(default=None, max_length=50)
    tags: list[str] | None = Field(default=None, max_length=50)
    is_enabled: bool | None = None
    version: int | None = Field(
        default=None,
        description="Expected version for optimistic locking (OL-1)",
    )


class PromptResponse(BaseModel):
    """Schema for returning a prompt."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    title: str | None
    description: str
    template: str
    arguments: list[PromptArgumentSchema]
    tags: list[str]
    version: int
    is_enabled: bool
    created_at: datetime
    updated_at: datetime


class PromptList(BaseModel):
    """Schema for paginated prompt list."""

    items: list[PromptResponse]
    total: int
