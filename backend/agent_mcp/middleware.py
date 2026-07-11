"""ASGI middleware for agent MCP authentication.

Validates bearer tokens from the Authorization header on all MCP endpoints.
Skips auth for /health and /oauth/token (which use their own credentials).
Stores auth context in a ContextVar for tool wrappers to access.
"""

import contextvars
import hashlib
import logging
import os
import time

from redis.exceptions import RedisError
from starlette.requests import Request
from starlette.responses import JSONResponse

from agent_mcp import auth, config

logger = logging.getLogger(__name__)

# Auth context available to tool wrappers via get_auth_context()
_auth_context: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "agent_auth_context", default=None
)

# Per-IP rate limit for OAuth token endpoint (brute-force protection)
_oauth_rate: dict[str, list[float]] = {}
_OAUTH_MAX_PER_MINUTE = 10

# Per-IP pre-auth rate limit for bearer-required endpoints. Applied BEFORE token
# validation so an unauthenticated client cannot force unbounded token-validation
# work (Redis lookup + bcrypt) by replaying random bearer tokens. Set generously
# above legitimate per-token throughput; configurable for high-fan-out setups.
_preauth_rate: dict[str, list[float]] = {}
_PREAUTH_MAX_PER_MINUTE = int(os.getenv("STUDIO_AGENT_PREAUTH_RATE_LIMIT", "120"))


def get_auth_context() -> dict | None:
    """Get the current request's auth context (set by AuthMiddleware)."""
    return _auth_context.get()


def _parse_trusted_networks() -> list:
    import ipaddress

    nets = []
    for entry in (config.TRUSTED_PROXIES or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            nets.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            logger.warning("Ignoring invalid TRUSTED_PROXIES entry: %s", entry)
    return nets


_TRUSTED_NETWORKS = _parse_trusted_networks()


def _peer_is_trusted(peer: str) -> bool:
    if not peer:
        return False
    import ipaddress

    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(addr in net for net in _TRUSTED_NETWORKS)


def _get_client_ip(request: Request) -> str:
    """Extract the real client IP, safe to key rate limiting on.

    Client-settable headers are honored only when the immediate peer is a trusted
    proxy; otherwise the socket peer is used. Behind the bundled nginx the real IP
    lands in X-Real-IP (== $remote_addr from the real_ip module), unspoofable
    through the proxy.
    """
    peer = request.client.host if request.client else ""
    if _peer_is_trusted(peer):
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()
        cf_ip = request.headers.get("cf-connecting-ip")
        if cf_ip:
            return cf_ip.strip()
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return peer


def _get_client_country(request: Request) -> str:
    """Extract client country from CF-IPCountry header (trusted proxy only)."""
    peer = request.client.host if request.client else ""
    if _peer_is_trusted(peer):
        cf_country = request.headers.get("cf-ipcountry")
        if cf_country and cf_country not in ("XX", "T1"):
            code = cf_country.upper()[:2]
            if len(code) == 2 and code.isalpha():
                return code
    return ""


_OAUTH_RATE_MAX_IPS = 10_000  # max tracked IPs to prevent unbounded growth


def _sliding_window_allow(
    store: dict[str, list[float]], client_ip: str, max_per_minute: int
) -> bool:
    """Per-IP sliding-window limiter over an in-process store. True if allowed."""
    now = time.monotonic()

    # Evict stale entries periodically to bound memory
    if len(store) > _OAUTH_RATE_MAX_IPS:
        stale = [ip for ip, ts in store.items() if not ts or now - ts[-1] > 120]
        for ip in stale:
            del store[ip]
        # Forced eviction of oldest entries if still over limit
        if len(store) > _OAUTH_RATE_MAX_IPS:
            by_age = sorted(store, key=lambda ip: store[ip][-1])
            for ip in by_age[:len(store) - _OAUTH_RATE_MAX_IPS]:
                del store[ip]

    window = store.get(client_ip)
    if window is None:
        store[client_ip] = [now]
        return True

    store[client_ip] = [t for t in window if now - t < 60]
    if len(store[client_ip]) >= max_per_minute:
        return False

    store[client_ip].append(now)
    return True


def _check_oauth_rate_limit(client_ip: str) -> bool:
    """Per-IP rate limit for OAuth token endpoint. Returns True if allowed."""
    return _sliding_window_allow(_oauth_rate, client_ip, _OAUTH_MAX_PER_MINUTE)


def _check_preauth_rate_limit(client_ip: str) -> bool:
    """Per-IP rate limit applied before bearer token validation."""
    return _sliding_window_allow(_preauth_rate, client_ip, _PREAUTH_MAX_PER_MINUTE)


# Paths that skip bearer token auth entirely
_PUBLIC_PATHS = frozenset({"/", "/health"})

# Paths that skip bearer auth but have their own protection
_CREDENTIAL_PATHS = frozenset({"/oauth/token"})

# Paths that require bearer auth (introspect per RFC 7662)
_AUTH_REQUIRED_PATHS_PREFIX = "/mcp"


class AuthMiddleware:
    """ASGI middleware that validates bearer tokens for MCP endpoints.

    - /health: no auth
    - /oauth/token: no bearer auth, but rate-limited per IP
    - /oauth/introspect: requires valid bearer token (RFC 7662)
    - /mcp/*: requires valid bearer token
    - All other paths: requires valid bearer token
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            # WebSocket, lifespan, etc. — pass through
            return await self.app(scope, receive, send)

        request = Request(scope)
        path = request.url.path
        client_ip = _get_client_ip(request)
        client_country = _get_client_country(request)

        # Public endpoints — no auth
        if path in _PUBLIC_PATHS:
            return await self.app(scope, receive, send)

        # OAuth token endpoint — rate limit but no bearer auth
        if path in _CREDENTIAL_PATHS:
            if not _check_oauth_rate_limit(client_ip):
                response = JSONResponse(
                    {"error": "rate_limit_exceeded", "error_description": "Too many requests"},
                    status_code=429,
                )
                return await response(scope, receive, send)
            # Pass client_ip and country through scope state for the handler
            scope.setdefault("state", {})
            scope["state"]["client_ip"] = client_ip
            scope["state"]["client_country"] = client_country
            return await self.app(scope, receive, send)

        # All other endpoints require bearer token
        auth_header = request.headers.get("authorization", "")
        if not auth_header.lower().startswith("bearer "):
            response = JSONResponse(
                {"error": "unauthorized", "error_description": "Bearer token required"},
                status_code=401,
            )
            return await response(scope, receive, send)

        bearer_token = auth_header[7:]

        # Pre-auth per-IP limit BEFORE validation, so an unauthenticated client
        # cannot force unbounded token-validation work by replaying random tokens.
        if not _check_preauth_rate_limit(client_ip):
            response = JSONResponse(
                {"error": "rate_limit_exceeded", "error_description": "Too many requests"},
                status_code=429,
            )
            return await response(scope, receive, send)

        # Validate token (IP + country for usage tracking)
        try:
            token_info = await auth.validate_token(bearer_token, client_ip, client_country)
        except (RedisError, OSError) as exc:
            logger.error("Token validation failed: %s", type(exc).__name__)
            response = JSONResponse(
                {"error": "service_unavailable",
                 "error_description": "Auth service temporarily unavailable"},
                status_code=503,
                headers={"Retry-After": "5"},
            )
            return await response(scope, receive, send)

        if token_info is None:
            response = JSONResponse(
                {"error": "invalid_token", "error_description": "Token is invalid or expired"},
                status_code=401,
            )
            return await response(scope, receive, send)

        # Check rate limit
        if not await auth.check_rate_limit(token_info["token_prefix"]):
            response = JSONResponse(
                {"error": "rate_limit_exceeded", "error_description": "Rate limit exceeded"},
                status_code=429,
            )
            return await response(scope, receive, send)

        # Store auth context for tool wrappers
        token_info["client_ip"] = client_ip
        token_info["client_country"] = client_country
        ctx_token = _auth_context.set(token_info)
        try:
            return await self.app(scope, receive, send)
        finally:
            _auth_context.reset(ctx_token)
