"""Budget/usage self-service MCP server (streamable HTTP, MCP SDK 2.0).

Serve with: ``uvicorn app.main:app --host 0.0.0.0 --port 8000`` (MCP endpoint at
``/mcp``, health at ``/health``).

The caller authenticates by sending their ``sk-<jwt>`` LiteLLM key as an HTTP
bearer token (``Authorization: Bearer sk-...``). We decode it locally to the
caller's ``user_id`` and answer only for that user.
"""

import logging

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.config import settings
from app.db import async_session_factory, litellm_session_factory
from app.identity import Identity, identity_from_key
from app.tools import NotVisibleError, list_my_teams_logic, team_budget_usage_logic

logger = logging.getLogger(__name__)

mcp = MCPServer("budget-usage")


def _bearer_from_context(ctx: Context) -> str:
    """Raw bearer token from the request headers (Context.headers, MCP SDK 2.0)."""
    headers = getattr(ctx, "headers", None)
    try:
        auth = headers.get("authorization", "") if headers is not None else ""
    except Exception:  # noqa: BLE001 — never let a header-shape change 500 the tool
        auth = ""
    auth = auth or ""
    if auth.lower().startswith("bearer "):
        return auth[len("bearer "):].strip()
    return ""


def _authenticate(ctx: Context) -> Identity:
    token = _bearer_from_context(ctx)
    if not token:
        raise ValueError("unauthenticated: send your sk- key as 'Authorization: Bearer sk-...'")
    try:
        return identity_from_key(token, settings.key_jwt_secret)
    except ValueError as e:
        raise ValueError(f"unauthenticated: {e}") from e


@mcp.tool()
async def list_my_teams(ctx: Context) -> dict:
    """List the teams you belong to and are allowed to see.

    Returns {"teams": [{"team_id", "team_alias"}]}. Pass a team_id to
    get_team_budget_usage.
    """
    ident = _authenticate(ctx)
    async with async_session_factory() as portal, litellm_session_factory() as litellm:
        return await list_my_teams_logic(portal, litellm, ident.user_id)


@mcp.tool()
async def get_team_budget_usage(team_id: str, ctx: Context) -> dict:
    """Your OWN budget and spend within `team_id`.

    Returns max_budget, spend, remaining, budget_duration, reset_at (some may be
    null when unlimited / no reset). Errors if you are not a member of that team.
    """
    ident = _authenticate(ctx)
    async with async_session_factory() as portal, litellm_session_factory() as litellm:
        try:
            return await team_budget_usage_logic(portal, litellm, ident.user_id, team_id)
        except NotVisibleError as e:
            raise ValueError(str(e)) from e


async def _health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


# Top-level ASGI app (served directly, so its built-in lifespan runs the
# streamable-HTTP session manager). DNS-rebinding host protection is disabled:
# this is an internal service authenticated by the sk- key, not a
# browser-localhost target, and it is reached via the in-cluster service name.
app = mcp.streamable_http_app(
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)
app.router.routes.append(Route("/health", _health, methods=["GET"]))
