"""Personal (team-less) keys: minting rules, JWT claims, payload, reveal, list flag."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from jose import jwt

from app.api.keys import _KEY_JWT_SECRET
from app.db.session import get_litellm_db
from app.main import app


def _result(*, scalar=None, mappings=None, first=None):
    r = MagicMock()
    r.scalar.return_value = scalar
    r.scalar_one_or_none.return_value = scalar
    r.mappings.return_value.first.return_value = first
    r.mappings.return_value.__iter__.return_value = iter(mappings or [])
    return r


def _portal_db(mock_db, *, beta="true"):
    """Portal DB: settings rows + the key sequence."""

    async def execute(stmt, params=None):
        sql = str(stmt)
        if "custom_portal_settings" in sql and "WHERE key = :key" in sql:
            return _result(scalar=beta)
        if "custom_portal_settings" in sql:
            return _result(
                mappings=[{"key": "default_tpm_limit", "value": "5000"}, {"key": "default_rpm_limit", "value": "50"}]
            )
        if "custom_key_sequence" in sql and "SELECT" in sql:
            return _result(scalar=10042)
        return _result()

    mock_db.execute = AsyncMock(side_effect=execute)
    return mock_db


def _litellm_db(user_row=None, key_row=None):
    db = AsyncMock()

    async def execute(stmt, params=None):
        sql = str(stmt)
        if "LiteLLM_UserTable" in sql:
            return _result(first=user_row)
        if "LiteLLM_VerificationToken" in sql and "WHERE token = :token" in sql:
            return _result(first=key_row)
        if "LiteLLM_VerificationToken" in sql:
            return _result(mappings=[key_row] if key_row else [])
        return _result()

    db.execute = AsyncMock(side_effect=execute)
    return db


@pytest.fixture
def litellm_db():
    holder = {"db": _litellm_db()}
    app.dependency_overrides[get_litellm_db] = lambda: holder["db"]
    yield holder
    app.dependency_overrides.pop(get_litellm_db, None)


def _claims(sk_key: str) -> dict:
    return json.loads(jwt.decode(sk_key.removeprefix("sk-"), _KEY_JWT_SECRET, algorithms=["HS256"])["sub"])


async def test_personal_key_is_minted_with_user_claims_and_tags(
    client_for_user, regular_user, mock_db, mock_litellm, litellm_db
):
    _portal_db(mock_db)
    litellm_db["db"] = _litellm_db(user_row={"models": ["beta-models"], "max_budget": None})
    mock_litellm.get_model_info = AsyncMock(
        return_value=[{"model_name": "glm", "model_info": {"access_groups": ["beta-models"]}}]
    )
    mock_litellm.generate_key = AsyncMock(return_value={"key": "ignored", "token": "hash"})
    async with client_for_user(regular_user) as client:
        resp = await client.post("/api/keys", json={"key_alias": "mine", "models": ["glm"]})
    assert resp.status_code == 200, resp.text
    kw = mock_litellm.generate_key.await_args.kwargs
    assert kw["team_id"] is None and kw.get("tags") is None  # key tags are Enterprise-only in LiteLLM
    assert kw["metadata"]["key_type"] == "personal" and kw["metadata"]["sk_key_id"] == 10042
    assert kw["tpm_limit"] == 5000 and kw["rpm_limit"] == 50
    claims = _claims(kw["key"])
    assert claims["prjId"] is None and claims["keyType"] == "USR" and claims["regUserId"] == regular_user.user_id
    assert resp.json()["key"] == kw["key"].removeprefix("sk-")


async def test_personal_key_refused_when_beta_is_off(client_for_user, regular_user, mock_db, mock_litellm, litellm_db):
    _portal_db(mock_db, beta="false")
    litellm_db["db"] = _litellm_db(user_row={"models": ["all-proxy-models"], "max_budget": None})
    async with client_for_user(regular_user) as client:
        resp = await client.post("/api/keys", json={"key_alias": "mine"})
    assert resp.status_code == 403 and "Beta" in resp.text
    mock_litellm.generate_key.assert_not_awaited()


@pytest.mark.parametrize("models", [[], ["no-default-models"], None])
async def test_personal_key_refused_without_a_grant(
    client_for_user, regular_user, mock_db, mock_litellm, litellm_db, models
):
    _portal_db(mock_db)
    litellm_db["db"] = _litellm_db(user_row={"models": models, "max_budget": None} if models is not None else None)
    async with client_for_user(regular_user) as client:
        resp = await client.post("/api/keys", json={"key_alias": "mine"})
    assert resp.status_code == 403 and "권한" in resp.text
    mock_litellm.generate_key.assert_not_awaited()


async def test_personal_key_models_must_stay_inside_the_grant(
    client_for_user, regular_user, mock_db, mock_litellm, litellm_db
):
    _portal_db(mock_db)
    litellm_db["db"] = _litellm_db(user_row={"models": ["beta-models"], "max_budget": 10.0})
    mock_litellm.get_model_info = AsyncMock(
        return_value=[{"model_name": "glm", "model_info": {"access_groups": ["beta-models"]}}]
    )
    async with client_for_user(regular_user) as client:
        bad = await client.post("/api/keys", json={"key_alias": "mine", "models": ["gpt-4o"]})
        over = await client.post("/api/keys", json={"key_alias": "mine", "max_budget": 25})
    assert bad.status_code == 400 and "gpt-4o" in bad.text
    assert over.status_code == 400 and "예산" in over.text
    mock_litellm.generate_key.assert_not_awaited()


async def test_team_key_path_is_unchanged(client_for_user, regular_user, mock_db, mock_litellm, litellm_db):
    _portal_db(mock_db, beta="false")  # the switch does not matter for team keys
    mock_litellm.generate_key = AsyncMock(return_value={"key": "ignored", "token": "hash"})
    async with client_for_user(regular_user) as client:
        resp = await client.post("/api/keys", json={"team_id": "team-1", "key_alias": "team"})
    assert resp.status_code == 200, resp.text
    kw = mock_litellm.generate_key.await_args.kwargs
    assert kw["team_id"] == "team-1" and kw.get("tags") is None and "key_type" not in kw["metadata"]
    assert _claims(kw["key"])["keyType"] == "PRJ"


async def test_reveal_and_list_handle_personal_keys(client_for_user, regular_user, mock_db, mock_litellm, litellm_db):
    key_row = {
        "token": "hash",
        "key_name": "sk-...",
        "key_alias": "u-1",
        "team_id": None,
        "user_id": regular_user.user_id,
        "spend": 0,
        "max_budget": None,
        "budget_duration": None,
        "budget_reset_at": None,
        "models": ["glm"],
        "expires": None,
        "created_at": None,
        "metadata": {"sk_key_id": 10042, "sk_iat": 1700000000, "display_alias": "mine"},
        "tpm_limit": 5000,
        "rpm_limit": 50,
        "team_metadata": None,
    }
    litellm_db["db"] = _litellm_db(key_row=key_row)
    mock_db.execute = AsyncMock(return_value=_result(mappings=[]))  # hidden-team settings: none
    async with client_for_user(regular_user) as client:
        reveal = await client.get("/api/keys/hash/reveal")
        listed = await client.get("/api/keys")
    assert reveal.status_code == 200
    claims = _claims("sk-" + reveal.json()["key"])
    assert claims == {
        "keyId": 10042,
        "prjId": None,
        "keyType": "USR",
        "regUserId": regular_user.user_id,
        "iat": 1700000000,
    }
    assert listed.status_code == 200
    assert listed.json()["keys"][0]["personal"] is True and listed.json()["keys"][0]["key_alias"] == "mine"
