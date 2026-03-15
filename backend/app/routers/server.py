"""Server configuration and management endpoints."""

import logging
import os
import secrets
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.server_config import ServerConfig
from app.schemas.server import (
    BulkScanResponse,
    EntityScanResult,
    GeneratedFile,
    PatternsListResponse,
    PromptGuardPatternResponse,
    RotateBearerResponse,
    ScanDetection,
    ServerConfigResponse,
    ServerConfigUpdate,
    SSLIssueRequest,
    SSLIssueResponse,
    SSLStatusResponse,
    ValidatePatternRequest,
    ValidatePatternResponse,
)
from app.services.history import compute_changes, model_to_dict, record_change

logger = logging.getLogger(__name__)

router = APIRouter()


def _active_studio_port() -> int:
    """Return the currently running Studio UI port from env."""
    return int(os.getenv("STUDIO_PORT", "3000"))


def _active_studio_ssl_port() -> int:
    """Return the currently running Studio SSL port from env."""
    return int(os.getenv("STUDIO_SSL_PORT", "443"))


async def _get_or_create_config(db: AsyncSession) -> ServerConfig:
    """Get the singleton server config, creating if needed."""
    result = await db.execute(select(ServerConfig))
    config = result.scalar_one_or_none()
    if not config:
        config = ServerConfig(
            id=str(uuid.uuid4()),
            studio_port=_active_studio_port(),
            studio_ssl_port=_active_studio_ssl_port(),
        )
        db.add(config)
        await db.commit()
        await db.refresh(config)
    return config


def _config_response(config: ServerConfig) -> ServerConfigResponse:
    """Build response with active port values injected from env."""
    resp = ServerConfigResponse.model_validate(config, from_attributes=True)
    resp.studio_port_active = _active_studio_port()
    resp.studio_ssl_port_active = _active_studio_ssl_port()
    return resp


@router.get("/server/config", response_model=ServerConfigResponse)
async def get_server_config(db: AsyncSession = Depends(get_db)) -> ServerConfigResponse:
    """Get current server configuration."""
    config = await _get_or_create_config(db)
    return _config_response(config)


@router.patch("/server/config", response_model=ServerConfigResponse)
async def update_server_config(
    data: ServerConfigUpdate, db: AsyncSession = Depends(get_db)
) -> ServerConfigResponse:
    """Update server configuration."""
    config = await _get_or_create_config(db)
    before = model_to_dict(config)

    update_data = data.model_dump(exclude_unset=True)

    # NOTE: global_variables are admin-authored config (VAT rates, dates, etc.)
    # and should NOT be scanned by the prompt injection guard — the admin IS the
    # trusted user.  Guard is for untrusted external input only.

    # Encrypt sensitive fields
    from app.services.crypto import encrypt

    if "auth_bearer_token" in update_data and update_data["auth_bearer_token"]:
        raw_token = update_data["auth_bearer_token"]
        update_data["auth_bearer_token"] = encrypt(raw_token)
        update_data["auth_bearer_token_prefix"] = raw_token[:12]
        # Reset usage tracking on manual token change
        update_data["auth_bearer_last_used_at"] = None
        update_data["auth_bearer_last_ip"] = None
        update_data["auth_bearer_last_country"] = None

    if "ssl_dns_credentials" in update_data and update_data["ssl_dns_credentials"]:
        update_data["ssl_dns_credentials"] = encrypt(update_data["ssl_dns_credentials"])

    if "oauth_clients_json" in update_data and update_data["oauth_clients_json"]:
        update_data["oauth_clients_json"] = encrypt(update_data["oauth_clients_json"])

    has_secret = (
        "oauth_introspection_client_secret" in update_data
        and update_data["oauth_introspection_client_secret"]
    )
    if has_secret:
        update_data["oauth_introspection_client_secret"] = encrypt(
            update_data["oauth_introspection_client_secret"]
        )

    for field, value in update_data.items():
        setattr(config, field, value)

    changes = compute_changes(before, data.model_dump(exclude_unset=True))
    changes.pop("auth_bearer_token", None)
    changes.pop("ssl_dns_credentials", None)
    changes.pop("oauth_clients_json", None)
    changes.pop("oauth_introspection_client_secret", None)
    await record_change(
        db, entity_type="server_config", entity_id=config.id,
        entity_name=config.server_name, action="update", snapshot=before, changes=changes,
    )
    await db.commit()
    await db.refresh(config)
    return _config_response(config)


@router.post("/server/config/rotate-bearer", response_model=RotateBearerResponse)
async def rotate_bearer_token(
    db: AsyncSession = Depends(get_db),
) -> RotateBearerResponse:
    """Generate a new random bearer token for the MCP server.

    Returns the plaintext token once — it will never be shown again.
    """
    config = await _get_or_create_config(db)
    before = model_to_dict(config)

    from app.services.crypto import encrypt

    raw_token = f"mcp_{secrets.token_urlsafe(48)}"
    prefix = raw_token[:12]

    config.auth_bearer_token = encrypt(raw_token)
    config.auth_bearer_token_prefix = prefix
    config.auth_bearer_last_used_at = None
    config.auth_bearer_last_ip = None
    config.auth_bearer_last_country = None
    config.auth_type = "bearer"

    await record_change(
        db,
        entity_type="server_config",
        entity_id=config.id,
        entity_name=config.server_name,
        action="update",
        snapshot=before,
        changes={"auth_bearer_token": "***rotated***"},
    )
    await db.commit()
    await db.refresh(config)

    return RotateBearerResponse(token=raw_token, token_prefix=prefix)


@router.get("/server/geoip-status")
async def geoip_status() -> dict:
    """Get GeoIP database status (MMDB loaded, build date, staleness)."""
    from app.services.client_ip import get_geoip_status

    return get_geoip_status()


@router.post("/server/geoip-update")
async def geoip_update(source: str | None = None) -> dict:
    """Download a fresh GeoLite2 MMDB and reload.

    Optional query param `source`: "generic" (P3TERX) or "maxmind" (API key).
    """
    from app.services.client_ip import update_mmdb

    result = update_mmdb(source=source)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Update failed"))
    return result


@router.get("/server/health")
async def server_health() -> dict:
    """Health check for FastMCP server via HTTP probe."""
    host = settings.fastmcp_host
    port = settings.fastmcp_port
    url = f"http://{host}:{port}/health"

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "status": "running",
                    "message": f"FastMCP server running on {host}:{port}",
                    "server_name": data.get("server_name", ""),
                    "transport": data.get("transport", ""),
                    "tools_count": data.get("tools_count", 0),
                    "resources_count": data.get("resources_count", 0),
                    "prompts_count": data.get("prompts_count", 0),
                }
            return {
                "status": "error",
                "message": f"FastMCP returned status {resp.status_code}",
            }
    except httpx.ConnectError:
        return {
            "status": "stopped",
            "message": f"FastMCP server not reachable at {host}:{port}",
        }
    except Exception as e:
        # ERR-03: Log raw exception but don't expose stack trace to client
        logger.error("Server health check error: %s", e)
        return {
            "status": "error",
            "message": "Health check failed. Check server logs for details.",
        }


@router.get("/server/generated", response_model=list[GeneratedFile])
async def list_generated_files() -> list[dict]:
    """List all generated Python files with their content."""
    generated_dir = Path(settings.generated_dir)
    files = []

    if generated_dir.exists():
        for py_file in sorted(generated_dir.rglob("*.py")):
            content = py_file.read_text(encoding="utf-8")
            files.append({
                "path": str(py_file.relative_to(generated_dir)),
                "content": content,
                "size_bytes": len(content.encode("utf-8")),
            })

    return files


# ---------------------------------------------------------------------------
# SSL / TLS endpoints
# ---------------------------------------------------------------------------

@router.get("/server/ssl/status", response_model=SSLStatusResponse)
async def ssl_status(db: AsyncSession = Depends(get_db)) -> dict:
    """Get current SSL certificate status by reading cert from disk."""
    config = await _get_or_create_config(db)

    if not config.ssl_enabled or not config.ssl_ui_domain:
        return {"status": "none", "domains": []}

    from app.services.ssl_manager import SSLManager

    mgr = SSLManager()
    return mgr.get_cert_status(config.ssl_ui_domain)


@router.post("/server/ssl/issue", response_model=SSLIssueResponse)
async def ssl_issue(
    body: SSLIssueRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Issue or renew an SSL certificate via certbot."""
    config = await _get_or_create_config(db)

    if not config.ssl_enabled:
        raise HTTPException(400, "SSL is not enabled in server settings")
    if not config.ssl_ui_domain:
        raise HTTPException(400, "UI domain is required for SSL certificate")
    if not config.ssl_email:
        raise HTTPException(400, "ACME email is required for SSL certificate")

    # Collect domains
    domains = [config.ssl_ui_domain]
    if config.ssl_mcp_domain and config.ssl_mcp_domain != config.ssl_ui_domain:
        domains.append(config.ssl_mcp_domain)

    from app.services.ssl_manager import SSLManager

    mgr = SSLManager()
    force = body.force_renew if body else False

    try:
        mgr_result = await mgr.issue_certificate(
            domains=domains,
            email=config.ssl_email,
            challenge_type=config.ssl_challenge_type,
            dns_provider=config.ssl_dns_provider,
            dns_credentials_encrypted=config.ssl_dns_credentials,
            force_renew=force,
        )
    except Exception as e:
        # ERR-04: Log raw exception but don't expose details to client
        logger.error("SSL certificate issuance failed: %s", e)
        raise HTTPException(500, "Certificate issuance failed. Check server logs for details.")

    # Update cert status in DB
    fallback = "error" if not mgr_result["success"] else "issued"
    config.ssl_cert_status = mgr_result.get("cert_status", fallback)
    await db.commit()

    # Regenerate nginx config to pick up new certs
    if mgr_result["success"]:
        try:
            from app.services.nginx_generator import NginxGenerator

            nginx_gen = NginxGenerator()
            await nginx_gen.generate_and_write(db)
        except Exception as e:
            logger.warning("Failed to regenerate nginx config after cert issue: %s", e)

    return mgr_result


# ---------------------------------------------------------------------------
# Prompt Injection Guard endpoints
# ---------------------------------------------------------------------------

@router.get("/server/security/patterns", response_model=PatternsListResponse)
async def list_security_patterns(
    db: AsyncSession = Depends(get_db),
) -> PatternsListResponse:
    """List all built-in and custom injection patterns with enabled state."""

    from app.services.prompt_guard import _load_ml_model, _load_patterns

    config = await _get_or_create_config(db)
    disabled_ids = set(config.prompt_guard_disabled_patterns or [])

    # Built-in patterns
    compiled_patterns = _load_patterns()
    builtin: list[PromptGuardPatternResponse] = []
    for cp in compiled_patterns:
        builtin.append(PromptGuardPatternResponse(
            id=cp.pattern_id,
            category=cp.category,
            severity=cp.severity,
            lang=cp.lang,
            pattern=cp.regex.pattern,
            description=cp.description,
            source="builtin",
            enabled=cp.pattern_id not in disabled_ids,
        ))

    # Custom patterns
    custom: list[PromptGuardPatternResponse] = []
    for p in (config.prompt_guard_custom_patterns or []):
        custom.append(PromptGuardPatternResponse(
            id=p.get("id", ""),
            category=p.get("category", "custom"),
            severity=p.get("severity", "MEDIUM"),
            lang=p.get("lang", "*"),
            pattern=p.get("pattern", ""),
            description=p.get("description", ""),
            source="custom",
            enabled=p.get("enabled", True),
        ))

    # Check ML availability
    session, _ = _load_ml_model()

    return PatternsListResponse(
        builtin=builtin,
        custom=custom,
        total=len(builtin) + len(custom),
        l1_available=session is not None,
    )


@router.post("/server/security/validate-pattern", response_model=ValidatePatternResponse)
async def validate_pattern(body: ValidatePatternRequest) -> ValidatePatternResponse:
    """Validate a regex pattern and optionally test against sample text."""
    import re as _re

    try:
        compiled = _re.compile(body.pattern, _re.UNICODE)
    except _re.error as exc:
        return ValidatePatternResponse(valid=False, error=str(exc))

    matches: list[str] = []
    if body.test_text:
        for m in compiled.finditer(body.test_text):
            text = m.group(0)
            if len(text) > 200:
                text = text[:200] + "..."
            matches.append(text)
            if len(matches) >= 20:
                break

    return ValidatePatternResponse(valid=True, matches=matches)


@router.post("/server/security/scan-all", response_model=BulkScanResponse)
async def scan_all_entities(
    db: AsyncSession = Depends(get_db),
) -> BulkScanResponse:
    """Scan all tools, prompts, and resources for prompt injection."""
    import time

    from sqlalchemy.orm import selectinload

    from app.models.prompt import Prompt
    from app.models.resource import Resource
    from app.models.tool import Tool
    from app.services.prompt_guard import _load_ml_model, scan_entity

    start = time.monotonic()
    results: list[EntityScanResult] = []
    total_scanned = 0

    # Scan tools (eagerly load parameters to avoid async lazy-load error)
    tools_result = await db.execute(
        select(Tool).options(selectinload(Tool.parameters))
    )
    for tool in tools_result.scalars().all():
        total_scanned += 1
        data = {
            "name": tool.name,
            "description": tool.description,
            "sql_query": tool.sql_query,
        }
        if hasattr(tool, "transform_template") and tool.transform_template:
            data["transform_template"] = tool.transform_template
        if tool.parameters:
            data["parameters"] = [
                {"name": p.name, "description": p.description}
                for p in tool.parameters
            ]

        scan = scan_entity("tool", data)
        if not scan.is_clean:
            results.append(EntityScanResult(
                entity_type="tool",
                entity_id=tool.id,
                entity_name=tool.name,
                is_clean=False,
                detections=[
                    ScanDetection(
                        field=d.field,
                        category=d.category,
                        severity=d.severity,
                        pattern_id=d.pattern_id,
                        matched_text=d.matched_text[:100],
                        layer=d.layer,
                        confidence=d.confidence,
                    )
                    for d in scan.detections
                ],
                max_severity=scan.max_severity,
            ))

    # Scan prompts
    prompts_result = await db.execute(select(Prompt))
    for prompt in prompts_result.scalars().all():
        total_scanned += 1
        data = {
            "name": prompt.name,
            "description": prompt.description,
            "template": getattr(prompt, "template", None),
        }
        scan = scan_entity("prompt", data)
        if not scan.is_clean:
            results.append(EntityScanResult(
                entity_type="prompt",
                entity_id=prompt.id,
                entity_name=prompt.name,
                is_clean=False,
                detections=[
                    ScanDetection(
                        field=d.field,
                        category=d.category,
                        severity=d.severity,
                        pattern_id=d.pattern_id,
                        matched_text=d.matched_text[:100],
                        layer=d.layer,
                        confidence=d.confidence,
                    )
                    for d in scan.detections
                ],
                max_severity=scan.max_severity,
            ))

    # Scan resources
    resources_result = await db.execute(select(Resource))
    for resource in resources_result.scalars().all():
        total_scanned += 1
        data = {
            "name": resource.name,
            "description": resource.description,
            "sql_query": getattr(resource, "sql_query", None),
            "static_content": getattr(resource, "static_content", None),
        }
        scan = scan_entity("resource", data)
        if not scan.is_clean:
            results.append(EntityScanResult(
                entity_type="resource",
                entity_id=resource.id,
                entity_name=resource.name,
                is_clean=False,
                detections=[
                    ScanDetection(
                        field=d.field,
                        category=d.category,
                        severity=d.severity,
                        pattern_id=d.pattern_id,
                        matched_text=d.matched_text[:100],
                        layer=d.layer,
                        confidence=d.confidence,
                    )
                    for d in scan.detections
                ],
                max_severity=scan.max_severity,
            ))

    elapsed_ms = (time.monotonic() - start) * 1000
    session, _ = _load_ml_model()

    return BulkScanResponse(
        total_scanned=total_scanned,
        total_flagged=len(results),
        results=results,
        scan_time_ms=round(elapsed_ms, 2),
        l1_available=session is not None,
    )
