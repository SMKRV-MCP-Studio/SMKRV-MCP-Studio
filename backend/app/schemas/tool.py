"""Pydantic schemas for Tool and Parameter entities."""

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

VALID_PARAM_TYPES = {"str", "int", "float", "bool", "date", "datetime"}


def _validate_param_type(v: str) -> str:
    if v not in VALID_PARAM_TYPES:
        raise ValueError(
            f"Invalid param_type '{v}'. Must be one of: {', '.join(sorted(VALID_PARAM_TYPES))}"
        )
    return v


class ParameterCreate(BaseModel):
    """Schema for creating a parameter inline with a tool."""

    name: str = Field(..., min_length=1, max_length=255, pattern=r"^[a-zA-Z_][a-zA-Z0-9_]{0,254}$")
    param_type: str
    description: str = Field(..., min_length=1, max_length=1000)
    is_required: bool = True
    default_value: str | None = Field(default=None, max_length=1000)
    enum_values: list[str] | None = Field(default=None, max_length=100)
    sort_order: int = Field(default=0, ge=0, le=1000)

    @field_validator("param_type")
    @classmethod
    def validate_param_type(cls, v: str) -> str:
        return _validate_param_type(v)


class ParameterUpdate(BaseModel):
    """Schema for updating a parameter."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    param_type: str | None = None
    description: str | None = Field(default=None, max_length=1000)
    is_required: bool | None = None
    default_value: str | None = Field(default=None, max_length=1000)
    enum_values: list[str] | None = Field(default=None, max_length=100)
    sort_order: int | None = Field(default=None, ge=0, le=1000)

    @field_validator("param_type")
    @classmethod
    def validate_param_type(cls, v: str | None) -> str | None:
        if v is not None:
            return _validate_param_type(v)
        return v


class ParameterResponse(BaseModel):
    """Schema for returning a parameter."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    tool_id: str
    name: str
    param_type: str
    description: str
    is_required: bool
    default_value: str | None
    enum_values: list[str] | None
    sort_order: int


_ENTITY_NAME_PATTERN = r"^[a-zA-Z_][a-zA-Z0-9_\-]{0,254}$"


class ToolCreate(BaseModel):
    """Schema for creating a tool with inline parameters."""

    connection_id: str
    name: str = Field(..., min_length=1, max_length=255, pattern=_ENTITY_NAME_PATTERN)
    description: str = Field(..., min_length=1, max_length=5000)
    sql_query: str = Field(..., min_length=1, max_length=100_000)
    return_type: str = Field(default="list[dict]", max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=50)
    annotations: dict = Field(default_factory=dict)
    cache_ttl: int = Field(default=0, ge=0, le=86400)
    is_enabled: bool = True
    transform_template: str | None = Field(default=None, max_length=100_000)
    parameters: list[ParameterCreate] = Field(default_factory=list, max_length=100)

    @field_validator("annotations")
    @classmethod
    def validate_annotation_keys(cls, v: dict) -> dict:
        if len(v) > 50:
            msg = "Too many annotations (max 50)"
            raise ValueError(msg)
        for key in v:
            if not isinstance(key, str) or not _IDENTIFIER_RE.match(key):
                msg = f"Annotation key '{key}' is not a valid Python identifier"
                raise ValueError(msg)
        return v


class ToolUpdate(BaseModel):
    """Schema for updating a tool. Parameters are replaced entirely."""

    connection_id: str | None = None
    name: str | None = Field(
        default=None, min_length=1, max_length=255, pattern=_ENTITY_NAME_PATTERN,
    )
    description: str | None = Field(default=None, max_length=5000)
    sql_query: str | None = Field(default=None, max_length=100_000)
    return_type: str | None = Field(default=None, max_length=100)
    tags: list[str] | None = None
    annotations: dict | None = None
    cache_ttl: int | None = Field(default=None, ge=0, le=86400)
    is_enabled: bool | None = None
    transform_template: str | None = Field(default=None, max_length=100_000)
    parameters: list[ParameterCreate] | None = Field(default=None, max_length=100)
    version: int | None = Field(
        default=None,
        description="Expected version for optimistic locking (OL-1)",
    )

    @field_validator("annotations")
    @classmethod
    def validate_annotation_keys(cls, v: dict | None) -> dict | None:
        if v is None:
            return v
        if len(v) > 50:
            msg = "Too many annotations (max 50)"
            raise ValueError(msg)
        for key in v:
            if not isinstance(key, str) or not _IDENTIFIER_RE.match(key):
                msg = f"Annotation key '{key}' is not a valid Python identifier"
                raise ValueError(msg)
        return v


class ToolResponse(BaseModel):
    """Schema for returning a tool with nested parameters."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    connection_id: str
    name: str
    description: str
    sql_query: str
    return_type: str
    tags: list[str]
    version: int
    annotations: dict
    cache_ttl: int
    is_enabled: bool
    transform_template: str | None = None
    created_at: datetime
    updated_at: datetime
    parameters: list[ParameterResponse] = Field(default_factory=list)


class ToolList(BaseModel):
    """Schema for paginated tool list."""

    items: list[ToolResponse]
    total: int
