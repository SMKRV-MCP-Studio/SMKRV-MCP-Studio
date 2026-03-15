"""Pydantic schemas for Connection entity."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.db_registry import DB_TYPE_PATTERN, REQUIRED_EXTRA_FIELDS
from app.services.crypto import mask_sensitive_extra


class ConnectionCreate(BaseModel):
    """Schema for creating a connection. Password in plaintext (encrypted on save)."""

    name: str = Field(..., min_length=1, max_length=255)
    db_type: str = Field(..., pattern=DB_TYPE_PATTERN)
    host: str = Field(default="", max_length=255)
    port: int = Field(default=5432, ge=1, le=65535)
    database: str = Field(default="", max_length=255)
    username: str = Field(default="", max_length=255)
    password: str = Field(default="", max_length=1024)
    ssl_mode: str = Field(default="prefer", max_length=20)
    pool_min_size: int = Field(default=2, ge=1, le=100)
    pool_max_size: int = Field(default=10, ge=1, le=100)
    extra_params: dict | None = Field(default=None)

    @field_validator("extra_params")
    @classmethod
    def validate_extra_params_size(cls, v: dict | None) -> dict | None:
        if v is not None:
            import json
            if len(json.dumps(v)) > 65536:
                raise ValueError("extra_params JSON too large (max 64KB)")
        return v
    is_active: bool = True
    max_concurrent_queries: int = Field(default=5, ge=1, le=200)
    queue_timeout_seconds: int = Field(default=30, ge=1, le=300)
    queue_enabled: bool = True

    @model_validator(mode="after")
    def validate_by_db_type(self):
        """Validate fields based on db_type — cloud DBs use extra_params instead of host/port."""
        db_type = self.db_type

        # Standard DBs require host, database, username, password
        if db_type not in ("snowflake", "bigquery"):
            if not self.host:
                raise ValueError("host is required for this database type")
            if not self.username:
                raise ValueError("username is required for this database type")
            if not self.password:
                raise ValueError("password is required for this database type")
            # Cassandra uses keyspace (in extra_params) instead of database
            if db_type != "cassandra" and not self.database:
                raise ValueError("database is required for this database type")

        # Validate required extra_params fields
        required = REQUIRED_EXTRA_FIELDS.get(db_type, [])
        if required:
            extra = self.extra_params or {}
            missing = [f for f in required if not extra.get(f)]
            if missing:
                raise ValueError(
                    f"extra_params must include: {', '.join(missing)} for {db_type}"
                )

        return self


class ConnectionUpdate(BaseModel):
    """Schema for updating a connection. All fields optional."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    db_type: str | None = Field(default=None, pattern=DB_TYPE_PATTERN)
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    database: str | None = Field(default=None, max_length=255)
    username: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, min_length=1, max_length=1024)
    ssl_mode: str | None = Field(default=None, max_length=20)
    pool_min_size: int | None = Field(default=None, ge=1, le=100)
    pool_max_size: int | None = Field(default=None, ge=1, le=100)
    extra_params: dict | None = None

    @field_validator("extra_params")
    @classmethod
    def validate_extra_params_size(cls, v: dict | None) -> dict | None:
        if v is not None:
            import json
            if len(json.dumps(v)) > 65536:
                raise ValueError("extra_params JSON too large (max 64KB)")
        return v
    is_active: bool | None = None
    max_concurrent_queries: int | None = Field(default=None, ge=1, le=200)
    queue_timeout_seconds: int | None = Field(default=None, ge=1, le=300)
    queue_enabled: bool | None = None
    version: int | None = Field(
        default=None,
        description="Expected version for optimistic locking (OL-1)",
    )


class ConnectionResponse(BaseModel):
    """Schema for returning a connection. Password and sensitive extra_params NEVER exposed."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    db_type: str
    host: str
    port: int
    database: str
    username: str
    password_masked: str = "••••••"
    ssl_mode: str
    pool_min_size: int
    pool_max_size: int
    extra_params: dict | None
    is_active: bool
    version: int
    max_concurrent_queries: int
    queue_timeout_seconds: int
    queue_enabled: bool
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def mask_sensitive_fields(self):
        """Mask sensitive values in extra_params (e.g. credentials_json → ••••••)."""
        self.extra_params = mask_sensitive_extra(self.extra_params)
        return self


class ConnectionList(BaseModel):
    """Schema for paginated connection list."""

    items: list[ConnectionResponse]
    total: int


class ConnectionTestResult(BaseModel):
    """Result of testing a database connection."""

    success: bool
    message: str
    latency_ms: float = 0.0
    hints: list[str] | None = None


class ConnectionHealthItem(BaseModel):
    """Health status of a single connection."""

    id: str
    name: str
    db_type: str
    healthy: bool
    latency_ms: float = 0.0
    error: str | None = None
    cached: bool = False


class ConnectionHealthList(BaseModel):
    """Batch health check response."""

    items: list[ConnectionHealthItem]
    total: int
    healthy_count: int
