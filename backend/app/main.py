"""SMKRV MCP Studio — FastAPI application entrypoint."""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text

from app.config import _is_production, settings, validate_production_secrets
from app.database import engine
from app.models import Base
from app.version import APP_VERSION

logger = logging.getLogger(__name__)

_SSL_RENEWAL_INTERVAL = 86400  # 24 hours
_RATE_LIMIT_CLEANUP_INTERVAL = 3600  # 1 hour


async def _apply_schema() -> None:
    """Apply schema via metadata.create_all (async-safe).

    Uses create_all which is idempotent — safe for production with SQLite.
    Reuses the shared engine from app.database (A1-01: no duplicate engine).
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Inline migrations for columns added after initial schema
    _migrations = [
        ("server_config", "auth_bearer_token_prefix", "VARCHAR(20)"),
        ("server_config", "auth_bearer_last_used_at", "DATETIME"),
        ("server_config", "auth_bearer_last_ip", "VARCHAR(45)"),
        ("server_config", "studio_port", "INTEGER DEFAULT 3000"),
        ("server_config", "studio_ssl_port", "INTEGER DEFAULT 443"),
        # GeoIP country columns (migration 009)
        ("server_config", "auth_bearer_last_country", "VARCHAR(2)"),
        ("agent_tokens", "last_country", "VARCHAR(2)"),
        ("oauth_clients", "last_country", "VARCHAR(2)"),
        ("agent_sessions", "client_country", "VARCHAR(2)"),
        # Prompt guard config columns (migration 011)
        ("server_config", "prompt_guard_enabled", "BOOLEAN DEFAULT 1"),
        ("server_config", "prompt_guard_l0_enabled", "BOOLEAN DEFAULT 1"),
        ("server_config", "prompt_guard_l1_enabled", "BOOLEAN DEFAULT 1"),
        ("server_config", "prompt_guard_l0_entity_types",
         'JSON DEFAULT \'["tool","prompt","resource"]\''),
        ("server_config", "prompt_guard_l1_entity_types",
         'JSON DEFAULT \'["tool","prompt","resource"]\''),
        ("server_config", "prompt_guard_block_severity", "VARCHAR(20) DEFAULT 'HIGH'"),
        ("server_config", "prompt_guard_ml_threshold", "FLOAT DEFAULT 0.5"),
        ("server_config", "prompt_guard_custom_patterns", "JSON DEFAULT '[]'"),
        ("server_config", "prompt_guard_disabled_patterns", "JSON DEFAULT '[]'"),
    ]
    async with engine.begin() as conn:
        for table, column, col_type in _migrations:
            try:
                await conn.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
                )
                logger.info("Migration: added %s.%s", table, column)
            except Exception as e:
                err_msg = str(e).lower()
                if "duplicate column" in err_msg or "already exists" in err_msg:
                    pass  # Expected -- column added in a previous run
                else:
                    logger.warning("Migration %s.%s failed: %s", table, column, e)

    logger.info("Database schema applied successfully")


async def _initial_deploy() -> None:
    """Generate default server.py on first startup so the MCP container can boot."""
    generated_dir = Path(settings.generated_dir)
    server_py = generated_dir / "server.py"

    if server_py.exists():
        return

    logger.info("First startup — generating initial MCP server files...")

    from app.database import async_session_factory
    from app.services.deployer import Deployer

    async with async_session_factory() as db:
        deployer = Deployer()
        result = await deployer.deploy(db)
        if result.deployed:
            logger.info("Initial deploy: %d files generated", result.files_generated)
        else:
            logger.warning("Initial deploy failed: %s", result.message)


async def _sync_nginx_config() -> None:
    """Regenerate nginx config from DB on every startup.

    Ensures nginx config matches current ServerConfig (SSL domains,
    agent-mcp toggle, etc.) without manual intervention. Idempotent —
    safe to call on every boot. The .reload flag triggers nginx reload
    via the watchdog (frontend entrypoint or supervisord watcher).
    """
    from app.database import async_session_factory
    from app.models.server_config import ServerConfig
    from app.services.nginx_generator import NginxGenerator

    try:
        async with async_session_factory() as db:
            result = await db.execute(select(ServerConfig))
            config = result.scalar_one_or_none()
            if not config:
                logger.debug("No ServerConfig in DB — skipping nginx config sync")
                return

            generator = NginxGenerator()
            path = await generator.generate_and_write(db)
            logger.info("Nginx config synced on startup: %s", path)
    except Exception:
        logger.warning("Failed to sync nginx config on startup", exc_info=True)


async def _sync_agent_tokens_to_redis() -> None:
    """Re-sync active agent tokens to Redis on every startup.

    Ensures tokens survive Redis restarts (agent-mcp validates via Redis only).
    """
    from app.database import async_session_factory
    from app.services.agent_auth import sync_all_agent_tokens_to_redis

    try:
        async with async_session_factory() as db:
            count = await sync_all_agent_tokens_to_redis(db)
            if count:
                logger.info("Agent tokens synced to Redis on startup: %d", count)
    except Exception:
        logger.warning("Failed to sync agent tokens to Redis on startup", exc_info=True)


async def _sync_oauth_sessions_to_redis() -> None:
    """Re-sync active OAuth sessions to Redis on every startup.

    Ensures O(1) prefix-index lookups survive Redis restarts.
    """
    from app.database import async_session_factory
    from app.services.agent_auth import sync_all_oauth_sessions_to_redis

    try:
        async with async_session_factory() as db:
            count = await sync_all_oauth_sessions_to_redis(db)
            if count:
                logger.info("OAuth sessions synced to Redis on startup: %d", count)
    except Exception:
        logger.warning("Failed to sync OAuth sessions to Redis on startup", exc_info=True)


async def _ssl_renewal_loop() -> None:
    """Background task: check and renew SSL certificates every 24 hours."""
    from app.database import async_session_factory
    from app.models.server_config import ServerConfig
    from app.services.nginx_generator import NginxGenerator
    from app.services.ssl_manager import SSLManager

    while True:
        await asyncio.sleep(_SSL_RENEWAL_INTERVAL)
        try:
            async with async_session_factory() as db:
                result = await db.execute(select(ServerConfig))
                config = result.scalar_one_or_none()
                if not config:
                    continue
                if not config.ssl_enabled or not config.ssl_auto_renew:
                    continue
                if not config.ssl_ui_domain or not config.ssl_email:
                    continue

                mgr = SSLManager()
                cert_info = mgr.get_cert_status(config.ssl_ui_domain)
                status = cert_info.get("status", "none")

                if status not in ("expiring_soon", "expired"):
                    logger.debug("SSL cert OK (status=%s), no renewal needed", status)
                    continue

                logger.info("SSL auto-renewal: cert status=%s, renewing...", status)
                domains = [config.ssl_ui_domain]
                if config.ssl_mcp_domain and config.ssl_mcp_domain != config.ssl_ui_domain:
                    domains.append(config.ssl_mcp_domain)
                if config.agent_mcp_domain and config.agent_mcp_domain not in domains:
                    domains.append(config.agent_mcp_domain)

                result_data = await mgr.issue_certificate(
                    domains=domains,
                    email=config.ssl_email,
                    challenge_type=config.ssl_challenge_type,
                    dns_provider=config.ssl_dns_provider,
                    dns_credentials_encrypted=config.ssl_dns_credentials,
                    force_renew=True,
                )

                if result_data["success"]:
                    config.ssl_cert_status = "issued"
                    await db.commit()
                    # Trigger nginx reload
                    nginx_gen = NginxGenerator()
                    await nginx_gen.generate_and_write(db)
                    logger.info("SSL auto-renewal succeeded for %s", domains)
                else:
                    logger.error("SSL auto-renewal failed: %s", result_data["message"])

        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Error in SSL renewal loop")


async def _rate_limit_cleanup_loop() -> None:
    """Background task: periodically clean up expired rate limit entries.

    With Redis-backed rate limiting, TTL handles expiry automatically.
    This loop is kept for any edge-case cleanup.
    """
    from app.services.auth import cleanup_expired_attempts

    while True:
        await asyncio.sleep(_RATE_LIMIT_CLEANUP_INTERVAL)
        try:
            removed = await cleanup_expired_attempts()
            if removed > 0:
                logger.debug("Rate limit cleanup: removed %d expired entries", removed)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Error in rate limit cleanup loop")


async def _initial_metrics_snapshot() -> None:
    """Persist completed daily metrics from Redis to DB on startup."""
    from app.database import async_session_factory
    from app.services.metrics_persistence import persist_daily_snapshots

    try:
        async with async_session_factory() as db:
            await persist_daily_snapshots(db)
    except Exception:
        logger.warning("Initial metrics snapshot failed", exc_info=True)


async def _metrics_snapshot_loop() -> None:
    """Background task: persist daily metric snapshots from Redis to DB every 6h."""
    from app.database import async_session_factory
    from app.services.metrics_persistence import persist_daily_snapshots

    while True:
        await asyncio.sleep(6 * 3600)  # 6 hours
        try:
            async with async_session_factory() as db:
                await persist_daily_snapshots(db)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.warning("Metrics snapshot loop failed", exc_info=True)


async def _prompt_guard_model_check_loop() -> None:
    """Background task: periodically check for ML model updates via checksum."""
    if not settings.prompt_guard_enabled or not settings.prompt_guard_ml_download:
        return
    try:
        from scripts.download_prompt_guard_model import check_and_update
    except ModuleNotFoundError:
        logger.info("Prompt Guard ML model update loop skipped (scripts package not available)")
        return
    interval = max(settings.prompt_guard_ml_update_interval_hours, 1) * 3600
    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(check_and_update, settings.prompt_guard_model_dir)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.warning("Prompt Guard model update check failed", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create directories, apply schema, run initial deploy, and start background tasks."""
    validate_production_secrets()
    Path("data").mkdir(exist_ok=True)
    Path(settings.generated_dir).mkdir(exist_ok=True)
    await _apply_schema()
    await asyncio.wait_for(_initial_deploy(), timeout=30)
    await _sync_nginx_config()
    await _sync_agent_tokens_to_redis()
    await _sync_oauth_sessions_to_redis()
    await _initial_metrics_snapshot()

    # Prompt Guard: download/update ML model on startup (non-blocking).
    # The `scripts` package is only available in dev — not bundled in Docker.
    if settings.prompt_guard_enabled and settings.prompt_guard_ml_download:
        try:
            from scripts.download_prompt_guard_model import check_and_update

            model_dir = settings.prompt_guard_model_dir
            await asyncio.to_thread(check_and_update, model_dir)
        except ModuleNotFoundError:
            logger.info("Prompt Guard ML model download skipped (scripts package not available)")
        except Exception:
            logger.warning("Prompt Guard model check failed", exc_info=True)

    # Start background tasks
    from app.services.log_consumer import run_log_consumer, run_log_pruner

    renewal_task = asyncio.create_task(_ssl_renewal_loop())
    cleanup_task = asyncio.create_task(_rate_limit_cleanup_loop())
    log_consumer_task = asyncio.create_task(run_log_consumer())
    log_pruner_task = asyncio.create_task(run_log_pruner())
    model_check_task = asyncio.create_task(_prompt_guard_model_check_loop())
    metrics_snapshot_task = asyncio.create_task(_metrics_snapshot_loop())

    yield

    # Cancel background tasks on shutdown
    for task in (renewal_task, cleanup_task, log_consumer_task, log_pruner_task,
                 model_check_task, metrics_snapshot_task):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Close shared Redis pool
    from app.services.redis_client import close_redis_pool

    await close_redis_pool()


app = FastAPI(
    title="SMKRV MCP Studio",
    description=(
        "Visual constructor for MCP (Model Context Protocol) servers. "
        "Build SQL-based tools, resources, and prompts through a web UI."
    ),
    version=APP_VERSION,
    docs_url=None,       # Disabled -- self-hosted below (no CDN)
    redoc_url=None,      # Disabled -- self-hosted below (no CDN)
    openapi_url="/api/openapi.json",
    openapi_tags=[
        {"name": "auth", "description": "Admin authentication, session, profile, and 2FA"},
        {"name": "connections", "description": "Database connection management"},
        {"name": "tools", "description": "MCP tool definitions"},
        {"name": "resources", "description": "MCP resource definitions"},
        {"name": "prompts", "description": "MCP prompt templates"},
        {"name": "schema", "description": "Database schema introspection"},
        {"name": "preview", "description": "Read-only SQL query preview"},
        {"name": "deploy", "description": "MCP server deployment lifecycle"},
        {"name": "server", "description": "Server configuration and SSL management"},
        {"name": "history", "description": "Entity change audit trail and rollback"},
        {"name": "queue", "description": "Redis query queue metrics"},
        {"name": "metrics", "description": "Operational per-tool metrics"},
        {"name": "export-import", "description": "Configuration backup and restore"},
        {"name": "agent-tokens", "description": "Temporary agent access tokens"},
        {"name": "oauth-clients", "description": "OAuth2 client credentials for agents"},
        {"name": "agent-activity", "description": "Agent access activity log"},
        {"name": "agent-auth", "description": "Internal OAuth2 token exchange (agent-mcp)"},
        {"name": "request-logs", "description": "Tool execution request logs"},
        {"name": "stats", "description": "Aggregated dashboard statistics"},
    ],
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Import and include routers
from app.dependencies import get_agent_or_admin  # noqa: E402
from app.routers import (  # noqa: E402, I001
    agent_activity,
    agent_auth,
    agent_tokens,
    auth,
    connections,
    deploy,
    export_import,
    history,
    mcp_tokens,
    metrics,
    oauth_clients,
    preview,
    prompts,
    queue,
    request_logs,
    resources,
    schema,
    server,
    stats,
    tools,
)

# Auth router — no authentication required (handles login/setup)
app.include_router(auth.router, prefix="/api/v1", tags=["auth"])

# All other routers — require authenticated admin session
_auth_deps = [Depends(get_agent_or_admin)]
_prefix = "/api/v1"


def _add(router, tag, *, auth=True):
    deps = _auth_deps if auth else []
    app.include_router(router, prefix=_prefix, tags=[tag], dependencies=deps)


_add(connections.router, "connections")
_add(tools.router, "tools")
_add(resources.router, "resources")
_add(prompts.router, "prompts")
_add(schema.router, "schema")
_add(preview.router, "preview")
# Deploy: auth per-endpoint (HTTP=get_agent_or_admin, WS=require_admin_ws)
_add(deploy.router, "deploy", auth=False)
_add(server.router, "server")
_add(history.router, "history")
_add(queue.router, "queue")
_add(metrics.router, "metrics")
_add(request_logs.router, "request-logs")
_add(stats.router, "stats")
_add(export_import.router, "export-import")
# Agent access routers — require authenticated admin session
_add(agent_tokens.router, "agent-tokens")
_add(mcp_tokens.router, "mcp-tokens")
_add(oauth_clients.router, "oauth-clients")
_add(agent_activity.router, "agent-activity")
# Agent auth — internal (service token, not cookie auth)
_add(agent_auth.router, "agent-auth", auth=False)


# ---------------------------------------------------------------------------
# Self-hosted API docs (no CDN — air-gapped support)
# ---------------------------------------------------------------------------
_static_dir = Path(__file__).parent / "static"
if _static_dir.is_dir():
    app.mount("/api/static", StaticFiles(directory=str(_static_dir)), name="api-static")


@app.get("/api/docs", include_in_schema=False)
async def custom_swagger_ui():
    """Swagger UI served from local static files (no inline scripts for CSP)."""
    return HTMLResponse("""\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SMKRV MCP Studio — API Docs</title>
<link rel="stylesheet" href="/api/static/swagger-ui/swagger-ui.css">
</head>
<body>
<div id="swagger-ui"></div>
<script src="/api/static/swagger-ui/swagger-ui-bundle.js"></script>
<script src="/api/static/swagger-ui/swagger-init.js"></script>
</body>
</html>""")


@app.get("/api/redoc", include_in_schema=False)
async def custom_redoc():
    """ReDoc served from local static files (no inline scripts for CSP)."""
    return HTMLResponse("""\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SMKRV MCP Studio — API Reference</title>
<style>body{margin:0;padding:0}</style>
</head>
<body>
<div id="redoc-container"></div>
<script src="/api/static/redoc/redoc.standalone.js"></script>
<script src="/api/static/redoc/redoc-init.js"></script>
</body>
</html>""")


@app.get("/api/health")
async def health_check() -> dict:
    """Health check endpoint."""
    return {"status": "ok", "version": APP_VERSION}
