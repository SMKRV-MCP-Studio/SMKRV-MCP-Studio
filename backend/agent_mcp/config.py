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
