"""Pydantic schemas for Connection entity."""

import ipaddress
import logging
import socket
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.db_registry import DB_TYPE_PATTERN, REQUIRED_EXTRA_FIELDS
from app.services.crypto import mask_sensitive_extra

_logger = logging.getLogger(__name__)

# Docker service names and special addresses that should never be used as connection hosts
_BLOCKED_HOSTNAMES: set[str] = {
    "localhost", "redis", "backend", "mcp", "agent-mcp", "0.0.0.0",
}


def _is_private_ip(addr: str) -> bool:
    """Check if an IP address string falls within blocked ranges.

    Uses Python's built-in ip_address properties which correctly handle:
    - RFC 1918 private (10/8, 172.16/12, 192.168/16)
    - IPv6 ULA (fc00::/7)
    - Loopback (127.0.0.0/8, ::1)
    - Link-local (169.254.0.0/16, fe80::/10)
    - Reserved (0.0.0.0/8, etc.)
    - Multicast
    - IPv6-mapped IPv4 (e.g., ::ffff:127.0.0.1)
    """
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    # Handle IPv6-mapped IPv4 (e.g., ::ffff:127.0.0.1, ::ffff:10.0.0.1)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_reserved
        or ip.is_link_local
        or ip.is_multicast
    )


def _resolve_with_timeout(hostname: str, timeout: float = 2.0) -> list:
    """Resolve hostname with timeout to avoid blocking event loop.

    Returns list of getaddrinfo results, or empty list on timeout/error.
    """
    old_timeout = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(timeout)
        return socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except TimeoutError:
        return []  # Can't resolve in time — allow (will fail at connection time)
    except socket.gaierror:
        return []  # DNS resolution failed — allow (may work at runtime)
    finally:
        socket.setdefaulttimeout(old_timeout)


def _validate_host_ssrf(host: str) -> str:
    """Block SSRF attempts via private network / Docker service hosts.

    Skipped when STUDIO_ALLOW_PRIVATE_NETWORKS=true.
    """
    from app.config import settings

    if settings.allow_private_networks:
        return host

    if not host:
        return host  # some DB types don't need host

    lower = host.lower().strip()

    # Block Docker service names and special addresses
    if lower in _BLOCKED_HOSTNAMES:
        raise ValueError(
            f"Host '{host}' is a blocked internal service name"
        )

    # Check if the host is a literal IP address
    if _is_private_ip(lower):
        raise ValueError(
            f"Host '{host}' resolves to a private/loopback address"
        )

    # Resolve hostname and check resolved IPs (only if not already an IP)
    try:
        ipaddress.ip_address(lower)
    except ValueError:
        # It's a hostname — resolve it with timeout
        results = _resolve_with_timeout(lower)
        for _family, _type, _proto, _canon, sockaddr in results:
            ip_str = sockaddr[0]
            if _is_private_ip(ip_str):
                raise ValueError(
                    f"Host '{host}' resolves to a private"
                    f"/loopback address ({ip_str})"
                )
        if not results:
            _logger.debug(
                "SSRF check: DNS resolution failed/timed out for '%s'", host,
            )

    return host


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

    @field_validator("host")
    @classmethod
    def validate_host_ssrf(cls, v: str) -> str:
        return _validate_host_ssrf(v)

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

    @field_validator("host")
    @classmethod
    def validate_host_ssrf(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return _validate_host_ssrf(v)

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
