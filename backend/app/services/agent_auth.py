"""Agent authentication service — token generation, OAuth2, Redis storage, activity logging."""

import json
import logging
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_session import AgentSession
from app.models.agent_token import AgentToken
from app.models.oauth_client import OAuthClient
from app.services.auth import (
    _hash_password_sync as _hash_secret,
)
from app.services.auth import (
    _verify_password_sync as _verify_secret,
)
from app.services.redis_client import get_redis

logger = logging.getLogger(__name__)

_TOKEN_PREFIX = "smkr_"
_CLIENT_ID_PREFIX = "smkr_cl_"
_CLIENT_SECRET_PREFIX = "smkr_cs_"
_REDIS_ACTIVITY_KEY = "agent:activity"
_REDIS_ACTIVITY_MAX = 500


def _generate_random(prefix: str, length: int = 40) -> str:
    """Generate a prefixed random token."""
    return prefix + secrets.token_urlsafe(length)


# ---------------------------------------------------------------------------
# Agent Tokens (temporary, max 7 days)
# ---------------------------------------------------------------------------


async def generate_agent_token(
    db: AsyncSession,
    name: str,
    duration_minutes: int,
) -> tuple[str, AgentToken]:
    """Create a temporary agent token.

    Returns (plaintext_token, AgentToken model).
    The plaintext is shown once and never stored.
    """
    if duration_minutes < 15 or duration_minutes > 10080:
        raise ValueError("Duration must be between 15 minutes and 7 days")

    plaintext = _generate_random(_TOKEN_PREFIX)
    token_hash = _hash_secret(plaintext)
    prefix = plaintext[:12]
    expires_at = datetime.now(UTC) + timedelta(minutes=duration_minutes)

    token = AgentToken(
        name=name,
        token_hash=token_hash,
        token_prefix=prefix,
        expires_at=expires_at,
    )
    db.add(token)
    await db.commit()
    await db.refresh(token)

    # Store in Redis with TTL for fast lookup + prefix index for O(1) validation
    try:
        r = get_redis()
        ttl = duration_minutes * 60
        key = f"agent:token:{token.id}"
        await r.hset(
            key,
            mapping={
                "id": token.id,
                "token_hash": token_hash,
                "token_prefix": prefix,
                "name": name,
                "expires_at": expires_at.isoformat(),
            },
        )
        await r.expire(key, ttl)
        # Prefix index: O(1) lookup by token prefix (first 12 chars)
        await r.set(f"agent:token_idx:{prefix}", key, ex=ttl)
    except Exception:
        logger.warning("Failed to store agent token in Redis (DB is source of truth)")

    return plaintext, token


async def sync_all_agent_tokens_to_redis(db: AsyncSession) -> int:
    """Re-sync all active agent tokens to Redis. Called on backend startup.

    Ensures tokens survive Redis restarts by repopulating from DB.
    Only syncs tokens that are not revoked and not yet expired.
    """
    now = datetime.now(UTC)
    result = await db.execute(
        select(AgentToken).where(
            AgentToken.revoked == False,  # noqa: E712
            AgentToken.expires_at > now,
        )
    )
    tokens = list(result.scalars().all())
    if not tokens:
        return 0

    synced = 0
    now_naive = now.replace(tzinfo=None)  # SQLite returns naive datetimes
    try:
        r = get_redis()
        for token in tokens:
            # Handle naive datetimes from SQLite (bug pattern #7)
            exp = token.expires_at
            if exp.tzinfo is not None:
                exp = exp.replace(tzinfo=None)
            remaining = int((exp - now_naive).total_seconds())
            if remaining <= 0:
                continue
            key = f"agent:token:{token.id}"
            mapping = {
                "id": token.id,
                "token_hash": token.token_hash,
                "token_prefix": token.token_prefix,
                "name": token.name,
                "expires_at": token.expires_at.isoformat(),
            }
            # Preserve usage data if already in Redis
            existing = await r.hgetall(key)
            if existing.get("last_used_at"):
                mapping["last_used_at"] = existing["last_used_at"]
            if existing.get("last_ip"):
                mapping["last_ip"] = existing["last_ip"]
            if existing.get("last_country"):
                mapping["last_country"] = existing["last_country"]
            await r.hset(key, mapping=mapping)
            await r.expire(key, remaining)
            await r.set(f"agent:token_idx:{token.token_prefix}", key, ex=remaining)
            synced += 1
    except Exception:
        logger.warning("Failed to sync agent tokens to Redis")

    logger.info("Synced %d agent tokens to Redis", synced)
    return synced


async def sync_all_oauth_sessions_to_redis(db: AsyncSession) -> int:
    """Re-sync all active OAuth sessions to Redis. Called on backend startup.

    Ensures OAuth session hashes survive Redis restarts, avoiding O(n)
    bcrypt fallback scan on every token validation.
    """
    now = datetime.now(UTC)
    result = await db.execute(
        select(AgentSession).where(
            AgentSession.revoked == False,  # noqa: E712
            AgentSession.expires_at > now,
        )
    )
    sessions = list(result.scalars().all())
    if not sessions:
        return 0

    # Pre-fetch all active clients to avoid N+1 queries
    client_result = await db.execute(
        select(OAuthClient).where(OAuthClient.revoked == False)  # noqa: E712
    )
    clients_by_id = {c.id: c for c in client_result.scalars().all()}

    synced = 0
    now_naive = now.replace(tzinfo=None)
    try:
        r = get_redis()
        for session in sessions:
            exp = session.expires_at
            if exp.tzinfo is not None:
                exp = exp.replace(tzinfo=None)
            remaining = int((exp - now_naive).total_seconds())
            if remaining <= 0:
                continue

            client = clients_by_id.get(session.oauth_client_id)
            if not client:
                continue

            key = f"agent:oauth:{session.id}"
            await r.hset(
                key,
                mapping={
                    "session_id": session.id,
                    "client_id": client.client_id,
                    "access_token_hash": session.access_token_hash,
                    "idle_timeout": str(client.idle_timeout_seconds),
                    "client_ip": session.client_ip or "",
                    "client_country": session.client_country or "",
                },
            )
            await r.expire(key, remaining)

            # We can't reconstruct the access_token prefix from hash,
            # so we skip prefix index rebuild — the fallback path will
            # back-fill it on first validation.
            synced += 1
    except Exception:
        logger.warning("Failed to sync OAuth sessions to Redis")

    if synced:
        logger.info("Synced %d OAuth sessions to Redis", synced)
    return synced


async def validate_agent_token(
    db: AsyncSession,
    token: str,
    client_ip: str | None = None,
    client_country: str | None = None,
) -> AgentToken | None:
    """Validate an agent token. Returns AgentToken if valid, None otherwise.

    Uses O(1) prefix-based lookup: the token_prefix (first 12 chars) is
    already stored in the DB, so we filter by prefix then verify one bcrypt.
    """
    now = datetime.now(UTC)
    prefix = token[:12]

    # O(1) lookup by prefix (unique per token)
    result = await db.execute(
        select(AgentToken).where(
            AgentToken.revoked == False,  # noqa: E712
            AgentToken.expires_at > now,
            AgentToken.token_prefix == prefix,
        )
    )
    candidate = result.scalar_one_or_none()

    if candidate and _verify_secret(token, candidate.token_hash):
        candidate.last_used_at = now
        if client_ip:
            candidate.last_ip = client_ip
        if client_country:
            candidate.last_country = client_country
        await db.commit()
        return candidate

    return None


async def sync_agent_token_usage_from_redis(db: AsyncSession) -> int:
    """Sync last_used_at and last_ip from Redis to DB for active agent tokens.

    Called before listing tokens so the UI shows fresh data.
    """
    now = datetime.now(UTC)
    result = await db.execute(
        select(AgentToken).where(
            AgentToken.revoked == False,  # noqa: E712
            AgentToken.expires_at > now,
        )
    )
    tokens = list(result.scalars().all())
    if not tokens:
        return 0

    updated = 0
    try:
        r = get_redis()
        for token in tokens:
            key = f"agent:token:{token.id}"
            data = await r.hgetall(key)
            if not data:
                continue
            last_used = data.get("last_used_at")
            last_ip = data.get("last_ip")
            changed = False
            if last_used:
                try:
                    used_dt = datetime.fromisoformat(last_used)
                    # Normalize to naive for SQLite comparison (bug pattern #7)
                    used_naive = used_dt.replace(tzinfo=None) if used_dt.tzinfo else used_dt
                    db_used = token.last_used_at
                    if db_used is not None and db_used.tzinfo is not None:
                        db_used = db_used.replace(tzinfo=None)
                    if db_used is None or used_naive > db_used:
                        token.last_used_at = used_naive
                        changed = True
                except (ValueError, TypeError):
                    pass
            if last_ip and last_ip != token.last_ip:
                token.last_ip = last_ip
                changed = True
            last_country = data.get("last_country")
            if last_country and last_country != token.last_country:
                token.last_country = last_country
                changed = True
            if changed:
                updated += 1
        if updated:
            await db.commit()
    except Exception:
        logger.warning("Failed to sync agent token usage from Redis")

    return updated


async def revoke_agent_token(db: AsyncSession, token_id: str) -> bool:
    """Revoke an agent token by ID."""
    result = await db.execute(select(AgentToken).where(AgentToken.id == token_id))
    token = result.scalar_one_or_none()
    if not token:
        return False

    token.revoked = True
    await db.commit()

    # Remove from Redis (token data + prefix index)
    try:
        r = get_redis()
        await r.delete(f"agent:token:{token_id}")
        if token.token_prefix:
            await r.delete(f"agent:token_idx:{token.token_prefix}")
    except Exception:
        logger.warning("Failed to remove agent token from Redis")

    return True


async def list_agent_tokens(
    db: AsyncSession, skip: int = 0, limit: int = 50, show_all: bool = False
) -> tuple[list[AgentToken], int]:
    """List agent tokens.

    If show_all=False (default): return all active tokens + 10 latest inactive.
    If show_all=True: return all tokens with standard pagination.
    """
    from datetime import datetime

    from sqlalchemy import func, or_

    count_result = await db.execute(select(func.count()).select_from(AgentToken))
    total = count_result.scalar() or 0

    if show_all:
        result = await db.execute(
            select(AgentToken).order_by(AgentToken.created_at.desc()).offset(skip).limit(limit)
        )
        items = list(result.scalars().all())
        return items, total

    now = datetime.now(UTC)

    # All active tokens (not revoked AND not expired)
    active_result = await db.execute(
        select(AgentToken)
        .where(AgentToken.revoked.is_(False), AgentToken.expires_at > now)
        .order_by(AgentToken.created_at.desc())
    )
    active = list(active_result.scalars().all())

    # 10 latest inactive (revoked OR expired)
    inactive_result = await db.execute(
        select(AgentToken)
        .where(or_(AgentToken.revoked.is_(True), AgentToken.expires_at <= now))
        .order_by(AgentToken.created_at.desc())
        .limit(10)
    )
    inactive = list(inactive_result.scalars().all())

    items = active + inactive
    items.sort(key=lambda t: t.created_at, reverse=True)
    return items, total


# ---------------------------------------------------------------------------
# OAuth2 Clients
# ---------------------------------------------------------------------------


async def create_oauth_client(
    db: AsyncSession,
    name: str,
    idle_timeout_minutes: int,
) -> tuple[str, str, OAuthClient]:
    """Create an OAuth2 client.

    Returns (client_id, client_secret_plaintext, OAuthClient model).
    """
    if idle_timeout_minutes < 15 or idle_timeout_minutes > 10080:
        raise ValueError("Idle timeout must be between 15 and 10080 minutes")

    client_id = _generate_random(_CLIENT_ID_PREFIX, 24)
    client_secret = _generate_random(_CLIENT_SECRET_PREFIX, 40)

    client = OAuthClient(
        name=name,
        client_id=client_id,
        client_secret_hash=_hash_secret(client_secret),
        client_secret_prefix=client_secret[:12],
        idle_timeout_seconds=idle_timeout_minutes * 60,
    )
    db.add(client)
    await db.commit()
    await db.refresh(client)

    return client_id, client_secret, client


async def exchange_credentials(
    db: AsyncSession,
    client_id: str,
    client_secret: str,
    client_ip: str | None = None,
    client_country: str | None = None,
) -> tuple[str, str, int] | None:
    """Exchange client credentials for access + refresh tokens.

    Returns (access_token, refresh_token, expires_in_seconds) or None if invalid.
    """
    result = await db.execute(
        select(OAuthClient).where(
            OAuthClient.client_id == client_id,
            OAuthClient.revoked == False,  # noqa: E712
        )
    )
    client = result.scalar_one_or_none()
    if not client:
        return None

    if not _verify_secret(client_secret, client.client_secret_hash):
        return None

    # Generate access + refresh tokens
    access_token = _generate_random("smkr_at_", 48)
    refresh_token = _generate_random("smkr_rt_", 48)
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=client.idle_timeout_seconds)

    session = AgentSession(
        oauth_client_id=client.id,
        access_token_hash=_hash_secret(access_token),
        refresh_token_hash=_hash_secret(refresh_token),
        expires_at=expires_at,
        last_activity_at=now,
        client_ip=client_ip,
        client_country=client_country,
    )
    db.add(session)

    client.last_used_at = now
    if client_ip:
        client.last_ip = client_ip
    if client_country:
        client.last_country = client_country

    await db.commit()
    await db.refresh(session)

    # Cache in Redis + prefix index for O(1) lookup
    try:
        r = get_redis()
        ttl = client.idle_timeout_seconds
        key = f"agent:oauth:{session.id}"
        await r.hset(
            key,
            mapping={
                "session_id": session.id,
                "client_id": client.client_id,
                "access_token_hash": session.access_token_hash,
                "idle_timeout": str(ttl),
                "client_ip": client_ip or "",
                "client_country": client_country or "",
            },
        )
        await r.expire(key, ttl)
        # Prefix index for O(1) lookup by access token prefix
        await r.set(f"agent:oauth_idx:{access_token[:12]}", key, ex=ttl)
    except Exception:
        logger.warning("Failed to cache OAuth session in Redis")

    return access_token, refresh_token, client.idle_timeout_seconds


async def refresh_access_token(
    db: AsyncSession,
    refresh_token: str,
    client_ip: str | None = None,
    client_country: str | None = None,
) -> tuple[str, str, int] | None:
    """Refresh an access token using a refresh token.

    Returns (new_access_token, new_refresh_token, expires_in) or None if invalid.
    """
    now = datetime.now(UTC)

    # Find session by checking refresh token hashes
    result = await db.execute(
        select(AgentSession).where(
            AgentSession.revoked == False,  # noqa: E712
        )
    )
    sessions = result.scalars().all()

    matched_session = None
    for s in sessions:
        if _verify_secret(refresh_token, s.refresh_token_hash):
            matched_session = s
            break

    if not matched_session:
        return None

    # Get parent client
    client_result = await db.execute(
        select(OAuthClient).where(
            OAuthClient.id == matched_session.oauth_client_id,
            OAuthClient.revoked == False,  # noqa: E712
        )
    )
    client = client_result.scalar_one_or_none()
    if not client:
        return None

    # Revoke old session
    matched_session.revoked = True

    # Create new session with fresh tokens
    new_access = _generate_random("smkr_at_", 48)
    new_refresh = _generate_random("smkr_rt_", 48)
    expires_at = now + timedelta(seconds=client.idle_timeout_seconds)

    new_session = AgentSession(
        oauth_client_id=client.id,
        access_token_hash=_hash_secret(new_access),
        refresh_token_hash=_hash_secret(new_refresh),
        expires_at=expires_at,
        last_activity_at=now,
        client_ip=client_ip,
        client_country=client_country,
    )
    db.add(new_session)

    client.last_used_at = now
    if client_ip:
        client.last_ip = client_ip
    if client_country:
        client.last_country = client_country

    await db.commit()
    await db.refresh(new_session)

    # Update Redis
    try:
        r = get_redis()
        ttl = client.idle_timeout_seconds
        # Remove old session + its prefix index
        await r.delete(f"agent:oauth:{matched_session.id}")
        # Cache new session + prefix index for O(1) lookup
        key = f"agent:oauth:{new_session.id}"
        await r.hset(
            key,
            mapping={
                "session_id": new_session.id,
                "client_id": client.client_id,
                "access_token_hash": new_session.access_token_hash,
                "idle_timeout": str(ttl),
                "client_ip": client_ip or "",
                "client_country": client_country or "",
            },
        )
        await r.expire(key, ttl)
        await r.set(f"agent:oauth_idx:{new_access[:12]}", key, ex=ttl)
    except Exception:
        logger.warning("Failed to update OAuth session in Redis")

    return new_access, new_refresh, client.idle_timeout_seconds


async def validate_oauth_token(
    db: AsyncSession,
    access_token: str,
    client_ip: str | None = None,
    client_country: str | None = None,
) -> OAuthClient | None:
    """Validate an OAuth2 access token. Returns OAuthClient if valid.

    Tries Redis prefix index for O(1) lookup first, falls back to DB scan.
    """
    now = datetime.now(UTC)
    matched_session = None

    # Fast path: Redis prefix index → session ID → single bcrypt verify
    try:
        r = get_redis()
        prefix = access_token[:12]
        idx_key = f"agent:oauth_idx:{prefix}"
        cached_key = await r.get(idx_key)
        if cached_key:
            session_id = cached_key.split(":")[-1]  # "agent:oauth:{id}" → id
            result = await db.execute(
                select(AgentSession).where(
                    AgentSession.id == session_id,
                    AgentSession.revoked == False,  # noqa: E712
                    AgentSession.expires_at > now,
                )
            )
            candidate = result.scalar_one_or_none()
            if candidate and _verify_secret(access_token, candidate.access_token_hash):
                matched_session = candidate
    except Exception:
        pass

    # Fallback: O(n) scan (backward compat for sessions created before index)
    if not matched_session:
        result = await db.execute(
            select(AgentSession).where(
                AgentSession.revoked == False,  # noqa: E712
                AgentSession.expires_at > now,
            )
        )
        for s in result.scalars().all():
            if _verify_secret(access_token, s.access_token_hash):
                matched_session = s
                # Back-fill prefix index for future O(1) lookups
                try:
                    r = get_redis()
                    key = f"agent:oauth:{s.id}"
                    s_exp = s.expires_at
                    if s_exp.tzinfo is not None:
                        s_exp = s_exp.replace(tzinfo=None)
                    idx_ttl = max(int((s_exp - now.replace(tzinfo=None)).total_seconds()), 60)
                    await r.set(f"agent:oauth_idx:{access_token[:12]}", key, ex=idx_ttl)
                except Exception:
                    pass
                break

    if not matched_session:
        return None

    # Get parent client
    client_result = await db.execute(
        select(OAuthClient).where(
            OAuthClient.id == matched_session.oauth_client_id,
            OAuthClient.revoked == False,  # noqa: E712
        )
    )
    client = client_result.scalar_one_or_none()
    if not client:
        return None

    # Slide the expiry window
    matched_session.last_activity_at = now
    matched_session.expires_at = now + timedelta(seconds=client.idle_timeout_seconds)
    if client_ip:
        matched_session.client_ip = client_ip
        client.last_ip = client_ip
    if client_country:
        matched_session.client_country = client_country
        client.last_country = client_country

    client.last_used_at = now
    await db.commit()

    # Refresh Redis TTL
    try:
        r = get_redis()
        key = f"agent:oauth:{matched_session.id}"
        await r.expire(key, client.idle_timeout_seconds)

    except Exception:
        pass

    return client


async def introspect_token(
    db: AsyncSession,
    token: str,
) -> dict:
    """RFC 7662 token introspection.

    Uses prefix-based O(1) lookup for agent tokens (DB column).
    Falls back to O(n) for OAuth tokens (no DB prefix column on sessions).
    """
    now = datetime.now(UTC)
    now_naive = now.replace(tzinfo=None)  # SQLite may return naive datetimes
    prefix = token[:12]

    # Try as agent token first — O(1) via prefix column
    result = await db.execute(
        select(AgentToken).where(
            AgentToken.revoked == False,  # noqa: E712
            AgentToken.token_prefix == prefix,
        )
    )
    candidate = result.scalar_one_or_none()
    if candidate and _verify_secret(token, candidate.token_hash):
        ea = candidate.expires_at
        exp = ea.replace(tzinfo=None) if ea.tzinfo else ea
        active = exp > now_naive
        return {
            "active": active,
            "token_type": "agent_token",
            "scope": " ".join(candidate.scopes or ["*"]),
            "exp": int(candidate.expires_at.timestamp()) if candidate.expires_at else None,
            "iat": int(candidate.created_at.timestamp()) if candidate.created_at else None,
            "client_id": candidate.token_prefix,
        }

    # Try as OAuth access token — use Redis prefix index if available
    matched_session = None
    try:
        r = get_redis()
        idx_key = f"agent:oauth_idx:{prefix}"
        cached_key = await r.get(idx_key)
        if cached_key:
            session_id = cached_key.split(":")[-1]
            sr = await db.execute(
                select(AgentSession).where(
                    AgentSession.id == session_id,
                    AgentSession.revoked == False,  # noqa: E712
                )
            )
            s = sr.scalar_one_or_none()
            if s and _verify_secret(token, s.access_token_hash):
                matched_session = s
    except Exception:
        pass

    # Fallback: O(n) scan for OAuth sessions
    if not matched_session:
        session_result = await db.execute(
            select(AgentSession).where(
                AgentSession.revoked == False,  # noqa: E712
            )
        )
        for s in session_result.scalars().all():
            if _verify_secret(token, s.access_token_hash):
                matched_session = s
                break

    if matched_session:
        client_result = await db.execute(
            select(OAuthClient).where(OAuthClient.id == matched_session.oauth_client_id)
        )
        client = client_result.scalar_one_or_none()
        sea = matched_session.expires_at
        s_exp = sea.replace(tzinfo=None) if sea.tzinfo else sea
        active = s_exp > now_naive and client and not client.revoked
        return {
            "active": active,
            "token_type": "bearer",
            "scope": " ".join(client.scopes or ["*"]) if client else "*",
            "exp": int(matched_session.expires_at.timestamp()),
            "iat": int(matched_session.created_at.timestamp()),
            "client_id": client.client_id if client else None,
        }

    return {"active": False}


async def revoke_oauth_client(db: AsyncSession, client_db_id: str) -> bool:
    """Revoke an OAuth2 client and all its sessions."""
    result = await db.execute(select(OAuthClient).where(OAuthClient.id == client_db_id))
    client = result.scalar_one_or_none()
    if not client:
        return False

    client.revoked = True

    # Revoke all sessions
    session_result = await db.execute(
        select(AgentSession).where(
            AgentSession.oauth_client_id == client.id,
            AgentSession.revoked == False,  # noqa: E712
        )
    )
    for s in session_result.scalars().all():
        s.revoked = True
        try:
            r = get_redis()
            await r.delete(f"agent:oauth:{s.id}")
            # Clean up any prefix index pointing to this session
            # (We can't recover the original token prefix from the hash, so
            # rely on Redis TTL for cleanup. The validate path will reject
            # revoked sessions from the DB even if index still exists.)
        except Exception:
            pass

    await db.commit()
    return True


async def list_oauth_clients(
    db: AsyncSession, skip: int = 0, limit: int = 50
) -> tuple[list[OAuthClient], int]:
    """List OAuth clients with pagination."""
    from sqlalchemy import func

    count_result = await db.execute(select(func.count()).select_from(OAuthClient))
    total = count_result.scalar() or 0

    result = await db.execute(
        select(OAuthClient).order_by(OAuthClient.created_at.desc()).offset(skip).limit(limit)
    )
    items = list(result.scalars().all())
    return items, total


# ---------------------------------------------------------------------------
# Activity logging (Redis)
# ---------------------------------------------------------------------------


async def record_activity(
    token_prefix: str,
    tool_name: str,
    ip: str,
    success: bool,
) -> None:
    """Record an agent activity entry in Redis."""
    try:
        r = get_redis()
        entry = json.dumps(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "token_prefix": token_prefix,
                "tool_name": tool_name,
                "ip": ip,
                "success": success,
            }
        )
        await r.lpush(_REDIS_ACTIVITY_KEY, entry)
        await r.ltrim(_REDIS_ACTIVITY_KEY, 0, _REDIS_ACTIVITY_MAX - 1)

    except Exception:
        logger.warning("Failed to record agent activity in Redis")


async def get_activity_log(skip: int = 0, limit: int = 50) -> tuple[list[dict], int]:
    """Get agent activity log from Redis."""
    try:
        r = get_redis()
        total = await r.llen(_REDIS_ACTIVITY_KEY)
        raw = await r.lrange(_REDIS_ACTIVITY_KEY, skip, skip + limit - 1)

        items = [json.loads(entry) for entry in raw]
        return items, total
    except Exception:
        logger.warning("Failed to read agent activity from Redis")
        return [], 0


async def get_activity_stats() -> dict:
    """Aggregate agent activity stats from Redis (for dashboard)."""
    try:
        r = get_redis()
        raw = await r.lrange(_REDIS_ACTIVITY_KEY, 0, _REDIS_ACTIVITY_MAX - 1)
        if not raw:
            return {
                "total_calls": 0,
                "success_count": 0,
                "error_count": 0,
                "success_rate": 0.0,
                "tool_usage": {},
            }

        from collections import Counter

        success_count = 0
        error_count = 0
        tool_counter: Counter[str] = Counter()

        for entry_raw in raw:
            try:
                entry = json.loads(entry_raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if entry.get("success"):
                success_count += 1
            else:
                error_count += 1
            tool_counter[entry.get("tool_name", "unknown")] += 1

        total_calls = success_count + error_count
        success_rate = round((success_count / total_calls * 100) if total_calls > 0 else 0.0, 1)
        return {
            "total_calls": total_calls,
            "success_count": success_count,
            "error_count": error_count,
            "success_rate": success_rate,
            "tool_usage": dict(tool_counter.most_common()),
        }
    except Exception:
        logger.warning(
            "Failed to aggregate agent activity stats from Redis",
            exc_info=True,
        )
        return {
            "total_calls": 0,
            "success_count": 0,
            "error_count": 0,
            "success_rate": 0.0,
            "tool_usage": {},
        }


# ---------------------------------------------------------------------------
# Rate limiting (Redis)
# ---------------------------------------------------------------------------


async def check_agent_rate_limit(token_prefix: str, max_per_minute: int = 120) -> bool:
    """Check per-token rate limit. Returns True if allowed."""
    try:
        r = get_redis()
        key = f"agent:rate:{token_prefix}"
        current = await r.incr(key)
        if current == 1:
            await r.expire(key, 60)

        return current <= max_per_minute
    except Exception:
        # Allow on Redis failure
        return True
