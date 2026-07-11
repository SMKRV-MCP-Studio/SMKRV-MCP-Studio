"""Pydantic schemas for ServerConfig entity."""

import json
import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

# RFC 1123 compliant domain name (no protocol, no trailing dot)
_DOMAIN_RE = re.compile(
    r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.[A-Za-z0-9-]{1,63})*\.[A-Za-z]{2,}$"
)


class ServerConfigUpdate(BaseModel):
    """Schema for updating server configuration."""

    server_name: str | None = Field(default=None, max_length=255)
    transport: str | None = Field(default=None, pattern=r"^(http|sse|stdio)$")
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    auth_type: str | None = Field(
        default=None,
        pattern=r"^(none|bearer|oauth_credentials|oauth_introspection)$",
    )
    auth_bearer_token: str | None = Field(default=None, max_length=2048)
    cors_origins: list[str] | None = Field(default=None, max_length=20)
    otel_enabled: bool | None = None
    log_level: str | None = Field(
        default=None, pattern=r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$"
    )

    # --- SSL / TLS ---
    ssl_enabled: bool | None = None
    ssl_ui_domain: str | None = Field(default=None, max_length=255)
    ssl_mcp_domain: str | None = Field(default=None, max_length=255)
    ssl_mcp_proxy_enabled: bool | None = None
    ssl_email: str | None = Field(default=None, max_length=255)
    ssl_challenge_type: str | None = Field(
        default=None, pattern=r"^(http-01|dns-01)$"
    )
    ssl_dns_provider: str | None = Field(
        default=None, pattern=r"^(cloudflare|route53)$"
    )
    ssl_dns_credentials: str | None = None
    ssl_auto_renew: bool | None = None

    # --- OAuth2 Client Credentials ---
    oauth_clients_json: str | None = Field(default=None, max_length=4096)
    oauth_token_ttl_seconds: int | None = Field(default=None, ge=60, le=604800)

    # --- OAuth2 Token Introspection ---
    oauth_introspection_url: str | None = Field(default=None, max_length=1024)
    oauth_introspection_client_id: str | None = Field(default=None, max_length=255)
    oauth_introspection_client_secret: str | None = Field(
        default=None, max_length=2048
    )
    oauth_introspection_cache_seconds: int | None = Field(
        default=None, ge=0, le=3600
    )

    # --- Docker port mappings ---
    studio_port: int | None = Field(default=None, ge=1, le=65535)
    studio_ssl_port: int | None = Field(default=None, ge=1, le=65535)

    # --- Agent MCP ---
    agent_mcp_enabled: bool | None = None
    agent_mcp_domain: str | None = Field(default=None, max_length=255)
    agent_mcp_rate_limit: int | None = Field(default=None, ge=1, le=1000)
    agent_mcp_fields_allowlist: list[str] | None = None

    # --- Global Variables (Jinja2 transform) ---
    global_variables: dict | None = None

    # --- Prompt Injection Guard ---
    prompt_guard_enabled: bool | None = None
    prompt_guard_l0_enabled: bool | None = None
    prompt_guard_l1_enabled: bool | None = None
    prompt_guard_l0_entity_types: list[str] | None = None
    prompt_guard_l1_entity_types: list[str] | None = None
    prompt_guard_block_severity: str | None = Field(
        default=None, pattern=r"^(LOW|MEDIUM|HIGH|CRITICAL)$"
    )
    prompt_guard_ml_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    prompt_guard_custom_patterns: list[dict] | None = None
    prompt_guard_disabled_patterns: list[str] | None = None

    @field_validator("agent_mcp_fields_allowlist")
    @classmethod
    def validate_fields_allowlist(
        cls, v: list[str] | None,
    ) -> list[str] | None:
        if v is None:
            return v
        if len(v) > 50:
            raise ValueError("Maximum 50 fields in allowlist")
        _field_re = re.compile(r"^[a-zA-Z_]\w{0,63}$")
        for name in v:
            if not isinstance(name, str) or not _field_re.match(name):
                raise ValueError(f"Invalid field name: {name}")
        return v

    @field_validator("prompt_guard_l0_entity_types", "prompt_guard_l1_entity_types")
    @classmethod
    def validate_entity_types(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        valid = {"tool", "prompt", "resource"}
        for item in v:
            if item not in valid:
                raise ValueError(f"Invalid entity type: {item}. Must be one of: {valid}")
        return v

    @field_validator("prompt_guard_custom_patterns")
    @classmethod
    def validate_custom_patterns(cls, v: list[dict] | None) -> list[dict] | None:
        if v is None:
            return v
        if len(v) > 200:
            raise ValueError("Maximum 200 custom patterns")
        for i, p in enumerate(v):
            if "pattern" not in p:
                raise ValueError(f"Pattern #{i}: 'pattern' field is required")
            try:
                re.compile(p["pattern"])
            except re.error as exc:
                raise ValueError(f"Pattern #{i}: invalid regex — {exc}")
            if "severity" in p and p["severity"] not in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
                raise ValueError(f"Pattern #{i}: invalid severity")
        return v

    @field_validator("global_variables")
    @classmethod
    def validate_global_variables(cls, v: dict | None) -> dict | None:
        if v is None:
            return v
        if len(v) > 100:
            raise ValueError("Maximum 100 global variables")
        _var_name_re = re.compile(r"^[a-zA-Z_]\w{0,63}$")
        for key in v:
            if not isinstance(key, str) or not _var_name_re.match(key):
                raise ValueError(f"Invalid variable name: {key}")
            if not isinstance(v[key], (str, int, float, bool)):
                raise ValueError(
                    f"Variable '{key}' must be a scalar (str/int/float/bool)"
                )
        return v

    @field_validator("oauth_introspection_url")
    @classmethod
    def validate_introspection_url(cls, v: str | None) -> str | None:
        if v is not None and v.strip():
            if not v.startswith(("https://", "http://")):
                raise ValueError(
                    "Introspection URL must start with https:// or http://"
                )
        return v

    @field_validator("oauth_clients_json")
    @classmethod
    def validate_oauth_clients_json(cls, v: str | None) -> str | None:
        if v is not None and v.strip():
            try:
                clients = json.loads(v)
            except json.JSONDecodeError:
                raise ValueError("OAuth clients must be valid JSON")
            if not isinstance(clients, list):
                raise ValueError("OAuth clients must be a JSON array")
            if len(clients) > 10:
                raise ValueError("Maximum 10 OAuth clients allowed")
            for c in clients:
                if (
                    not isinstance(c, dict)
                    or "client_id" not in c
                    or "client_secret" not in c
                ):
                    raise ValueError(
                        "Each client must have 'client_id' and 'client_secret'"
                    )
        return v

    @field_validator("ssl_ui_domain", "ssl_mcp_domain", "agent_mcp_domain")
    @classmethod
    def validate_domain(cls, v: str | None) -> str | None:
        if v is not None and v.strip() and not _DOMAIN_RE.match(v):
            raise ValueError(f"Invalid domain name: {v}")
        return v


class ServerConfigResponse(BaseModel):
    """Schema for returning server configuration."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    server_name: str
    transport: str
    host: str
    port: int
    auth_type: str
    cors_origins: list[str]
    otel_enabled: bool
    log_level: str
    created_at: datetime
    updated_at: datetime

    # --- Bearer Auth ---
    auth_bearer_token_set: bool = False
    auth_bearer_token_prefix: str | None = None
    auth_bearer_last_used_at: datetime | None = None
    auth_bearer_last_ip: str | None = None
    auth_bearer_last_country: str | None = None

    # --- SSL / TLS ---
    ssl_enabled: bool = False
    ssl_ui_domain: str | None = None
    ssl_mcp_domain: str | None = None
    ssl_mcp_proxy_enabled: bool = False
    ssl_email: str | None = None
    ssl_challenge_type: str = "http-01"
    ssl_dns_provider: str | None = None
    ssl_dns_credentials_set: bool = False
    ssl_auto_renew: bool = True
    ssl_cert_status: str | None = None

    # --- OAuth2 ---
    oauth_clients_count: int = 0
    oauth_token_ttl_seconds: int = 3600
    oauth_introspection_url: str | None = None
    oauth_introspection_client_id: str | None = None
    oauth_introspection_secret_set: bool = False
    oauth_introspection_cache_seconds: int = 60

    # --- Docker port mappings ---
    studio_port: int = 3000
    studio_ssl_port: int = 443
    studio_port_active: int = 3000  # current running value from env
    studio_ssl_port_active: int = 443  # current running value from env

    # --- Agent MCP ---
    agent_mcp_enabled: bool = False
    agent_mcp_domain: str | None = None
    agent_mcp_rate_limit: int = 120
    agent_mcp_fields_allowlist: list[str] = Field(default_factory=list)

    # --- Global Variables (Jinja2 transform) ---
    global_variables: dict = Field(default_factory=dict)

    # --- Prompt Injection Guard ---
    prompt_guard_enabled: bool = True
    prompt_guard_l0_enabled: bool = True
    prompt_guard_l1_enabled: bool = True
    prompt_guard_l0_entity_types: list[str] = Field(
        default_factory=lambda: ["tool", "prompt", "resource"]
    )
    prompt_guard_l1_entity_types: list[str] = Field(
        default_factory=lambda: ["tool", "prompt", "resource"]
    )
    prompt_guard_block_severity: str = "HIGH"
    prompt_guard_ml_threshold: float = 0.5
    prompt_guard_custom_patterns: list[dict] = Field(default_factory=list)
    prompt_guard_disabled_patterns: list[str] = Field(default_factory=list)


class RotateBearerResponse(BaseModel):
    """One-time response after rotating the bearer token."""

    token: str
    token_prefix: str
    message: str = "Bearer token rotated. Copy it now — it won't be shown again."


class DeployStatus(BaseModel):
    """Schema for deploy/MCP server status."""

    status: str  # running, stopped, error
    message: str = ""
    pid: int | None = None


class DeployResponse(BaseModel):
    """Schema for deploy operation result."""

    deployed: bool
    message: str
    files_generated: int = 0
    errors: list[str] = []
    warnings: list[str] = []


class PreviewRequest(BaseModel):
    """Schema for SQL preview execution."""

    connection_id: str
    sql_query: str = Field(..., min_length=1, max_length=100_000)
    params: dict = Field(default_factory=dict)
    limit: int = Field(default=10, ge=1, le=1000)
    transform_template: str | None = Field(default=None, max_length=100_000)


class PreviewResponse(BaseModel):
    """Schema for SQL preview result."""

    columns: list[str]
    rows: list[dict]
    row_count: int
    execution_time_ms: float
    transformed_result: list[dict] | dict | str | None = None
    transform_error: str | None = None


class GeneratedFile(BaseModel):
    """Schema for a generated file."""

    path: str
    content: str
    size_bytes: int


# ---------------------------------------------------------------------------
# SSL / TLS schemas
# ---------------------------------------------------------------------------
class SSLIssueRequest(BaseModel):
    """Optional overrides when issuing a certificate."""

    force_renew: bool = False


class SSLIssueResponse(BaseModel):
    """Result of a certbot issue/renew operation."""

    success: bool
    message: str
    domains: list[str] = []
    cert_status: str | None = None


class SSLStatusResponse(BaseModel):
    """Current SSL certificate status."""

    status: str  # none, issued, expiring_soon, expired
    domains: list[str] = []
    issuer: str | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    days_remaining: int | None = None


# ---------------------------------------------------------------------------
# Prompt Injection Guard schemas
# ---------------------------------------------------------------------------

class PromptGuardPatternResponse(BaseModel):
    """A single injection pattern (built-in or custom)."""

    id: str
    category: str
    severity: str
    lang: str = "*"
    pattern: str
    description: str = ""
    source: str = "builtin"  # builtin | custom
    enabled: bool = True


class PatternsListResponse(BaseModel):
    """Full list of guard patterns with metadata."""

    builtin: list[PromptGuardPatternResponse] = []
    custom: list[PromptGuardPatternResponse] = []
    total: int = 0
    l1_available: bool = False


class ValidatePatternRequest(BaseModel):
    """Request body for pattern validation."""

    pattern: str
    test_text: str | None = None


class ValidatePatternResponse(BaseModel):
    """Result of regex pattern validation."""

    valid: bool
    error: str | None = None
    matches: list[str] = []


class ScanDetection(BaseModel):
    """A single detection from entity scanning."""

    field: str
    category: str
    severity: str
    pattern_id: str
    matched_text: str
    layer: str
    confidence: float = 1.0


class EntityScanResult(BaseModel):
    """Scan result for a single entity."""

    entity_type: str
    entity_id: str
    entity_name: str
    is_clean: bool
    detections: list[ScanDetection] = []
    max_severity: str | None = None


class BulkScanResponse(BaseModel):
    """Result of scanning all entities."""

    total_scanned: int
    total_flagged: int
    results: list[EntityScanResult] = []
    scan_time_ms: float = 0.0
    l1_available: bool = False
