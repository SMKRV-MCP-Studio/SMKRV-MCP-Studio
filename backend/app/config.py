"""Application configuration via environment variables."""

import logging
import os

from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


def _is_production() -> bool:
    """Check if running in production mode.

    Explicit STUDIO_ENV takes priority: "dev"/"development" → not production.
    Falls back to Docker detection (/.dockerenv) only when STUDIO_ENV is unset.
    """
    env = os.getenv("STUDIO_ENV", "").lower()
    if env in ("dev", "development"):
        return False
    if env in ("prod", "production"):
        return True
    return os.path.exists("/.dockerenv")


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    All variables are prefixed with STUDIO_ in the environment.
    """

    database_url: str = "sqlite+aiosqlite:///./data/studio.db"
    encryption_key: str = ""
    generated_dir: str = "./generated"
    fastmcp_host: str = "localhost"
    fastmcp_port: int = 8080
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:5173"]
    log_level: str = "INFO"

    # --- Auth / JWT ---
    jwt_secret: str = ""

    # --- Redis ---
    redis_url: str = "redis://redis:6379/0"

    # --- Agent MCP ---
    agent_service_token: str = ""  # shared secret for agent-mcp → backend auth

    # --- Prompt Guard ---
    prompt_guard_enabled: bool = True
    prompt_guard_ml_threshold: float = 0.5
    prompt_guard_model_dir: str = "./models/prompt-guard"
    prompt_guard_block_severity: str = "HIGH"  # minimum severity to block writes
    prompt_guard_ml_download: bool = True  # auto-download DeBERTa on startup
    prompt_guard_ml_update_interval_hours: int = 168  # check HF for updates (7 days)

    # --- SSL / Certbot ---
    external_https_port: int = 443  # external port for HTTP→HTTPS redirect
    nginx_config_dir: str = "/shared/nginx_config"
    certbot_webroot: str = "/var/www/certbot"
    letsencrypt_dir: str = "/etc/letsencrypt"
    ssl_staging: bool = False  # Use LE staging environment for testing

    # --- Nginx upstream hosts ---
    # Docker service names for 5-container setup; 127.0.0.1 for all-in-one
    nginx_backend_host: str = "backend"
    nginx_mcp_host: str = "mcp"
    nginx_agent_mcp_host: str = "agent-mcp"
    # nginx 1.25+ uses "http2 on;" directive; older uses "listen ... http2;"
    nginx_http2_modern: bool = True

    # --- Trusted proxies (set_real_ip_from) ---
    # Comma-separated CIDRs trusted for X-Forwarded-For extraction.
    # Default: Docker private networks + Cloudflare IPv4 ranges.
    # When behind Docker NAT or reverse proxy, $remote_addr is the proxy IP;
    # set_real_ip_from tells nginx to extract real client IP from X-Forwarded-For.
    trusted_proxies: str = (
        # Docker / private networks
        "127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,"
        # Cloudflare IPv4 (https://www.cloudflare.com/ips-v4)
        "173.245.48.0/20,103.21.244.0/22,103.22.200.0/22,103.31.4.0/22,"
        "141.101.64.0/18,108.162.192.0/18,190.93.240.0/20,188.114.96.0/20,"
        "197.234.240.0/22,198.41.128.0/17,162.158.0.0/15,104.16.0.0/13,"
        "104.24.0.0/14,172.64.0.0/13,131.0.72.0/22"
    )

    model_config = {"env_prefix": "STUDIO_"}


settings = Settings()


def validate_production_secrets() -> None:
    """Validate critical secrets are set in production.

    Call this during app startup. In production, empty secrets for JWT,
    encryption, and agent service token are fatal — the app will refuse
    to start to prevent insecure defaults.
    """
    if not _is_production():
        if not settings.jwt_secret:
            logger.warning(
                "STUDIO_JWT_SECRET is empty — sessions will not persist across restarts"
            )
        if not settings.encryption_key:
            logger.warning(
                "STUDIO_ENCRYPTION_KEY is empty"
                " — encrypted data will not persist across restarts"
            )
        return

    errors: list[str] = []
    if not settings.jwt_secret:
        errors.append("STUDIO_JWT_SECRET")
    if not settings.encryption_key:
        errors.append("STUDIO_ENCRYPTION_KEY")
    if not settings.agent_service_token:
        errors.append("STUDIO_AGENT_SERVICE_TOKEN")

    if errors:
        raise RuntimeError(
            f"Critical secrets missing in production: {', '.join(errors)}. "
            "Set these environment variables before starting the application."
        )
