"""Keycloak SSO: token recognition and token -> key resolution/caching."""

import time

import httpx
import pytest
from tokens import ISSUER, _b64, keycloak_token, make_jwt, portal_key

import app.sso as sso
from app.config import settings


class _FakeResp:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _FakeClient:
    calls: list = []
    status_code = 200
    payload: object = {}
    raise_exc: Exception | None = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, headers=None, json=None):
        _FakeClient.calls.append({"url": url, "headers": headers, "json": json})
        if _FakeClient.raise_exc:
            raise _FakeClient.raise_exc
        return _FakeResp(_FakeClient.status_code, _FakeClient.payload)


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    monkeypatch.setattr(settings, "keycloak_issuer", ISSUER)
    monkeypatch.setattr(settings, "backend_base_url", "http://backend:8000")
    monkeypatch.setattr(settings, "backend_api_key", "test-api-key")
    monkeypatch.setattr(settings, "sso_cache_ttl", 60)
    monkeypatch.setattr(sso.httpx, "AsyncClient", _FakeClient)
    _FakeClient.calls = []
    _FakeClient.status_code = 200
    _FakeClient.payload = {"litellm_key": "sk-user-key", "expires_at": int(time.time()) + 300}
    _FakeClient.raise_exc = None
    sso._cache.clear()
    yield
    sso._cache.clear()


# ─── recognition ─────────────────────────────────────────────


def test_keycloak_token_is_recognised_by_issuer():
    assert sso.looks_like_keycloak_token(keycloak_token())


def test_trailing_slash_in_configured_issuer_is_tolerated(monkeypatch):
    monkeypatch.setattr(settings, "keycloak_issuer", ISSUER + "/")
    assert sso.looks_like_keycloak_token(keycloak_token())


def test_other_issuer_is_not_recognised():
    assert not sso.looks_like_keycloak_token(make_jwt({"iss": "https://evil.example.com/realms/litellm"}))


def test_portal_key_jwt_is_not_mistaken_for_keycloak():
    assert not sso.looks_like_keycloak_token(portal_key())


@pytest.mark.parametrize("value", ["sk-abc", "plainkey", "a.b", "a.!!!.c", "", f"x.{_b64(['list'])}.y"])
def test_garbage_is_not_recognised(value):
    assert not sso.looks_like_keycloak_token(value)


def test_nothing_is_recognised_without_an_issuer(monkeypatch):
    monkeypatch.setattr(settings, "keycloak_issuer", "")
    assert not sso.looks_like_keycloak_token(keycloak_token())
    assert not sso.is_enabled()


def test_enabled_needs_the_backend_too(monkeypatch):
    assert sso.is_enabled()
    monkeypatch.setattr(settings, "backend_api_key", "")
    assert not sso.is_enabled()


# ─── resolution ──────────────────────────────────────────────


async def test_resolves_via_backend_with_api_key_and_team():
    key = await sso.resolve_user_key("tok", "team-1")
    assert key == "sk-user-key"
    call = _FakeClient.calls[0]
    assert call["url"] == "http://backend:8000/api/external/token-auth"
    assert call["headers"]["X-Api-Key"] == "test-api-key"
    assert call["json"] == {"token": "tok", "team_id": "team-1"}


async def test_success_is_cached_per_token_and_team():
    await sso.resolve_user_key("tok", None)
    await sso.resolve_user_key("tok", None)
    assert len(_FakeClient.calls) == 1
    await sso.resolve_user_key("tok", "team-1")  # different team -> different key
    await sso.resolve_user_key("other-tok", None)  # refreshed token -> fresh lookup
    assert len(_FakeClient.calls) == 3


async def test_raw_token_is_not_kept_in_the_cache():
    await sso.resolve_user_key("very-secret-token", None)
    assert all("very-secret-token" not in k for k in sso._cache)


async def test_cache_never_outlives_the_token(monkeypatch):
    _FakeClient.payload = {"litellm_key": "sk-user-key", "expires_at": int(time.time()) - 1}
    await sso.resolve_user_key("tok", None)
    await sso.resolve_user_key("tok", None)
    assert len(_FakeClient.calls) == 2
    assert sso._cache == {}


async def test_cache_ttl_is_capped_by_token_expiry():
    _FakeClient.payload = {"litellm_key": "sk-user-key", "expires_at": int(time.time()) + 10}
    await sso.resolve_user_key("tok", None)
    (_, expires_mono), = sso._cache.values()
    assert expires_mono - time.monotonic() <= 11


async def test_401_raises_and_is_not_cached():
    _FakeClient.status_code = 401
    _FakeClient.payload = {"detail": "Invalid token"}
    for _ in range(2):
        with pytest.raises(sso.SsoAuthError) as e:
            await sso.resolve_user_key("tok", None)
        assert e.value.status_code == 401
    assert len(_FakeClient.calls) == 2


async def test_403_passes_the_backend_reason_through():
    _FakeClient.status_code = 403
    _FakeClient.payload = {"detail": "해당 팀의 멤버가 아닙니다."}
    with pytest.raises(sso.SsoAuthError) as e:
        await sso.resolve_user_key("tok", "team-x")
    assert e.value.status_code == 403 and e.value.message == "해당 팀의 멤버가 아닙니다."
    assert sso._cache == {}


@pytest.mark.parametrize("status", [404, 500, 502])
async def test_other_backend_statuses_are_503(status):
    _FakeClient.status_code = status
    _FakeClient.payload = {"detail": "x"}
    with pytest.raises(sso.SsoAuthError) as e:
        await sso.resolve_user_key("tok", None)
    assert e.value.status_code == 503


async def test_backend_unreachable_is_503():
    _FakeClient.raise_exc = httpx.ConnectError("down")
    with pytest.raises(sso.SsoAuthError) as e:
        await sso.resolve_user_key("tok", None)
    assert e.value.status_code == 503


async def test_cache_is_pruned_when_full(monkeypatch):
    monkeypatch.setattr(settings, "sso_cache_max_entries", 3)
    sso._cache["stale"] = ("sk-old", time.monotonic() - 1)
    for i in range(5):
        await sso.resolve_user_key(f"tok-{i}", None)
    assert "stale" not in sso._cache
    assert len(sso._cache) <= 3
