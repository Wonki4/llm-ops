"""Portal MCP served by the backend at /mcp (RFC 9728 metadata, 401 hint, stateless)."""

import dataclasses
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from mcp.server.auth.provider import AccessToken

from app import main
from app.auth.jwt import TokenPayload
from app.config import Settings, settings
from app.mcp import auth as mcp_auth
from app.mcp import server as mcp_server_module
from app.mcp import tools as mcp_tools
from app.mcp.auth import McpTokenVerifier

MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
}
TOOLS_LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}


def _settings(**kw) -> Settings:
    base = {"mcp_enabled": True, "mcp_public_url": "https://portal-api.example.com/mcp"}
    return Settings(**{**base, **kw})


# ─── startup validation ───────────────────────────────────────


def test_validate_rejects_missing_public_url():
    with pytest.raises(RuntimeError, match="mcp_public_url"):
        mcp_server_module.validate_mcp_settings(_settings(mcp_public_url=""))


def test_validate_rejects_public_url_not_ending_in_mcp():
    with pytest.raises(RuntimeError, match="/mcp"):
        mcp_server_module.validate_mcp_settings(_settings(mcp_public_url="http://x/other"))


def test_validate_rejects_portal_client_as_mcp_client():
    with pytest.raises(RuntimeError, match="keycloak_client_id"):
        mcp_server_module.validate_mcp_settings(_settings(mcp_client_id="litellm-portal"))


def test_validate_rejects_empty_client_or_audience():
    with pytest.raises(RuntimeError):
        mcp_server_module.validate_mcp_settings(_settings(mcp_client_id=""))
    with pytest.raises(RuntimeError):
        mcp_server_module.validate_mcp_settings(_settings(mcp_token_audience=""))


def test_validate_accepts_a_good_config():
    mcp_server_module.validate_mcp_settings(_settings())


def test_validate_noop_when_disabled():
    mcp_server_module.validate_mcp_settings(_settings(mcp_enabled=False, mcp_public_url=""))


# ─── served app ───────────────────────────────────────────────


@pytest.fixture
def installed(monkeypatch):
    """A bare FastAPI app with one route and the MCP installed on it."""
    monkeypatch.setattr(settings, "mcp_enabled", True)
    monkeypatch.setattr(settings, "mcp_public_url", "http://test/mcp")
    monkeypatch.setattr(settings, "keycloak_base_url", "http://kc")
    monkeypatch.setattr(settings, "keycloak_realm", "r")

    test_app = FastAPI()

    @test_app.get("/ping")
    async def ping() -> dict:
        return {"pong": True}

    server = mcp_server_module.install_mcp(test_app)
    client = AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test")
    return SimpleNamespace(app=test_app, server=server, client=client)


async def test_protected_resource_metadata_points_at_keycloak(installed):
    async with installed.client as c:
        resp = await c.get("/.well-known/oauth-protected-resource/mcp")

    assert resp.status_code == 200
    body = resp.json()
    assert body["resource"] == "http://test/mcp"
    assert body["authorization_servers"] == ["http://kc/realms/r"]


async def test_unauthenticated_mcp_call_gets_401_with_metadata_hint(installed):
    async with installed.client as c:
        resp = await c.post("/mcp", json=TOOLS_LIST, headers=MCP_HEADERS)

    assert resp.status_code == 401
    hint = 'resource_metadata="http://test/.well-known/oauth-protected-resource/mcp"'
    assert hint in resp.headers["www-authenticate"]


async def test_rejected_token_gets_401(installed, monkeypatch):
    async def reject(self, token: str) -> None:
        return None

    monkeypatch.setattr(McpTokenVerifier, "verify_token", reject)

    async with installed.client as c:
        resp = await c.post("/mcp", json=TOOLS_LIST, headers={**MCP_HEADERS, "Authorization": "Bearer bad"})

    assert resp.status_code == 401


async def test_authenticated_stateless_tools_list(installed, monkeypatch):
    async def accept(self, token: str) -> AccessToken | None:
        if token != "good":
            return None
        return AccessToken(token=token, client_id="portal-mcp", scopes=[], claims={"preferred_username": "admin001"})

    monkeypatch.setattr(McpTokenVerifier, "verify_token", accept)
    headers = {**MCP_HEADERS, "Authorization": "Bearer good"}

    async with installed.server.session_manager.run(), installed.client as c:
        init = await c.post("/mcp", json=INITIALIZE, headers=headers)
        # a different replica: no mcp-session-id, no shared state
        listed = await c.post("/mcp", json=TOOLS_LIST, headers=headers)

    assert init.status_code == 200, init.text
    assert "mcp-session-id" not in init.headers
    assert listed.status_code == 200, listed.text
    names = {t["name"] for t in listed.json()["result"]["tools"]}
    assert "list_recipes" in names


async def test_tool_call_over_http_acts_as_the_token_caller(installed, monkeypatch, mock_db, super_user):
    """The verified token reaches the tool (auth context survives the stateless transport)."""
    payload = TokenPayload(sub="s", preferred_username="admin001", azp="portal-mcp")

    async def accept(self, token: str) -> AccessToken:
        return AccessToken(token=token, client_id="portal-mcp", scopes=[], claims=dataclasses.asdict(payload))

    @asynccontextmanager
    async def session():
        yield mock_db

    upsert = AsyncMock(return_value=super_user)
    monkeypatch.setattr(McpTokenVerifier, "verify_token", accept)
    monkeypatch.setattr(mcp_auth, "upsert_portal_user", upsert)
    monkeypatch.setattr(mcp_tools, "async_session_factory", session)
    mock_db.commit = AsyncMock()
    call = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "whoami", "arguments": {}}}
    headers = {**MCP_HEADERS, "Authorization": "Bearer good"}

    async with installed.server.session_manager.run(), installed.client as c:
        await c.post("/mcp", json=INITIALIZE, headers=headers)
        resp = await c.post("/mcp", json=call, headers=headers)

    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    assert result.get("isError") is not True, result
    assert json.loads(result["content"][0]["text"]) == {"user_id": "admin001", "global_role": "super_user"}
    upsert.assert_awaited_once_with(mock_db, payload)


async def test_other_paths_untouched(installed):
    async with installed.client as c:
        ok = await c.get("/ping")
        missing = await c.get("/nope")
        wrong_method = await c.post("/ping")
        trailing = await c.get("/mcp/")

    assert ok.json() == {"pong": True}
    assert missing.status_code == 404 and missing.json() == {"detail": "Not Found"}
    assert wrong_method.status_code == 405
    assert trailing.status_code == 404  # only the exact MCP paths leave FastAPI


# ─── the real app ─────────────────────────────────────────────


async def test_disabled_by_default_mcp_is_404():
    assert main.mcp_server is None
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://test") as c:
        mcp = await c.post("/mcp", json=TOOLS_LIST, headers=MCP_HEADERS)
        prm = await c.get("/.well-known/oauth-protected-resource/mcp")

    assert mcp.status_code == 404
    assert prm.status_code == 404


async def test_lifespan_runs_the_mcp_session_manager(monkeypatch):
    events: list[str] = []

    @asynccontextmanager
    async def run():
        events.append("start")
        yield
        events.append("stop")

    monkeypatch.setattr(main, "mcp_server", SimpleNamespace(session_manager=SimpleNamespace(run=run)))

    async with main.lifespan(main.app):
        assert events == ["start"]
    assert events == ["start", "stop"]
