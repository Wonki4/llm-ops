"""Gateway SSO: /api/external/token-auth swaps a Keycloak token for the user's SSO key."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt

import app.api.external as external
from app.auth.jwt import TokenPayload
from app.clients.litellm import LiteLLMClient, get_litellm_client
from app.config import settings
from app.db.session import get_db, get_litellm_db
from app.main import app
from app.services.portal_keys import KEY_JWT_SECRET, generate_sk_jwt

API_KEY = "test-external-key"
HEADERS = {"X-Api-Key": API_KEY}


def _result(*, scalar=None, first=None, rows=None):
    r = MagicMock()
    r.scalar.return_value = scalar
    r.scalar_one_or_none.return_value = scalar
    r.first.return_value = first
    r.mappings.return_value.first.return_value = first
    r.mappings.return_value.__iter__.side_effect = lambda: iter(rows or [])
    return r


class FakeDBs:
    """Portal + LiteLLM DBs answering by SQL text; records what ran."""

    def __init__(self, *, beta="true", grant=("all-proxy-models",), member=False, sso_rows=()):
        self.beta = beta
        self.grant = list(grant) if grant is not None else None
        self.member = member
        self.sso_rows = list(sso_rows)
        self.portal_sql: list[str] = []
        self.litellm_sql: list[tuple[str, dict]] = []

        self.portal = AsyncMock()
        self.portal.execute = AsyncMock(side_effect=self._portal)
        self.litellm = AsyncMock()
        self.litellm.execute = AsyncMock(side_effect=self._litellm)

    async def _portal(self, stmt, params=None):
        sql = str(stmt)
        self.portal_sql.append(sql)
        if "custom_portal_settings" in sql and "WHERE key = :key" in sql:
            return _result(scalar=self.beta)
        if "custom_portal_settings" in sql:
            return _result(rows=[])
        if "custom_key_sequence" in sql and "SELECT" in sql:
            return _result(scalar=10050)
        return _result()

    async def _litellm(self, stmt, params=None):
        sql = str(stmt)
        self.litellm_sql.append((sql, params or {}))
        if "LiteLLM_UserTable" in sql:
            return _result(first={"models": self.grant, "max_budget": None} if self.grant is not None else None)
        if "LiteLLM_TeamMembership" in sql:
            return _result(first=(1,) if self.member else None)
        if "LiteLLM_VerificationToken" in sql:
            team_id = (params or {}).get("team_id")
            return _result(rows=[r for r in self.sso_rows if r["team_id"] == team_id])
        return _result()


def _token(**overrides) -> TokenPayload:
    base = dict(
        sub="kc-sub", preferred_username="user001", email="user001@example.com", exp=1_900_000_000, azp="webapp"
    )
    base.update(overrides)
    return TokenPayload(**base)


@pytest.fixture
def litellm_client() -> MagicMock:
    client = MagicMock(spec=LiteLLMClient)
    client.generate_key = AsyncMock(return_value={"token": "hash"})
    return client


@pytest.fixture
def sso(monkeypatch, litellm_client):
    """Enable SSO, stub token verification + provisioning, wire fake DBs."""
    monkeypatch.setattr(settings, "external_api_key", API_KEY)
    monkeypatch.setattr(settings, "gateway_sso_enabled", True)
    monkeypatch.setattr(settings, "gateway_allowed_azp", [])

    state = {"token": _token(), "verify_error": None, "verify_calls": []}

    async def fake_verify(token, audience=None):
        state["verify_calls"].append((token, audience))
        if state["verify_error"]:
            raise ValueError(state["verify_error"])
        return state["token"]

    monkeypatch.setattr(external, "verify_token", fake_verify)
    monkeypatch.setattr(external, "upsert_portal_user", AsyncMock())
    provision = AsyncMock()
    monkeypatch.setattr(external, "auto_provision_user", provision)
    state["provision"] = provision

    def wire(dbs: FakeDBs) -> AsyncClient:
        app.dependency_overrides[get_litellm_client] = lambda: litellm_client
        app.dependency_overrides[get_db] = lambda: dbs.portal
        app.dependency_overrides[get_litellm_db] = lambda: dbs.litellm
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    state["wire"] = wire
    yield state
    app.dependency_overrides.clear()


def _claims(sk_key: str) -> dict:
    return json.loads(jwt.decode(sk_key.removeprefix("sk-"), KEY_JWT_SECRET, algorithms=["HS256"])["sub"])


# ─── gatekeeping ─────────────────────────────────────────────


async def test_disabled_returns_404(sso, monkeypatch, litellm_client):
    monkeypatch.setattr(settings, "gateway_sso_enabled", False)
    async with sso["wire"](FakeDBs()) as client:
        resp = await client.post("/api/external/token-auth", json={"token": "t"}, headers=HEADERS)
    assert resp.status_code == 404
    litellm_client.generate_key.assert_not_awaited()


async def test_requires_external_api_key(sso):
    async with sso["wire"](FakeDBs()) as client:
        resp = await client.post("/api/external/token-auth", json={"token": "t"}, headers={"X-Api-Key": "wrong"})
    assert resp.status_code == 401
    assert sso["verify_calls"] == []


async def test_invalid_token_is_401_and_verified_against_gateway_audience(sso, monkeypatch, litellm_client):
    monkeypatch.setattr(settings, "gateway_token_audience", "litellm-gateway")
    sso["verify_error"] = "Invalid token: Signature has expired"
    async with sso["wire"](FakeDBs()) as client:
        resp = await client.post("/api/external/token-auth", json={"token": "kc-token"}, headers=HEADERS)
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid token"  # uniform: never echo why
    assert sso["verify_calls"] == [("kc-token", "litellm-gateway")]
    litellm_client.generate_key.assert_not_awaited()


async def test_disallowed_client_is_401(sso, monkeypatch, litellm_client):
    monkeypatch.setattr(settings, "gateway_allowed_azp", ["chat-app"])
    sso["token"] = _token(azp="other-app")
    async with sso["wire"](FakeDBs()) as client:
        resp = await client.post("/api/external/token-auth", json={"token": "t"}, headers=HEADERS)
    assert resp.status_code == 401
    litellm_client.generate_key.assert_not_awaited()


async def test_allowed_client_passes(sso, monkeypatch):
    monkeypatch.setattr(settings, "gateway_allowed_azp", ["chat-app"])
    sso["token"] = _token(azp="chat-app")
    async with sso["wire"](FakeDBs()) as client:
        resp = await client.post("/api/external/token-auth", json={"token": "t"}, headers=HEADERS)
    assert resp.status_code == 200, resp.text


# ─── personal SSO key ────────────────────────────────────────


async def test_personal_needs_beta(sso, litellm_client):
    async with sso["wire"](FakeDBs(beta="false")) as client:
        resp = await client.post("/api/external/token-auth", json={"token": "t"}, headers=HEADERS)
    assert resp.status_code == 403 and "Beta" in resp.text
    litellm_client.generate_key.assert_not_awaited()


@pytest.mark.parametrize("grant", [None, [], ["no-default-models"]])
async def test_personal_needs_a_grant(sso, litellm_client, grant):
    async with sso["wire"](FakeDBs(grant=grant)) as client:
        resp = await client.post("/api/external/token-auth", json={"token": "t"}, headers=HEADERS)
    assert resp.status_code == 403 and "권한" in resp.text
    litellm_client.generate_key.assert_not_awaited()


async def test_first_personal_request_mints_an_sso_key(sso, litellm_client):
    dbs = FakeDBs()
    async with sso["wire"](dbs) as client:
        resp = await client.post("/api/external/token-auth", json={"token": "t"}, headers=HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user_id"] == "USER001"  # canonical id is upper-cased
    assert body["team_id"] is None and body["expires_at"] == 1_900_000_000

    kw = litellm_client.generate_key.await_args.kwargs
    assert kw["user_id"] == "USER001" and kw["team_id"] is None
    assert kw["metadata"]["issued_via"] == "gateway_sso"
    assert kw["metadata"]["key_type"] == "personal"  # shows up in personal usage like any personal key
    assert kw["metadata"]["sk_key_id"] == 10050
    assert body["litellm_key"] == kw["key"] and body["litellm_key"].startswith("sk-")
    claims = _claims(body["litellm_key"])
    assert claims["regUserId"] == "USER001" and claims["prjId"] is None

    sso["provision"].assert_awaited_once()
    assert any("pg_advisory_xact_lock" in s for s in dbs.portal_sql)
    dbs.portal.commit.assert_awaited()


async def test_existing_personal_sso_key_is_reused(sso, litellm_client):
    row = {
        "user_id": "USER001",
        "team_id": None,
        "metadata": {"issued_via": "gateway_sso", "sk_key_id": 10007, "sk_iat": 1_700_000_000},
    }
    dbs = FakeDBs(sso_rows=[row])
    async with sso["wire"](dbs) as client:
        resp = await client.post("/api/external/token-auth", json={"token": "t"}, headers=HEADERS)
    assert resp.status_code == 200, resp.text
    assert resp.json()["litellm_key"] == generate_sk_jwt(10007, None, "USER001", iat=1_700_000_000)
    litellm_client.generate_key.assert_not_awaited()
    assert not any("pg_advisory_xact_lock" in s for s in dbs.portal_sql)


# ─── team SSO key ────────────────────────────────────────────


async def test_team_requires_membership(sso, litellm_client):
    async with sso["wire"](FakeDBs(member=False)) as client:
        resp = await client.post("/api/external/token-auth", json={"token": "t", "team_id": "team-1"}, headers=HEADERS)
    assert resp.status_code == 403
    litellm_client.generate_key.assert_not_awaited()


async def test_team_member_gets_a_team_sso_key_without_personal_grant(sso, litellm_client):
    # No personal grant needed: the team governs a team key.
    dbs = FakeDBs(member=True, grant=None, beta="false")
    async with sso["wire"](dbs) as client:
        resp = await client.post("/api/external/token-auth", json={"token": "t", "team_id": "team-1"}, headers=HEADERS)
    assert resp.status_code == 200, resp.text
    kw = litellm_client.generate_key.await_args.kwargs
    assert kw["team_id"] == "team-1"
    assert kw["metadata"]["issued_via"] == "gateway_sso" and "key_type" not in kw["metadata"]
    assert _claims(resp.json()["litellm_key"])["prjId"] == "team-1"


async def test_team_and_personal_keys_are_separate(sso, litellm_client):
    personal = {
        "user_id": "USER001",
        "team_id": None,
        "metadata": {"issued_via": "gateway_sso", "sk_key_id": 1, "sk_iat": 1},
    }
    dbs = FakeDBs(member=True, sso_rows=[personal])
    async with sso["wire"](dbs) as client:
        resp = await client.post("/api/external/token-auth", json={"token": "t", "team_id": "team-1"}, headers=HEADERS)
    assert resp.status_code == 200, resp.text
    litellm_client.generate_key.assert_awaited_once()  # the personal key is not reused for the team
    lookups = [p for s, p in dbs.litellm_sql if "LiteLLM_VerificationToken" in s]
    assert lookups and all(p["team_id"] == "team-1" for p in lookups)


async def test_blank_team_header_means_personal(sso, litellm_client):
    async with sso["wire"](FakeDBs()) as client:
        resp = await client.post("/api/external/token-auth", json={"token": "t", "team_id": "  "}, headers=HEADERS)
    assert resp.status_code == 200, resp.text
    assert litellm_client.generate_key.await_args.kwargs["team_id"] is None


async def test_mint_failure_rolls_back_and_releases_the_lock(sso, litellm_client):
    litellm_client.generate_key = AsyncMock(side_effect=RuntimeError("boom"))
    dbs = FakeDBs()
    async with sso["wire"](dbs) as client:
        with pytest.raises(RuntimeError):
            await client.post("/api/external/token-auth", json={"token": "t"}, headers=HEADERS)
    dbs.portal.rollback.assert_awaited()
