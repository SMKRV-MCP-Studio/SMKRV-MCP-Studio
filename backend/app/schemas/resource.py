"""Pydantic schemas for Resource entity."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

_ENTITY_NAME_PATTERN = r"^[a-zA-Z_][a-zA-Z0-9_\-]{0,254}$"


class ResourceCreate(BaseModel):
    """Schema for creating a resource."""

    connection_id: str | None = None
    name: str = Field(..., min_length=1, max_length=255, pattern=_ENTITY_NAME_PATTERN)
    uri_template: str = Field(..., min_length=1, max_length=1024)
    description: str = Field(..., min_length=1, max_length=5000)
    sql_query: str | None = Field(default=None, max_length=100_000)
    static_content: str | None = Field(default=None, max_length=1_000_000)
    mime_type: str = Field(default="application/json", max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=50)
    is_enabled: bool = True


class ResourceUpdate(BaseModel):
    """Schema for updating a resource. All fields optional."""

    connection_id: str | None = None
    name: str | None = Field(
        default=None, min_length=1, max_length=255, pattern=_ENTITY_NAME_PATTERN,
    )
    uri_template: str | None = Field(default=None, min_length=1, max_length=1024)
    description: str | None = Field(default=None, max_length=5000)
    sql_query: str | None = Field(default=None, max_length=100_000)
    static_content: str | None = Field(default=None, max_length=1_000_000)
    mime_type: str | None = Field(default=None, max_length=100)
    tags: list[str] | None = Field(default=None, max_length=50)
    is_enabled: bool | None = None
    version: int | None = Field(
        default=None,
        description="Expected version for optimistic locking (OL-1)",
    )


class ResourceResponse(BaseModel):
    """Schema for returning a resource."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    connection_id: str | None
    name: str
    uri_template: str
    description: str
    sql_query: str | None
    static_content: str | None
    mime_type: str
    tags: list[str]
    version: int
    is_enabled: bool
    created_at: datetime
    updated_at: datetime


class ResourceList(BaseModel):
    """Schema for paginated resource list."""

    items: list[ResourceResponse]
    total: int
