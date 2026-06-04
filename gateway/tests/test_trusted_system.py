"""Tests for keyless trusted-system resolution and caching."""

import pytest

import app.trusted_system as ts
from app.config import settings


class _FakeResp:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    """Stand-in for httpx.AsyncClient that records calls."""

    calls: list = []
    status_code = 200
    payload: dict = {"litellm_key": "sk-system-key"}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, headers=None, json=None):
        _FakeClient.calls.append({"url": url, "headers": headers, "json": json})
        return _FakeResp(_FakeClient.status_code, _FakeClient.payload)


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    # Configure the keyless path and stub the HTTP client + cache.
    monkeypatch.setattr(settings, "backend_base_url", "http://backend:8000")
    monkeypatch.setattr(settings, "backend_api_key", "test-api-key")
    monkeypatch.setattr(ts.httpx, "AsyncClient", _FakeClient)
    _FakeClient.calls = []
    _FakeClient.status_code = 200
    _FakeClient.payload = {"litellm_key": "sk-system-key"}
    ts._cache.clear()
    yield
    ts._cache.clear()


async def test_disabled_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "backend_base_url", "")
    assert await ts.resolve_system_key("payroll", "secret") is None
    assert _FakeClient.calls == []


async def test_resolves_and_forwards_api_key():
    key = await ts.resolve_system_key("payroll", "secret")
    assert key == "sk-system-key"
    assert _FakeClient.calls[0]["headers"]["X-Api-Key"] == "test-api-key"
    assert _FakeClient.calls[0]["json"] == {"system_id": "payroll", "secret": "secret"}


async def test_success_is_cached():
    await ts.resolve_system_key("payroll", "secret")
    await ts.resolve_system_key("payroll", "secret")
    assert len(_FakeClient.calls) == 1  # second call served from cache


async def test_rotated_secret_misses_cache():
    await ts.resolve_system_key("payroll", "secret")
    await ts.resolve_system_key("payroll", "different-secret")
    assert len(_FakeClient.calls) == 2


async def test_unauthorized_returns_none_and_is_not_cached():
    _FakeClient.status_code = 401
    _FakeClient.payload = {"detail": "Invalid system credentials"}
    assert await ts.resolve_system_key("payroll", "bad") is None
    assert await ts.resolve_system_key("payroll", "bad") is None
    assert len(_FakeClient.calls) == 2  # failures are never cached
