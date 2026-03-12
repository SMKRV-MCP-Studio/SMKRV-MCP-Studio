"""Agent MCP server — FastMCP instance with auth, tools, and OAuth endpoints."""

import logging
from pathlib import Path

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from agent_mcp import auth, config
from agent_mcp.middleware import AuthMiddleware, get_auth_context
from agent_mcp.oauth_endpoint import handle_introspect_request, handle_token_request
from agent_mcp.output_guard import DATA_TOOLS, scan_output
from agent_mcp.tools import (
    connections,
    deploy,
    export_import,
    flow,
    history,
    monitoring,
    prompts,
    resources,
    server_config,
    tools,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Read version from VERSION file (same pattern as backend)
# ---------------------------------------------------------------------------
_VERSION = "0.0.0"
try:
    for p in (Path("VERSION"), Path("/app/VERSION"), Path(__file__).parent.parent / "VERSION"):
        if p.exists():
            _VERSION = p.read_text().strip()
            break
except Exception:
    pass

# ---------------------------------------------------------------------------
# Create FastMCP instance
# ---------------------------------------------------------------------------
mcp = FastMCP(name="SMKRV MCP Studio Agent")


# ---------------------------------------------------------------------------
# Auth wrapper — validate auth context on every MCP tool call
# ---------------------------------------------------------------------------
_TOOL_FUNCTIONS: dict[str, object] = {}


def _register_tool(fn, *, name: str | None = None, description: str | None = None):
    """Register an async tool function with auth wrapping.

    FastMCP 3.x add_tool() accepts only a callable (or Tool).
    We decorate the wrapper with @tool() to set name/description,
    then pass it to add_tool().
    """
    import copy
    import inspect

    from fastmcp.tools import tool as tool_decorator

    tool_name = name or fn.__name__
    tool_desc = description or fn.__doc__ or ""

    async def _authed_wrapper(**tool_kwargs):
        # Auth context is set by AuthMiddleware at the HTTP transport level
        auth_info = get_auth_context()
        if auth_info is None:
            raise RuntimeError(
                "Authentication required. Provide a valid bearer token "
                "in the Authorization header."
            )

        # Record activity
        success = True
        try:
            result = await fn(**tool_kwargs)
            # OWASP LLM01: scan untrusted DB content for injection patterns
            if config.OUTPUT_SCANNING and isinstance(result, dict) and tool_name in DATA_TOOLS:
                result = scan_output(result)
            return result
        except Exception:
            success = False
            raise
        finally:
            try:
                await auth.record_activity(
                    token_prefix=auth_info.get("token_prefix", ""),
                    tool_name=tool_name,
                    ip=auth_info.get("client_ip", ""),
                    success=success,
                )
            except Exception:
                pass

    # Copy metadata for FastMCP parameter introspection
    _authed_wrapper.__name__ = tool_name
    _authed_wrapper.__doc__ = fn.__doc__
    _authed_wrapper.__module__ = fn.__module__
    _authed_wrapper.__qualname__ = fn.__qualname__
    _authed_wrapper.__annotations__ = copy.copy(fn.__annotations__)
    _authed_wrapper.__signature__ = inspect.signature(fn)

    # Decorate with @tool to set name/description for FastMCP 3.x
    decorated = tool_decorator(name=tool_name, description=tool_desc)(_authed_wrapper)
    mcp.add_tool(decorated)
    _TOOL_FUNCTIONS[tool_name] = fn


# ---------------------------------------------------------------------------
# Register all tools
# ---------------------------------------------------------------------------

# --- Total: 45 tools ---

# Connections (8 tools)
_register_tool(connections.list_connections, name="list_connections")
_register_tool(connections.get_connection, name="get_connection")
_register_tool(connections.create_connection, name="create_connection")
_register_tool(connections.update_connection, name="update_connection")
_register_tool(connections.delete_connection, name="delete_connection")
_register_tool(connections.test_connection, name="test_connection")
_register_tool(connections.list_tables, name="list_tables")
_register_tool(connections.list_columns, name="list_columns")

# Tools (8 tools)
_register_tool(tools.list_tools, name="list_tools")
_register_tool(tools.get_tool, name="get_tool")
_register_tool(tools.create_tool, name="create_tool")
_register_tool(tools.create_tools_batch, name="create_tools_batch")
_register_tool(tools.update_tool, name="update_tool")
_register_tool(tools.delete_tool, name="delete_tool")
_register_tool(tools.duplicate_tool, name="duplicate_tool")
_register_tool(tools.preview_sql, name="preview_sql")

# Resources (5 tools)
_register_tool(resources.list_resources, name="list_resources")
_register_tool(resources.get_resource, name="get_resource")
_register_tool(resources.create_resource, name="create_resource")
_register_tool(resources.update_resource, name="update_resource")
_register_tool(resources.delete_resource, name="delete_resource")

# Prompts (5 tools)
_register_tool(prompts.list_prompts, name="list_prompts")
_register_tool(prompts.get_prompt, name="get_prompt")
_register_tool(prompts.create_prompt, name="create_prompt")
_register_tool(prompts.update_prompt, name="update_prompt")
_register_tool(prompts.delete_prompt, name="delete_prompt")

# Deploy (3 tools)
_register_tool(deploy.deploy_server, name="deploy_server")
_register_tool(deploy.stop_server, name="stop_server")
_register_tool(deploy.get_deploy_status, name="get_deploy_status")

# Export/Import (2 tools)
_register_tool(export_import.export_config, name="export_config")
_register_tool(export_import.import_config, name="import_config")

# History (3 tools)
_register_tool(history.list_history, name="list_history")
_register_tool(history.get_entity_history, name="get_entity_history")
_register_tool(history.rollback, name="rollback")

# Monitoring (3 tools)
_register_tool(monitoring.get_metrics_stats, name="get_metrics_stats")
_register_tool(monitoring.get_metrics_timeseries, name="get_metrics_timeseries")
_register_tool(monitoring.get_queue_metrics, name="get_queue_metrics")

# Server Config & Global Variables (7 tools)
_register_tool(server_config.get_server_config, name="get_server_config")
_register_tool(server_config.update_server_config, name="update_server_config")
_register_tool(server_config.get_global_variables, name="get_global_variables")
_register_tool(server_config.set_global_variables, name="set_global_variables")
_register_tool(server_config.update_global_variables, name="update_global_variables")
_register_tool(server_config.get_server_health, name="get_server_health")
_register_tool(server_config.get_generated_code, name="get_generated_code")

# Flow (1 tool)
_register_tool(flow.get_flow_layout, name="get_flow_layout")


# ---------------------------------------------------------------------------
# Custom HTTP endpoints
# ---------------------------------------------------------------------------

@mcp.custom_route("/", methods=["GET", "HEAD", "POST"])
async def root_info(request: Request) -> JSONResponse:
    """Root route — redirect hint to MCP endpoint."""
    is_post = request.method == "POST"
    return JSONResponse(
        {
            **({"error": "MCP endpoint is at /mcp"} if is_post else {"message": "MCP endpoint is at /mcp"}),
            "mcp_endpoint": "/mcp",
            "health": "/health",
        },
        status_code=404 if is_post else 200,
    )


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> JSONResponse:
    """Health check for Docker healthcheck and monitoring."""
    return JSONResponse({
        "status": "ok",
        "service": "agent-mcp",
        "version": _VERSION,
        "mcp_endpoint": "/agent-mcp/mcp",
    })


@mcp.custom_route("/oauth/token", methods=["POST"])
async def oauth_token(request: Request) -> JSONResponse:
    """OAuth2 token endpoint (client_credentials / refresh_token grants)."""
    return await handle_token_request(request)


@mcp.custom_route("/oauth/introspect", methods=["POST"])
async def oauth_introspect(request: Request) -> JSONResponse:
    """RFC 7662 token introspection endpoint (requires valid bearer token)."""
    return await handle_introspect_request(request)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    """Start the Agent MCP server with auth middleware."""
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    # Validate critical config before starting
    config.validate_startup()

    logger.info(
        "Starting Agent MCP server v%s on %s:%d",
        _VERSION, config.SERVER_HOST, config.SERVER_PORT,
    )
    logger.info("Backend URL: %s", config.BACKEND_URL)
    logger.info("Tools registered: %d", len(_TOOL_FUNCTIONS))
    logger.info("Auth middleware: ENABLED")

    # Get the ASGI app from FastMCP and wrap with auth middleware
    try:
        inner_app = mcp.http_app(path="/mcp")
    except (AttributeError, TypeError):
        # Fallback for FastMCP versions without http_app()
        logger.warning("mcp.http_app() not available, falling back to mcp.run()")
        mcp.run(
            transport="streamable-http",
            host=config.SERVER_HOST,
            port=config.SERVER_PORT,
        )
        return

    app = AuthMiddleware(inner_app)

    import uvicorn
    uvicorn.run(
        app,
        host=config.SERVER_HOST,
        port=config.SERVER_PORT,
        log_level=config.LOG_LEVEL.lower(),
    )
