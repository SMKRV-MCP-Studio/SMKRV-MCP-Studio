"""Authentication service — bcrypt hashing, JWT tokens, cookie management."""

import asyncio
import logging
import secrets
import time as _time
import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from fastapi import Response

from app.config import settings
from app.services.redis_client import get_redis

logger = logging.getLogger(__name__)

# --- JWT secret auto-generation ---

_jwt_secret: str | None = None


def _get_jwt_secret() -> str:
    """Get JWT secret, auto-generating if not configured."""
    global _jwt_secret
    if _jwt_secret is not None:
        return _jwt_secret

    secret = settings.jwt_secret
    if not secret:
        secret = secrets.token_urlsafe(64)
        logger.warning(
            "STUDIO_JWT_SECRET is empty — a secret was auto-generated. "
            "Set STUDIO_JWT_SECRET in .env to persist sessions across restarts.",
        )
        settings.jwt_secret = secret

    _jwt_secret = secret
    return _jwt_secret


# --- Password hashing (bcrypt, cost=12) ---

_BCRYPT_ROUNDS = 12


def _hash_password_sync(plain: str) -> str:
    """Hash a plaintext password with bcrypt (sync, for use in threads)."""
    salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    return bcrypt.hashpw(plain.encode(), salt).decode()


def _verify_password_sync(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against a bcrypt hash (sync, for use in threads)."""
    return bcrypt.checkpw(plain.encode(), hashed.encode())


async def hash_password(plain: str) -> str:
    """Hash a plaintext password with bcrypt without blocking the event loop."""
    return await asyncio.to_thread(_hash_password_sync, plain)


async def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against a bcrypt hash without blocking the event loop."""
    return await asyncio.to_thread(_verify_password_sync, plain, hashed)


# --- JWT tokens ---

_JWT_ALGORITHM = "HS256"
_JWT_EXPIRY_HOURS = 24


def create_access_token(username: str) -> str:
    """Create a signed JWT with username as subject, jti, and 24h expiry."""
    now = datetime.now(UTC)
    payload = {
        "sub": username,
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + timedelta(hours=_JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, _get_jwt_secret(), algorithm=_JWT_ALGORITHM)


def decode_access_token(token: str) -> str | None:
    """Decode and validate a JWT. Returns username or None if invalid/expired.

    Rejects tokens with ``purpose=2fa_pending`` so that a pending 2FA token
    cannot be used as a full session.
    """
    try:
        payload = jwt.decode(token, _get_jwt_secret(), algorithms=[_JWT_ALGORITHM])
        if payload.get("purpose") == "2fa_pending":
            return None
        return payload.get("sub")
    except jwt.ExpiredSignatureError:
        logger.debug("JWT expired")
        return None
    except jwt.InvalidTokenError:
        logger.debug("JWT invalid")
        return None


# --- 2FA pending tokens ---

_2FA_PENDING_EXPIRY_MINUTES = 5


def create_2fa_pending_token(username: str) -> str:
    """Create a short-lived JWT for the 2FA verification step.

    Proves that the password was verified but 2FA is not yet complete.
    Cannot be used for normal API access (guarded by ``decode_access_token``).
    """
    now = datetime.now(UTC)
    payload = {
        "sub": username,
        "purpose": "2fa_pending",
        "iat": now,
        "exp": now + timedelta(minutes=_2FA_PENDING_EXPIRY_MINUTES),
    }
    return jwt.encode(payload, _get_jwt_secret(), algorithm=_JWT_ALGORITHM)


def decode_2fa_pending_token(token: str) -> str | None:
    """Decode a 2FA pending token. Returns username or None.

    Only accepts tokens with ``purpose=2fa_pending``.
    """
    try:
        payload = jwt.decode(token, _get_jwt_secret(), algorithms=[_JWT_ALGORITHM])
        if payload.get("purpose") != "2fa_pending":
            return None
        return payload.get("sub")
    except jwt.ExpiredSignatureError:
        logger.debug("2FA pending token expired")
        return None
    except jwt.InvalidTokenError:
        logger.debug("2FA pending token invalid")
        return None


# --- Cookie helpers ---

_COOKIE_NAME = "smkrv_session"
_COOKIE_MAX_AGE = 86400  # 24 hours


def _is_ssl_mode() -> bool:
    """Check if SSL is enabled (behind reverse proxy or direct)."""
    return settings.ssl_enabled or bool(settings.ssl_cert_path)


def set_auth_cookie(response: Response, token: str) -> None:
    """Set httpOnly session cookie on the response.

    The Secure flag is set when SSL is enabled (direct or reverse proxy).
    """
    response.set_cookie(
        key=_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=_is_ssl_mode(),
        samesite="strict",
        max_age=_COOKIE_MAX_AGE,
        path="/api",
    )


def clear_auth_cookie(response: Response) -> None:
    """Clear the session cookie."""
    response.delete_cookie(
        key=_COOKIE_NAME,
        httponly=True,
        samesite="strict",
        path="/api",
    )


def get_cookie_name() -> str:
    """Return the session cookie name (for dependency injection)."""
    return _COOKIE_NAME


# --- Rate limiting (Redis-backed, survives restarts) ---

_MAX_ATTEMPTS = 5
_LOCKOUT_SECONDS = 60

# Separate rate limiter for 2FA verification (stricter: 5 attempts / 5 min)
_2FA_MAX_ATTEMPTS = 5
_2FA_LOCKOUT_SECONDS = 300

_REDIS_KEY_LOGIN = "ratelimit:login:{ip}"
_REDIS_KEY_2FA = "ratelimit:2fa:{ip}"

# --- In-memory rate limit fallback when Redis is unavailable ---

_fallback_rate_limits: dict[str, list[float]] = {}
_FALLBACK_WINDOW = 300  # 5 minutes
_FALLBACK_MAX_KEYS = 10000


def _fallback_check(key: str, max_attempts: int) -> bool:
    """Check in-memory rate limit. Returns True if allowed (under limit)."""
    now = _time.monotonic()
    attempts = _fallback_rate_limits.get(key, [])
    # Remove expired entries
    attempts = [t for t in attempts if now - t < _FALLBACK_WINDOW]
    _fallback_rate_limits[key] = attempts
    return len(attempts) < max_attempts


def _fallback_record(key: str) -> None:
    """Record a failed attempt in the in-memory fallback."""
    now = _time.monotonic()
    attempts = _fallback_rate_limits.get(key, [])
    attempts = [t for t in attempts if now - t < _FALLBACK_WINDOW]
    attempts.append(now)
    _fallback_rate_limits[key] = attempts
    # Prevent unbounded growth — evict oldest 10% of entries
    if len(_fallback_rate_limits) > _FALLBACK_MAX_KEYS:
        sorted_keys = sorted(
            _fallback_rate_limits,
            key=lambda k: _fallback_rate_limits[k][-1] if _fallback_rate_limits[k] else 0,
        )
        for k in sorted_keys[:_FALLBACK_MAX_KEYS // 10]:
            del _fallback_rate_limits[k]


def _fallback_clear(*keys: str) -> None:
    """Clear in-memory rate limit entries."""
    for key in keys:
        _fallback_rate_limits.pop(key, None)


async def check_rate_limit(client_ip: str) -> bool:
    """Check if a login attempt is allowed. Returns True if allowed."""
    try:
        r = get_redis()
        count = await r.get(_REDIS_KEY_LOGIN.format(ip=client_ip))
        if count is None:
            return True
        return int(count) < _MAX_ATTEMPTS
    except Exception:
        logger.warning("Redis unavailable for rate limiting, using in-memory fallback")
        return _fallback_check(f"login:{client_ip}", _MAX_ATTEMPTS)


async def check_2fa_rate_limit(client_ip: str) -> bool:
    """Check if a 2FA verification attempt is allowed. Returns True if allowed.

    Separate from login rate limit to prevent shared-counter abuse:
    an attacker cannot use login attempts to exhaust 2FA rate limit.
    """
    try:
        r = get_redis()
        count = await r.get(_REDIS_KEY_2FA.format(ip=client_ip))
        if count is None:
            return True
        return int(count) < _2FA_MAX_ATTEMPTS
    except Exception:
        logger.warning("Redis unavailable for 2FA rate limiting, using in-memory fallback")
        return _fallback_check(f"2fa:{client_ip}", _2FA_MAX_ATTEMPTS)


async def record_failed_attempt(client_ip: str) -> None:
    """Record a failed login attempt in Redis (or in-memory fallback)."""
    try:
        r = get_redis()
        key = _REDIS_KEY_LOGIN.format(ip=client_ip)
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.expire(key, _LOCKOUT_SECONDS)
        await pipe.execute()
    except Exception:
        logger.warning("Redis unavailable, recording failed attempt in memory")
        _fallback_record(f"login:{client_ip}")


async def record_failed_2fa_attempt(client_ip: str) -> None:
    """Record a failed 2FA verification attempt (separate counter)."""
    try:
        r = get_redis()
        key = _REDIS_KEY_2FA.format(ip=client_ip)
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.expire(key, _2FA_LOCKOUT_SECONDS)
        await pipe.execute()
    except Exception:
        logger.warning("Redis unavailable, recording failed 2FA attempt in memory")
        _fallback_record(f"2fa:{client_ip}")


async def clear_attempts(client_ip: str) -> None:
    """Clear login and 2FA attempts on successful login."""
    _fallback_clear(f"login:{client_ip}", f"2fa:{client_ip}")
    try:
        r = get_redis()
        await r.delete(
            _REDIS_KEY_LOGIN.format(ip=client_ip),
            _REDIS_KEY_2FA.format(ip=client_ip),
        )
    except Exception:
        logger.warning("Redis unavailable, rate limit entries not cleared")


async def cleanup_expired_attempts() -> int:
    """No-op: Redis TTL handles expiry automatically.

    Kept for backward compatibility with the cleanup loop in main.py.
    """
    return 0
