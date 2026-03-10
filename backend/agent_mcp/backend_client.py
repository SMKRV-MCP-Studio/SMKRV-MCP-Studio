"""HTTP client wrapper for calling the Studio backend API."""

import logging
import time

import httpx

from agent_mcp import config

logger = logging.getLogger(__name__)

_UNSET = object()  # sentinel: distinguish "not provided" from explicit None

_client: httpx.AsyncClient | None = None

# Cached fields allowlist (refreshed every 60s)
_fields_allowlist: set[str] | None = None
_fields_allowlist_ts: float = 0.0
_FIELDS_CACHE_TTL = 60.0


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            base_url=config.BACKEND_URL,
            timeout=30.0,
            headers={
                "X-Agent-Service-Token": config.AGENT_SERVICE_TOKEN,
                "Content-Type": "application/json",
            },
        )
    return _client


class BackendError(Exception):
    """Raised when the backend returns an error."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Backend error {status_code}: {detail}")


async def _handle_response(resp: httpx.Response) -> dict | list | None:
    """Handle backend response, raising BackendError on non-2xx."""
    if resp.status_code >= 400:
        try:
            body = resp.json()
            detail = body.get("detail", resp.text)
        except Exception:
            detail = resp.text
        raise BackendError(resp.status_code, detail)
    if resp.status_code == 204:
        return None
    return resp.json()


async def get(path: str, params: dict | None = None) -> dict | list:
    """GET request to backend API."""
    client = _get_client()
    resp = await client.get(f"/api/v1{path}", params=params)
    result = await _handle_response(resp)
    return result  # type: ignore[return-value]


async def post(
    path: str, data: dict | None = None, timeout: float | object = _UNSET,
) -> dict | None:
    """POST request to backend API.

    Args:
        timeout: Per-request timeout in seconds. Omit to use client default (30s).
    """
    client = _get_client()
    kwargs: dict = {"json": data}
    if timeout is not _UNSET:
        kwargs["timeout"] = timeout
    resp = await client.post(f"/api/v1{path}", **kwargs)
    return await _handle_response(resp)  # type: ignore[return-value]


async def patch(path: str, data: dict | None = None) -> dict | None:
    """PATCH request to backend API."""
    client = _get_client()
    resp = await client.patch(f"/api/v1{path}", json=data)
    return await _handle_response(resp)  # type: ignore[return-value]


async def delete(path: str) -> None:
    """DELETE request to backend API."""
    client = _get_client()
    resp = await client.delete(f"/api/v1{path}")
    await _handle_response(resp)


async def get_fields_allowlist() -> set[str]:
    """Return the cached fields allowlist from server config.

    Empty set means no restriction. Non-empty restricts which fields
    agents can request via the ``fields`` parameter on list_tools.
    Cached for 60s to avoid per-call overhead.
    """
    global _fields_allowlist, _fields_allowlist_ts
    now = time.monotonic()
    if _fields_allowlist is not None and (now - _fields_allowlist_ts) < _FIELDS_CACHE_TTL:
        return _fields_allowlist
    try:
        cfg = await get("/server/config")
        raw = cfg.get("agent_mcp_fields_allowlist") or []  # type: ignore[union-attr]
        _fields_allowlist = set(raw)
    except Exception:
        _fields_allowlist = set()
        logger.debug("Failed to fetch fields allowlist", exc_info=True)
    _fields_allowlist_ts = now
    return _fields_allowlist


async def close() -> None:
    """Close the HTTP client."""
    global _client
    if _client:
        await _client.aclose()
        _client = None
