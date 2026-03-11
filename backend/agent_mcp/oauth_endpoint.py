"""OAuth2 token and introspection endpoints for the agent MCP server.

These are custom HTTP endpoints served alongside the MCP protocol,
handling client_credentials and refresh_token grants.
"""

import logging

from starlette.requests import Request
from starlette.responses import JSONResponse

from agent_mcp import backend_client
from agent_mcp.middleware import _get_client_country, _get_client_ip

logger = logging.getLogger(__name__)


async def handle_token_request(request: Request) -> JSONResponse:
    """Handle POST /oauth/token — client_credentials or refresh_token grant."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    grant_type = body.get("grant_type")
    # Use real client IP and country (CF-Connecting-IP / X-Forwarded-For aware)
    client_ip = _get_client_ip(request)
    client_country = _get_client_country(request) or None

    if grant_type == "client_credentials":
        client_id = body.get("client_id")
        client_secret = body.get("client_secret")
        if not client_id or not client_secret:
            return JSONResponse(
                {"error": "invalid_request", "error_description": "client_id and client_secret required"},
                status_code=400,
            )

        # Delegate to backend (which has DB access)
        try:
            result = await backend_client.post("/agent-auth/token", data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "client_ip": client_ip,
                "client_country": client_country,
            })
        except backend_client.BackendError as e:
            if e.status_code == 401:
                return JSONResponse({"error": "invalid_client"}, status_code=401)
            return JSONResponse({"error": "server_error", "error_description": e.detail}, status_code=500)

        if result is None:
            return JSONResponse({"error": "invalid_client"}, status_code=401)

        return JSONResponse(result)

    elif grant_type == "refresh_token":
        refresh_token = body.get("refresh_token")
        if not refresh_token:
            return JSONResponse(
                {"error": "invalid_request", "error_description": "refresh_token required"},
                status_code=400,
            )

        try:
            result = await backend_client.post("/agent-auth/token", data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_ip": client_ip,
                "client_country": client_country,
            })
        except backend_client.BackendError as e:
            if e.status_code == 401:
                return JSONResponse({"error": "invalid_grant"}, status_code=401)
            return JSONResponse({"error": "server_error"}, status_code=500)

        if result is None:
            return JSONResponse({"error": "invalid_grant"}, status_code=401)

        return JSONResponse(result)

    else:
        return JSONResponse(
            {"error": "unsupported_grant_type"},
            status_code=400,
        )


async def handle_introspect_request(request: Request) -> JSONResponse:
    """Handle POST /oauth/introspect — RFC 7662 token introspection.

    Protected by AuthMiddleware (requires valid bearer token per RFC 7662).
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    token = body.get("token")
    if not token:
        return JSONResponse({"active": False})

    try:
        result = await backend_client.post("/agent-auth/introspect", data={"token": token})
    except backend_client.BackendError:
        return JSONResponse({"active": False})

    return JSONResponse(result or {"active": False})
