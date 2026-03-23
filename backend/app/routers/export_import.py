"""Export / Import router — full project snapshot as JSON."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.connection import Connection
from app.models.parameter import Parameter
from app.models.prompt import Prompt
from app.models.resource import Resource
from app.models.server_config import ServerConfig
from app.models.tool import Tool
from app.schemas.connection import _validate_host_ssrf
from app.schemas.constants import ENTITY_NAME_PATTERN as _ENP
from app.services.crypto import encrypt, encrypt_sensitive_extra, mask_sensitive_extra
from app.services.history import record_change
from app.services.prompt_guard import is_enabled as guard_enabled
from app.services.prompt_guard import scan_entity

router = APIRouter(prefix="/export-import")

_IMPORT_MAX_BYTES = 10_000_000  # 10 MB

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ExportParameter(BaseModel):
    name: str
    param_type: str
    description: str
    is_required: bool
    default_value: str | None = None
    enum_values: list[str] | None = None
    sort_order: int = 0


class ExportConnection(BaseModel):
    name: str = Field(..., pattern=_ENP)
    db_type: str
    host: str
    port: int
    database: str
    username: str
    password: str | None = None  # optional on import
    ssl_mode: str = "prefer"
    pool_min_size: int = 2
    pool_max_size: int = 10
    extra_params: dict | None = None
    is_active: bool = True
    max_concurrent_queries: int = 5
    queue_timeout_seconds: int = 30
    queue_enabled: bool = True


class ExportTool(BaseModel):
    name: str = Field(..., pattern=_ENP)
    description: str
    sql_query: str
    return_type: str = "list[dict]"
    tags: list[str] = Field(default_factory=list)
    version: int = 1
    annotations: dict = Field(default_factory=dict)
    cache_ttl: int = 0
    is_enabled: bool = True
    connection_name: str  # resolved by name (not ID)
    parameters: list[ExportParameter] = Field(default_factory=list)
    transform_template: str | None = None


class ExportResource(BaseModel):
    name: str = Field(..., pattern=_ENP)
    uri_template: str
    description: str
    sql_query: str | None = None
    static_content: str | None = None
    mime_type: str = "application/json"
    tags: list[str] = Field(default_factory=list)
    version: int = 1
    is_enabled: bool = True
    connection_name: str | None = None  # resolved by name


class ExportPrompt(BaseModel):
    name: str = Field(..., pattern=_ENP)
    title: str | None = None
    description: str
    template: str
    arguments: list[dict] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    version: int = 1
    is_enabled: bool = True


class ExportServerConfig(BaseModel):
    server_name: str
    transport: str
    host: str
    port: int
    auth_type: str = "none"
    cors_origins: list[str] = Field(default_factory=list)
    log_level: str = "INFO"
    global_variables: dict = Field(default_factory=dict)


class ExportPayload(BaseModel):
    version: Literal["1.0", "1.1", "1.2"] = "1.0"
    exported_at: datetime
    server_config: ExportServerConfig | None = None
    connections: list[ExportConnection] = Field(default_factory=list)
    tools: list[ExportTool] = Field(default_factory=list)
    resources: list[ExportResource] = Field(default_factory=list)
    prompts: list[ExportPrompt] = Field(default_factory=list)


class ImportResult(BaseModel):
    success: bool
    message: str
    created: dict = Field(default_factory=dict)
    skipped: dict = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


@router.get("/export", response_model=ExportPayload)
async def export_all(db: AsyncSession = Depends(get_db)) -> ExportPayload:
    """Export all entities as a portable JSON snapshot."""

    # Server config
    result = await db.execute(select(ServerConfig))
    config = result.scalar_one_or_none()
    server_config = None
    if config:
        server_config = ExportServerConfig(
            server_name=config.server_name,
            transport=config.transport,
            host=config.host,
            port=config.port,
            auth_type=config.auth_type,
            cors_origins=config.cors_origins or [],
            log_level=config.log_level,
            global_variables=config.global_variables or {},
        )

    # Connections (password NOT exported)
    result = await db.execute(select(Connection))
    connections_db = result.scalars().all()
    conn_id_to_name: dict[str, str] = {}
    connections: list[ExportConnection] = []
    for c in connections_db:
        conn_id_to_name[c.id] = c.name
        connections.append(
            ExportConnection(
                name=c.name,
                db_type=c.db_type,
                host=c.host,
                port=c.port,
                database=c.database,
                username=c.username,
                password=None,  # never export actual passwords
                ssl_mode=c.ssl_mode,
                pool_min_size=c.pool_min_size,
                pool_max_size=c.pool_max_size,
                extra_params=mask_sensitive_extra(c.extra_params),
                is_active=c.is_active,
                max_concurrent_queries=c.max_concurrent_queries,
                queue_timeout_seconds=c.queue_timeout_seconds,
                queue_enabled=c.queue_enabled,
            )
        )

    # Tools + parameters
    result = await db.execute(
        select(Tool).options(selectinload(Tool.parameters))
    )
    tools_db = result.scalars().all()
    tools: list[ExportTool] = []
    for t in tools_db:
        tools.append(
            ExportTool(
                name=t.name,
                description=t.description,
                sql_query=t.sql_query,
                return_type=t.return_type,
                tags=t.tags or [],
                version=t.version,
                annotations=t.annotations or {},
                cache_ttl=t.cache_ttl,
                is_enabled=t.is_enabled,
                connection_name=conn_id_to_name.get(t.connection_id, "unknown"),
                transform_template=t.transform_template,
                parameters=[
                    ExportParameter(
                        name=p.name,
                        param_type=p.param_type,
                        description=p.description,
                        is_required=p.is_required,
                        default_value=p.default_value,
                        enum_values=p.enum_values,
                        sort_order=p.sort_order,
                    )
                    for p in sorted(t.parameters, key=lambda x: x.sort_order)
                ],
            )
        )

    # Resources
    result = await db.execute(select(Resource))
    resources_db = result.scalars().all()
    resources: list[ExportResource] = []
    for r in resources_db:
        resources.append(
            ExportResource(
                name=r.name,
                uri_template=r.uri_template,
                description=r.description,
                sql_query=r.sql_query,
                static_content=r.static_content,
                mime_type=r.mime_type,
                tags=r.tags or [],
                version=r.version,
                is_enabled=r.is_enabled,
                connection_name=(
                    conn_id_to_name.get(r.connection_id, "") if r.connection_id else None
                ),
            )
        )

    # Prompts
    result = await db.execute(select(Prompt))
    prompts_db = result.scalars().all()
    prompts: list[ExportPrompt] = []
    for pr in prompts_db:
        prompts.append(
            ExportPrompt(
                name=pr.name,
                title=pr.title,
                description=pr.description,
                template=pr.template,
                arguments=pr.arguments or [],
                tags=pr.tags or [],
                version=pr.version,
                is_enabled=pr.is_enabled,
            )
        )

    return ExportPayload(
        version="1.2",
        exported_at=datetime.now(UTC),
        server_config=server_config,
        connections=connections,
        tools=tools,
        resources=resources,
        prompts=prompts,
    )


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


@router.post("/import", response_model=ImportResult)
async def import_all(
    payload: ExportPayload,
    request: Request,
    dry_run: bool = False,
    db: AsyncSession = Depends(get_db),
) -> ImportResult:
    """Import entities from a JSON snapshot. Skips duplicates by name.

    Pass ``?dry_run=true`` to preview changes without applying them.
    """
    # H-12: Validate import format version
    supported_versions = {"1.0", "1.1", "1.2"}
    if payload.version not in supported_versions:
        raise HTTPException(
            400,
            f"Unsupported import version '{payload.version}'. "
            f"Supported: {', '.join(sorted(supported_versions))}",
        )

    # A4-04: Enforce import size limit
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > _IMPORT_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Import payload too large (max {_IMPORT_MAX_BYTES // 1_000_000}MB)",
        )

    created: dict[str, int] = {
        "connections": 0, "tools": 0, "resources": 0, "prompts": 0,
    }
    skipped: dict[str, list[str]] = {
        "connections": [], "tools": [], "resources": [], "prompts": [],
    }
    errors: list[str] = []

    # -------------------------------------------------------------------
    # 0. Pre-import prompt injection scan
    # -------------------------------------------------------------------
    if guard_enabled():
        blocked: list[str] = []
        for tool_data in payload.tools:
            scan = scan_entity("tool", tool_data.model_dump())
            if scan.max_severity in ("HIGH", "CRITICAL"):
                blocked.append(f"Tool '{tool_data.name}': {scan.detections[0].category}")
        for res_data in payload.resources:
            scan = scan_entity("resource", res_data.model_dump())
            if scan.max_severity in ("HIGH", "CRITICAL"):
                blocked.append(f"Resource '{res_data.name}': {scan.detections[0].category}")
        for prompt_data in payload.prompts:
            scan = scan_entity("prompt", prompt_data.model_dump())
            if scan.max_severity in ("HIGH", "CRITICAL"):
                blocked.append(f"Prompt '{prompt_data.name}': {scan.detections[0].category}")
        if payload.server_config and payload.server_config.global_variables:
            scan = scan_entity(
                "server_config",
                {"global_variables": payload.server_config.global_variables},
            )
            if scan.max_severity in ("HIGH", "CRITICAL"):
                blocked.append(f"Server config global_variables: {scan.detections[0].category}")

        if blocked:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Import blocked: prompt injection detected",
                    "blocked_entities": blocked,
                },
            )

    # -------------------------------------------------------------------
    # 1. Connections
    # -------------------------------------------------------------------
    result = await db.execute(select(Connection))
    existing_conns = {c.name: c for c in result.scalars().all()}
    conn_name_to_id: dict[str, str] = {c.name: c.id for c in existing_conns.values()}

    for conn_data in payload.connections:
        if conn_data.name in existing_conns:
            skipped["connections"].append(conn_data.name)
            continue

        # SSRF validation — block private/internal hosts on import
        if conn_data.host:
            try:
                _validate_host_ssrf(conn_data.host)
            except ValueError as exc:
                errors.append(
                    f"Connection '{conn_data.name}': SSRF blocked — {exc}"
                )
                continue

        new_id = str(uuid.uuid4())
        # Import without password — user must set passwords manually
        new_conn = Connection(
            id=new_id,
            name=conn_data.name,
            db_type=conn_data.db_type,
            host=conn_data.host,
            port=conn_data.port,
            database=conn_data.database,
            username=conn_data.username,
            password_encrypted=encrypt(conn_data.password) if conn_data.password else encrypt(""),
            ssl_mode=conn_data.ssl_mode,
            pool_min_size=conn_data.pool_min_size,
            pool_max_size=conn_data.pool_max_size,
            extra_params=encrypt_sensitive_extra(conn_data.extra_params),
            is_active=conn_data.is_active,
            max_concurrent_queries=conn_data.max_concurrent_queries,
            queue_timeout_seconds=conn_data.queue_timeout_seconds,
            queue_enabled=conn_data.queue_enabled,
        )
        db.add(new_conn)
        conn_name_to_id[conn_data.name] = new_id
        created["connections"] += 1

    await db.flush()

    # -------------------------------------------------------------------
    # 2. Tools
    # -------------------------------------------------------------------
    result = await db.execute(select(Tool))
    existing_tools = {t.name for t in result.scalars().all()}

    for tool_data in payload.tools:
        if tool_data.name in existing_tools:
            skipped["tools"].append(tool_data.name)
            continue

        connection_id = conn_name_to_id.get(tool_data.connection_name)
        if not connection_id:
            errors.append(
                f"Tool '{tool_data.name}': connection"
                f" '{tool_data.connection_name}' not found"
            )
            continue

        tool_id = str(uuid.uuid4())
        new_tool = Tool(
            id=tool_id,
            connection_id=connection_id,
            name=tool_data.name,
            description=tool_data.description,
            sql_query=tool_data.sql_query,
            return_type=tool_data.return_type,
            tags=tool_data.tags,
            version=1,
            annotations=tool_data.annotations,
            cache_ttl=tool_data.cache_ttl,
            is_enabled=tool_data.is_enabled,
            transform_template=tool_data.transform_template,
        )
        db.add(new_tool)

        for p in tool_data.parameters:
            db.add(
                Parameter(
                    id=str(uuid.uuid4()),
                    tool_id=tool_id,
                    name=p.name,
                    param_type=p.param_type,
                    description=p.description,
                    is_required=p.is_required,
                    default_value=p.default_value,
                    enum_values=p.enum_values,
                    sort_order=p.sort_order,
                )
            )

        created["tools"] += 1

    # -------------------------------------------------------------------
    # 3. Resources
    # -------------------------------------------------------------------
    result = await db.execute(select(Resource))
    existing_resources = {r.name for r in result.scalars().all()}

    for res_data in payload.resources:
        if res_data.name in existing_resources:
            skipped["resources"].append(res_data.name)
            continue

        connection_id = None
        if res_data.connection_name:
            connection_id = conn_name_to_id.get(res_data.connection_name)
            if not connection_id:
                errors.append(
                    f"Resource '{res_data.name}': connection"
                    f" '{res_data.connection_name}' not found"
                )
                continue

        db.add(
            Resource(
                id=str(uuid.uuid4()),
                connection_id=connection_id,
                name=res_data.name,
                uri_template=res_data.uri_template,
                description=res_data.description,
                sql_query=res_data.sql_query,
                static_content=res_data.static_content,
                mime_type=res_data.mime_type,
                tags=res_data.tags,
                version=1,
                is_enabled=res_data.is_enabled,
            )
        )
        created["resources"] += 1

    # -------------------------------------------------------------------
    # 4. Prompts
    # -------------------------------------------------------------------
    result = await db.execute(select(Prompt))
    existing_prompts = {p.name for p in result.scalars().all()}

    for prompt_data in payload.prompts:
        if prompt_data.name in existing_prompts:
            skipped["prompts"].append(prompt_data.name)
            continue

        db.add(
            Prompt(
                id=str(uuid.uuid4()),
                name=prompt_data.name,
                title=prompt_data.title,
                description=prompt_data.description,
                template=prompt_data.template,
                arguments=prompt_data.arguments,
                tags=prompt_data.tags,
                version=1,
                is_enabled=prompt_data.is_enabled,
            )
        )
        created["prompts"] += 1

    # -------------------------------------------------------------------
    # 5. Server config (update only, never create)
    # -------------------------------------------------------------------
    if payload.server_config:
        result = await db.execute(select(ServerConfig))
        existing_config = result.scalar_one_or_none()
        if existing_config:
            cfg = payload.server_config
            existing_config.server_name = cfg.server_name
            existing_config.transport = cfg.transport
            existing_config.host = cfg.host
            existing_config.port = cfg.port
            existing_config.auth_type = cfg.auth_type
            existing_config.cors_origins = cfg.cors_origins
            existing_config.log_level = cfg.log_level
            if cfg.global_variables:
                existing_config.global_variables = cfg.global_variables

    total_created = sum(created.values())
    total_skipped = sum(len(v) for v in skipped.values())

    if dry_run:
        # A4-05: Rollback all changes in dry-run mode
        await db.rollback()
        return ImportResult(
            success=True,
            message=(
                f"Dry run: would import {total_created} entities"
                f" ({total_skipped} skipped, {len(errors)} errors)"
            ),
            created=created,
            skipped={k: v for k, v in skipped.items() if v},
            errors=errors,
        )

    # Record batch history entries (one per entity type with creations)
    _type_map = {
        "connections": "connection",
        "tools": "tool",
        "resources": "resource",
        "prompts": "prompt",
    }
    for plural, count in created.items():
        if count > 0:
            entity_type = _type_map[plural]
            await record_change(
                db,
                entity_type=entity_type,
                entity_id="import",
                entity_name=f"Imported {count} {plural}",
                action="import",
                snapshot={"count": count, "skipped": len(skipped.get(plural, []))},
            )

    await db.commit()

    return ImportResult(
        success=True,
        message=(
            f"Imported {total_created} entities"
            f" ({total_skipped} skipped, {len(errors)} errors)"
        ),
        created=created,
        skipped={k: v for k, v in skipped.items() if v},
        errors=errors,
    )
