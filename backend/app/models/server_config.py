"""ServerConfig model — FastMCP server settings (singleton)."""

import json
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ServerConfig(TimestampMixin, Base):
    """FastMCP server configuration. Always one row."""

    __tablename__ = "server_config"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    server_name: Mapped[str] = mapped_column(String(255), default="SMKRV Analytics MCP")
    transport: Mapped[str] = mapped_column(String(20), default="http")  # http, sse, stdio
    host: Mapped[str] = mapped_column(String(255), default="0.0.0.0")
    port: Mapped[int] = mapped_column(Integer, default=8080)
    auth_type: Mapped[str] = mapped_column(
        String(30), default="none"
    )  # none, bearer, oauth_credentials, oauth_introspection
    auth_bearer_token: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    auth_bearer_token_prefix: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )
    auth_bearer_last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    auth_bearer_last_ip: Mapped[str | None] = mapped_column(
        String(45), nullable=True
    )
    auth_bearer_last_country: Mapped[str | None] = mapped_column(
        String(2), nullable=True
    )
    cors_origins: Mapped[list] = mapped_column(
        JSON, default=lambda: ["http://localhost:3000", "http://localhost:5173"]
    )
    otel_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    log_level: Mapped[str] = mapped_column(String(20), default="INFO")

    # --- SSL / TLS ---
    ssl_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    ssl_ui_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ssl_mcp_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ssl_mcp_proxy_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    ssl_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ssl_challenge_type: Mapped[str] = mapped_column(String(20), default="http-01")
    ssl_dns_provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ssl_dns_credentials: Mapped[str | None] = mapped_column(
        String(2048), nullable=True
    )  # Fernet-encrypted
    ssl_auto_renew: Mapped[bool] = mapped_column(Boolean, default=True)
    ssl_cert_status: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # --- OAuth2 Client Credentials (self-contained) ---
    oauth_clients_json: Mapped[str | None] = mapped_column(
        String(4096), nullable=True
    )  # Fernet-encrypted JSON: [{"client_id": "...", "client_secret": "..."}]
    oauth_token_ttl_seconds: Mapped[int] = mapped_column(Integer, default=3600)

    # --- OAuth2 Token Introspection (external) ---
    oauth_introspection_url: Mapped[str | None] = mapped_column(
        String(1024), nullable=True
    )
    oauth_introspection_client_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    oauth_introspection_client_secret: Mapped[str | None] = mapped_column(
        String(1024), nullable=True
    )  # Fernet-encrypted
    oauth_introspection_cache_seconds: Mapped[int] = mapped_column(
        Integer, default=60
    )

    # --- Docker port mappings (desired values; active values come from env) ---
    studio_port: Mapped[int] = mapped_column(Integer, default=3000)
    studio_ssl_port: Mapped[int] = mapped_column(Integer, default=443)

    # --- Agent MCP ---
    agent_mcp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    agent_mcp_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    agent_mcp_rate_limit: Mapped[int] = mapped_column(Integer, default=120)
    agent_mcp_fields_allowlist: Mapped[list] = mapped_column(
        JSON, default=list
    )  # empty = no restriction; non-empty = only these fields in list_tools fields param

    # --- Global Variables (Jinja2 transform) ---
    global_variables: Mapped[dict] = mapped_column(JSON, default=dict)

    # --- Prompt Injection Guard ---
    prompt_guard_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    prompt_guard_l0_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    prompt_guard_l1_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    prompt_guard_l0_entity_types: Mapped[list] = mapped_column(
        JSON, default=lambda: ["tool", "prompt", "resource"]
    )
    prompt_guard_l1_entity_types: Mapped[list] = mapped_column(
        JSON, default=lambda: ["tool", "prompt", "resource"]
    )
    prompt_guard_block_severity: Mapped[str] = mapped_column(
        String(20), default="HIGH"
    )
    prompt_guard_ml_threshold: Mapped[float] = mapped_column(Float, default=0.5)
    prompt_guard_custom_patterns: Mapped[list] = mapped_column(
        JSON, default=list
    )
    prompt_guard_disabled_patterns: Mapped[list] = mapped_column(
        JSON, default=list
    )

    @property
    def auth_bearer_token_set(self) -> bool:
        """True when a bearer token has been configured."""
        return bool(self.auth_bearer_token)

    @property
    def ssl_dns_credentials_set(self) -> bool:
        """True when DNS credentials have been stored (never expose actual value)."""
        return bool(self.ssl_dns_credentials)

    @property
    def oauth_clients_count(self) -> int:
        """Number of registered OAuth2 clients (never expose secrets)."""
        if not self.oauth_clients_json:
            return 0
        try:
            from app.services.crypto import decrypt

            clients = json.loads(decrypt(self.oauth_clients_json))
            return len(clients)
        except Exception:
            return 0

    @property
    def oauth_introspection_secret_set(self) -> bool:
        """True when introspection client secret has been stored."""
        return bool(self.oauth_introspection_client_secret)
