"""Admin personal-access PATCH + /api/me exposure of the grant (mock DBs)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.db.session import get_litellm_db
from app.main import app


def _result(*, scalar=None, first=None, mappings=None):
    r = MagicMock()
    r.scalar.return_value = scalar
    r.scalar_one_or_none.return_value = scalar
    r.mappings.return_value.first.return_value = first
    r.mappings.return_value.__iter__.return_value = iter(mappings or [])
    return r


MODEL_INFO = [
    {"model_name": "glm", "model_info": {"access_groups": ["beta-models"]}},
    {"model_name": "gpt-4o", "model_info": {}},
]


@pytest.fixture
def litellm_db():
    holder = {"db": AsyncMock()}
    app.dependency_overrides[get_litellm_db] = lambda: holder["db"]
    yield holder
    app.dependency_overrides.pop(get_litellm_db, None)


def _litellm_db_with(*, user_exists=True, personal_tokens=()):
    db = AsyncMock()

    async def execute(stmt, params=None):
        sql = str(stmt)
        if "LiteLLM_UserTable" in sql:
            return _result(scalar=1 if user_exists else None)
        if "team_id IS NULL" in sql:
            return _result(mappings=[{"token": t} for t in personal_tokens])
        return _result()

    db.execute = AsyncMock(side_effect=execute)
    return db


def _portal_db(mock_db, *, user_email="u@example.com"):
    mock_db.execute = AsyncMock(return_value=_result(first={"email": user_email} if user_email else None))
    return mock_db


async def test_custom_grant_validates_and_updates_the_user_row(
    client_for_user, super_user, mock_db, mock_litellm, litellm_db
):
    _portal_db(mock_db)
    litellm_db["db"] = _litellm_db_with()
    mock_litellm.get_model_info = AsyncMock(return_value=MODEL_INFO)
    mock_litellm.update_user = AsyncMock(return_value={})
    async with client_for_user(super_user) as client:
        resp = await client.patch(
            "/api/admin/users/user001/personal-access",
            json={"access": "custom", "models": ["beta-models", "gpt-4o"], "tpm_limit": 20000, "max_budget": 50},
        )
    assert resp.status_code == 200, resp.text
    mock_litellm.update_user.assert_awaited_once_with(
        "user001", models=["beta-models", "gpt-4o"], max_budget=50.0, tpm_limit=20000
    )
    body = resp.json()
    assert body["personal_access"] == "custom" and body["models"] == ["beta-models", "gpt-4o"]
    assert body["tpm_limit"] == 20000 and "rpm_limit" not in body


async def test_custom_grant_rejects_unknown_names(client_for_user, super_user, mock_db, mock_litellm, litellm_db):
    _portal_db(mock_db)
    litellm_db["db"] = _litellm_db_with()
    mock_litellm.get_model_info = AsyncMock(return_value=MODEL_INFO)
    mock_litellm.update_user = AsyncMock()
    async with client_for_user(super_user) as client:
        resp = await client.patch(
            "/api/admin/users/user001/personal-access", json={"access": "custom", "models": ["nope"]}
        )
        empty = await client.patch("/api/admin/users/user001/personal-access", json={"access": "custom", "models": []})
    assert resp.status_code == 400 and "nope" in resp.text
    assert empty.status_code == 400
    mock_litellm.update_user.assert_not_awaited()


async def test_all_and_none_write_sentinels_and_none_can_delete_personal_keys(
    client_for_user, super_user, mock_db, mock_litellm, litellm_db
):
    _portal_db(mock_db)
    litellm_db["db"] = _litellm_db_with(personal_tokens=("h1", "h2"))
    mock_litellm.update_user = AsyncMock(return_value={})
    mock_litellm.delete_key = AsyncMock(return_value={})
    async with client_for_user(super_user) as client:
        all_ = await client.patch("/api/admin/users/user001/personal-access", json={"access": "all"})
        none = await client.patch(
            "/api/admin/users/user001/personal-access", json={"access": "none", "delete_personal_keys": True}
        )
    assert all_.status_code == 200 and all_.json()["models"] == ["all-proxy-models"]
    assert none.status_code == 200 and none.json()["models"] == ["no-default-models"]
    assert none.json()["deleted_personal_keys"] == 2
    assert [c.args[0] for c in mock_litellm.delete_key.await_args_list] == ["h1", "h2"]


async def test_missing_litellm_row_is_created_with_the_grant(
    client_for_user, super_user, mock_db, mock_litellm, litellm_db
):
    _portal_db(mock_db, user_email="new@example.com")
    litellm_db["db"] = _litellm_db_with(user_exists=False)
    mock_litellm.create_user = AsyncMock(return_value={})
    mock_litellm.update_user = AsyncMock()
    async with client_for_user(super_user) as client:
        resp = await client.patch("/api/admin/users/newbie/personal-access", json={"access": "all", "rpm_limit": 5})
    assert resp.status_code == 200, resp.text
    mock_litellm.create_user.assert_awaited_once_with(
        "newbie", "new@example.com", models=["all-proxy-models"], rpm_limit=5
    )
    mock_litellm.update_user.assert_not_awaited()


async def test_unknown_portal_user_404(client_for_user, super_user, mock_db, mock_litellm, litellm_db):
    _portal_db(mock_db, user_email=None)
    async with client_for_user(super_user) as client:
        resp = await client.patch("/api/admin/users/ghost/personal-access", json={"access": "all"})
    assert resp.status_code == 404


async def test_me_exposes_the_grant_and_whether_personal_keys_can_be_minted(
    client_for_user, regular_user, mock_db, mock_litellm, litellm_db
):
    mock_db.execute = AsyncMock(return_value=_result(scalar="true"))  # beta switch on
    db = AsyncMock()
    user_row = {
        "spend": 1.5,
        "max_budget": 10,
        "models": ["beta-models"],
        "tpm_limit": 20000,
        "rpm_limit": None,
        "budget_duration": "30d",
    }

    async def execute(stmt, params=None):
        sql = str(stmt)
        if "LiteLLM_UserTable" in sql:
            return _result(first=user_row)
        return _result(scalar=None)  # not a team admin

    db.execute = AsyncMock(side_effect=execute)
    litellm_db["db"] = db
    async with client_for_user(regular_user) as client:
        resp = await client.get("/api/me")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["models"] == ["beta-models"] and body["personal_access"] == "custom"
    assert body["personal_keys_beta_enabled"] is True and body["personal_keys_enabled"] is True
    assert body["tpm_limit"] == 20000 and body["budget_duration"] == "30d" and body["role"] == "user"


async def test_null_limits_are_cleared_directly_since_litellm_drops_nulls(
    client_for_user, super_user, mock_db, mock_litellm, litellm_db
):
    _portal_db(mock_db)
    db = _litellm_db_with()
    db.commit = AsyncMock()
    litellm_db["db"] = db
    mock_litellm.update_user = AsyncMock(return_value={})
    async with client_for_user(super_user) as client:
        resp = await client.patch(
            "/api/admin/users/user001/personal-access",
            json={"access": "all", "tpm_limit": None, "max_budget": None, "rpm_limit": 5},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["tpm_limit"] is None and resp.json()["max_budget"] is None and resp.json()["rpm_limit"] == 5
    updates = [c for c in db.execute.await_args_list if "UPDATE" in str(c.args[0])]
    assert len(updates) == 1
    assert "max_budget = NULL, tpm_limit = NULL" in str(updates[0].args[0]) and updates[0].args[1] == {"uid": "user001"}
    mock_litellm.update_user.assert_awaited_once_with("user001", models=["all-proxy-models"], rpm_limit=5)
