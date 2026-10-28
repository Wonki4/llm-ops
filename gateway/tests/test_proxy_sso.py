"""End to end through the gateway: a Keycloak token in, the user's SSO key out to LiteLLM."""

import json
import time

import httpx
import pytest
from tokens import ISSUER, keycloak_token, portal_key

import app.proxy as proxy
import app.sso as sso
from app.config import settings
from app.main import app

_RealAsyncClient = httpx.AsyncClient


class Upstreams:
    """Plays both the management backend and LiteLLM."""

    def __init__(self):
        self.backend_calls: list[dict] = []
        self.litellm_calls: list[httpx.Request] = []
        self.backend_status = 200
        self.backend_body: dict = {"litellm_key": "sk-user-sso-key", "expires_at": int(time.time()) + 300}

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/external/token-auth":
            self.backend_calls.append({"headers": dict(request.headers), "json": json.loads(request.content)})
            return httpx.Response(self.backend_status, json=self.backend_body)
        self.litellm_calls.append(request)
        return httpx.Response(200, json={"object": "list", "data": []})


@pytest.fixture
def upstreams(monkeypatch):
    up = Upstreams()

    def factory(*args, **kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(up.handler))
        return _RealAsyncClient(*args, **kwargs)

    # Both the proxy and the SSO module build their own clients; route them to the mock.
    monkeypatch.setattr(proxy.httpx, "AsyncClient", factory)
    monkeypatch.setattr(proxy, "_LITELLM_BASE", "http://litellm:4000")
    monkeypatch.setattr(settings, "keycloak_issuer", ISSUER)
    monkeypatch.setattr(settings, "backend_base_url", "http://backend:8000")
    monkeypatch.setattr(settings, "backend_api_key", "test-api-key")
    sso._cache.clear()
    yield up
    sso._cache.clear()


def _client() -> httpx.AsyncClient:
    return _RealAsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway")


async def test_keycloak_token_is_swapped_for_the_sso_key(upstreams):
    token = keycloak_token()
    async with _client() as client:
        resp = await client.get("/v1/models", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text

    assert upstreams.backend_calls[0]["json"] == {"token": token, "team_id": None}
    assert upstreams.backend_calls[0]["headers"]["x-api-key"] == "test-api-key"
    sent = upstreams.litellm_calls[0]
    assert sent.headers["authorization"] == "Bearer sk-user-sso-key"
    assert all(token not in v for v in sent.headers.values())  # the Keycloak token never reaches LiteLLM


async def test_team_header_selects_the_team_key_and_is_not_forwarded(upstreams):
    async with _client() as client:
        resp = await client.get(
            "/v1/models", headers={"Authorization": f"Bearer {keycloak_token()}", "X-Team-Id": "team-1"}
        )
    assert resp.status_code == 200, resp.text
    assert upstreams.backend_calls[0]["json"]["team_id"] == "team-1"
    assert "x-team-id" not in upstreams.litellm_calls[0].headers


async def test_other_credential_headers_are_dropped_on_the_sso_path(upstreams):
    async with _client() as client:
        await client.get(
            "/v1/models", headers={"Authorization": f"Bearer {keycloak_token()}", "x-api-key": "sk-someone-else"}
        )
    sent = upstreams.litellm_calls[0]
    assert "x-api-key" not in sent.headers
    assert sent.headers["authorization"] == "Bearer sk-user-sso-key"


async def test_post_body_and_lowercase_bearer_scheme(upstreams):
    body = {"model": "glm", "messages": [{"role": "user", "content": "hi"}]}
    async with _client() as client:
        resp = await client.post(
            "/v1/chat/completions", json=body, headers={"authorization": f"bearer {keycloak_token()}"}
        )
    assert resp.status_code == 200, resp.text
    sent = upstreams.litellm_calls[0]
    assert json.loads(sent.content) == body
    assert sent.headers["authorization"] == "Bearer sk-user-sso-key"


async def test_portal_key_keeps_the_normal_path(upstreams):
    key = portal_key()  # HS256 JWT without iss, exactly what the portal shows users
    async with _client() as client:
        resp = await client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200, resp.text
    assert upstreams.backend_calls == []
    assert upstreams.litellm_calls[0].headers["authorization"] == f"Bearer sk-{key}"


@pytest.mark.parametrize(
    ("backend_status", "detail", "expected"),
    [(401, "Invalid token", 401), (403, "해당 팀의 멤버가 아닙니다.", 403), (500, "boom", 503)],
)
async def test_denials_never_reach_litellm(upstreams, backend_status, detail, expected):
    upstreams.backend_status = backend_status
    upstreams.backend_body = {"detail": detail}
    async with _client() as client:
        resp = await client.get("/v1/models", headers={"Authorization": f"Bearer {keycloak_token()}"})
    assert resp.status_code == expected
    assert resp.json()["error"]["type"] == "auth_error"
    if expected == 403:
        assert resp.json()["error"]["message"] == detail
    assert upstreams.litellm_calls == []


async def test_sso_disabled_leaves_keycloak_tokens_on_the_normal_path(upstreams, monkeypatch):
    monkeypatch.setattr(settings, "keycloak_issuer", "")
    async with _client() as client:
        await client.get("/v1/models", headers={"Authorization": f"Bearer {keycloak_token()}"})
    assert upstreams.backend_calls == []
    assert len(upstreams.litellm_calls) == 1  # LiteLLM rejects it as an unknown key, as before
