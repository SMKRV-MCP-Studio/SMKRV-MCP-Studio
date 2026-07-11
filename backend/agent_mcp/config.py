"""Agent MCP server configuration from environment variables."""

import os

BACKEND_URL: str = os.getenv("STUDIO_BACKEND_URL", "http://backend:8000")
AGENT_SERVICE_TOKEN: str = os.getenv("STUDIO_AGENT_SERVICE_TOKEN", "")
REDIS_URL: str = os.getenv("STUDIO_REDIS_URL", "redis://redis:6379/0")
SERVER_PORT: int = int(os.getenv("STUDIO_AGENT_MCP_PORT", "8090"))
SERVER_HOST: str = os.getenv("STUDIO_AGENT_MCP_HOST", "0.0.0.0")
LOG_LEVEL: str = os.getenv("STUDIO_LOG_LEVEL", "INFO")

# Rate limit default (requests per minute per token)
DEFAULT_RATE_LIMIT: int = int(os.getenv("STUDIO_AGENT_RATE_LIMIT", "120"))

# Trusted reverse-proxy CIDRs. Forwarded client-IP headers are honored only when
# the immediate peer is in this set (must match the backend's trusted_proxies).
TRUSTED_PROXIES: str = os.getenv(
    "STUDIO_TRUSTED_PROXIES",
    "127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,"
    "173.245.48.0/20,103.21.244.0/22,103.22.200.0/22,103.31.4.0/22,"
    "141.101.64.0/18,108.162.192.0/18,190.93.240.0/20,188.114.96.0/20,"
    "197.234.240.0/22,198.41.128.0/17,162.158.0.0/15,104.16.0.0/13,"
    "104.24.0.0/14,172.64.0.0/13,131.0.72.0/22",
)

# OWASP output scanning — scan for injection patterns in tool output
# Backwards-compatible: reads STUDIO_AGENT_OUTPUT_SCANNING first,
# falls back to old STUDIO_AGENT_OUTPUT_WRAPPING
OUTPUT_SCANNING: bool = os.getenv(
    "STUDIO_AGENT_OUTPUT_SCANNING",
    os.getenv("STUDIO_AGENT_OUTPUT_WRAPPING", "true"),
).lower() in ("true", "1", "yes")


def validate_startup() -> None:
    """Validate critical config at startup. Raises RuntimeError on failure."""
    errors: list[str] = []

    if not AGENT_SERVICE_TOKEN:
        errors.append(
            "STUDIO_AGENT_SERVICE_TOKEN is empty — agent-mcp cannot authenticate "
            "with the backend. Set this env var before starting."
        )

    if not REDIS_URL:
        errors.append("STUDIO_REDIS_URL is empty — token validation requires Redis.")

    if errors:
        raise RuntimeError("Agent MCP startup failed:\n  " + "\n  ".join(errors))
