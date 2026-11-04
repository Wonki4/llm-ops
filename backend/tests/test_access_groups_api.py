"""Admin model access groups: group CRUD through LiteLLM and bulk user grants (mock DBs)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.db.session import get_litellm_db
from app.main import app

URL = "/api/admin/access-groups"

MODEL_INFO = [
    {"model_name": "glm", "model_info": {"db_model": True, "access_groups": ["reasoning"]}},
    {"model_name": "gpt-oss", "model_info": {"db_model": True, "access_groups": ["reasoning"]}},
    {"model_name": "qwen", "model_info": {"db_model": True, "access_groups": []}},
    {"model_name": "cfg-model", "model_info": {"db_model": False, "access_groups": ["legacy"]}},
    # one model name, a config deployment and a DB deployment: the config one keeps its group
    {"model_name": "mixed", "model_info": {"db_model": False, "access_groups": ["legacy"]}},
    {"model_name": "mixed", "model_info": {"db_model": True}},
]


def _rows(rows):
    r = MagicMock()
    r.mappings.return_value.all.return_value = rows
    r.mappings.return_value.__iter__.side_effect = lambda: iter(rows)
    return r


class FakeLiteLLMDB:
    """LiteLLM_UserTable rows as {user_id: models}; answers the queries the router makes."""

    def __init__(self, users: dict[str, list[str]], personal_keys: dict[str, int] | None = None):
        self.users = users
        self.personal_keys = personal_keys or {}

    async def execute(self, stmt, params=None):
        sql, p = str(stmt), params or {}
        if "unnest(models)" in sql:
            counts: dict[str, int] = {}
            for models in self.users.values():
                for m in set(models):
                    counts[m] = counts.get(m, 0) + 1
            return _rows([{"entry": k, "n": v} for k, v in counts.items()])
        if "LiteLLM_VerificationToken" in sql:
            return _rows([{"user_id": u, "n": n} for u, n in self.personal_keys.items() if u in p["ids"]])
        if "&&" in sql:
            hits = [{"user_id": u, "models": m} for u, m in self.users.items() if set(m) & set(p["names"])]
            return _rows(sorted(hits, key=lambda r: r["user_id"]))
        if "= ANY(models)" in sql:
            hits = [{"user_id": u, "models": m} for u, m in self.users.items() if p["group"] in m]
            return _rows(sorted(hits, key=lambda r: r["user_id"]))
        if "user_id = ANY(" in sql:
            return _rows([{"user_id": u, "models": self.users[u]} for u in p["ids"] if u in self.users])
        raise AssertionError(f"unexpected SQL: {sql}")


PORTAL_USERS = {
    "USER001": {"display_name": "홍길동", "email": "u1@example.com"},
    "USER002": {"display_name": "김영희", "email": "u2@example.com"},
}


def _portal_db(mock_db, users=PORTAL_USERS):
    async def execute(stmt, params=None):
        ids = (params or {}).get("ids", [])
        return _rows([{"user_id": u, **users[u]} for u in ids if u in users])

    mock_db.execute = AsyncMock(side_effect=execute)
    return mock_db


@pytest.fixture
def env(mock_db, mock_litellm):
    """(litellm db holder, litellm client): users USER001 in reasoning, USER002 custom, USER003 all."""
    holder = {
        "db": FakeLiteLLMDB(
            {
                "USER001": ["reasoning"],
                "USER002": ["gpt-oss"],
                "USER003": ["all-proxy-models", "reasoning"],
            },
            personal_keys={"USER001": 2},
        )
    }
    app.dependency_overrides[get_litellm_db] = lambda: holder["db"]
    _portal_db(mock_db)
    mock_litellm.get_model_info = AsyncMock(return_value=MODEL_INFO)
    for name in ("create_access_group", "update_access_group", "delete_access_group", "update_user", "create_user"):
        setattr(mock_litellm, name, AsyncMock(return_value={}))
    yield holder, mock_litellm
    app.dependency_overrides.pop(get_litellm_db, None)


# ─── listing ────────────────────────────────────────────────────────────────


async def test_list_groups_models_and_user_counts(client_for_user, super_user, env):
    async with client_for_user(super_user) as client:
        resp = await client.get(URL)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    groups = {g["name"]: g for g in data["groups"]}
    assert list(groups) == ["legacy", "reasoning"]
    assert groups["reasoning"] == {
        "name": "reasoning",
        "models": [{"model_name": "glm", "locked": False}, {"model_name": "gpt-oss", "locked": False}],
        "locked": False,
        "user_count": 2,
    }
    # config deployments carry "legacy": those memberships cannot be edited, the group not deleted
    assert groups["legacy"]["models"] == [
        {"model_name": "cfg-model", "locked": True},
        {"model_name": "mixed", "locked": True},
    ]
    assert groups["legacy"]["locked"] is True and groups["legacy"]["user_count"] == 0
    models = {m["model_name"]: m for m in data["models"]}
    assert models["qwen"] == {"model_name": "qwen", "editable": True, "access_groups": []}
    assert models["cfg-model"]["editable"] is False
    assert models["mixed"] == {"model_name": "mixed", "editable": True, "access_groups": ["legacy"]}


async def test_access_groups_are_super_user_only(client_for_user, regular_user, env):
    async with client_for_user(regular_user) as client:
        assert (await client.get(URL)).status_code == 403
        assert (await client.post(f"{URL}/reasoning/users", json={"user_ids": ["user009"]})).status_code == 403


async def test_group_members(client_for_user, super_user, env):
    async with client_for_user(super_user) as client:
        resp = await client.get(f"{URL}/reasoning/users")
    assert resp.status_code == 200, resp.text
    assert resp.json()["users"] == [
        {
            "user_id": "USER001",
            "display_name": "홍길동",
            "email": "u1@example.com",
            "logged_in": True,
            "personal_access": "custom",
            "personal_key_count": 2,
        },
        {
            "user_id": "USER003",
            "display_name": None,
            "email": None,
            "logged_in": False,
            "personal_access": "all",
            "personal_key_count": 0,
        },
    ]


async def test_users_who_can_call_a_model(client_for_user, super_user, env):
    async with client_for_user(super_user) as client:
        resp = await client.get(f"{URL}/model-users", params={"model": "gpt-oss"})
    assert resp.status_code == 200, resp.text
    via = {u["user_id"]: u["via"] for u in resp.json()["users"]}
    assert via == {"USER001": ["reasoning"], "USER002": ["direct"], "USER003": ["all", "reasoning"]}


# ─── group CRUD ─────────────────────────────────────────────────────────────


async def test_create_group(client_for_user, super_user, env):
    _, litellm = env
    async with client_for_user(super_user) as client:
        resp = await client.post(URL, json={"name": "vision", "model_names": ["qwen", "mixed"]})
    assert resp.status_code == 200, resp.text
    litellm.create_access_group.assert_awaited_once_with("vision", ["qwen", "mixed"])


@pytest.mark.parametrize(
    "body, status, match",
    [
        ({"name": "reasoning", "model_names": ["qwen"]}, 409, "already exists"),
        ({"name": "glm", "model_names": ["qwen"]}, 400, "model"),
        ({"name": "has space", "model_names": ["qwen"]}, 400, "group name"),
        ({"name": "vision", "model_names": []}, 400, "at least one"),
        ({"name": "vision", "model_names": ["cfg-model"]}, 400, "cfg-model"),
        ({"name": "vision", "model_names": ["nope"]}, 400, "nope"),
    ],
)
async def test_create_group_rejects(client_for_user, super_user, env, body, status, match):
    _, litellm = env
    async with client_for_user(super_user) as client:
        resp = await client.post(URL, json=body)
    assert resp.status_code == status and match in resp.text, resp.text
    litellm.create_access_group.assert_not_awaited()


async def test_update_group_models(client_for_user, super_user, env):
    _, litellm = env
    async with client_for_user(super_user) as client:
        resp = await client.put(f"{URL}/reasoning", json={"model_names": ["glm", "qwen"]})
    assert resp.status_code == 200, resp.text
    litellm.update_access_group.assert_awaited_once_with("reasoning", ["glm", "qwen"])


async def test_config_only_group_gets_db_models_through_create(client_for_user, super_user, env):
    """LiteLLM only knows groups on DB models, so a group carried by config models alone
    is "new" to /access_group: adding DB models goes through create, the locked ones stay."""
    _, litellm = env
    async with client_for_user(super_user) as client:
        resp = await client.put(f"{URL}/legacy", json={"model_names": ["cfg-model", "mixed", "qwen"]})
    assert resp.status_code == 200, resp.text
    litellm.create_access_group.assert_awaited_once_with("legacy", ["mixed", "qwen"])
    litellm.update_access_group.assert_not_awaited()


@pytest.mark.parametrize(
    "group, models, status, match",
    [
        ("nope", ["glm"], 404, "not found"),
        ("legacy", ["mixed"], 400, "cfg-model"),  # a config model cannot leave
        ("reasoning", [], 400, "at least one"),
        ("reasoning", ["cfg-model"], 400, "cfg-model"),
    ],
)
async def test_update_group_rejects(client_for_user, super_user, env, group, models, status, match):
    _, litellm = env
    async with client_for_user(super_user) as client:
        resp = await client.put(f"{URL}/{group}", json={"model_names": models})
    assert resp.status_code == status and match in resp.text, resp.text
    litellm.update_access_group.assert_not_awaited()
    litellm.create_access_group.assert_not_awaited()


async def test_delete_group_revokes_it_from_users(client_for_user, super_user, env):
    _, litellm = env
    async with client_for_user(super_user) as client:
        resp = await client.delete(f"{URL}/reasoning")
    assert resp.status_code == 200, resp.text
    litellm.delete_access_group.assert_awaited_once_with("reasoning")
    calls = {c.args[0]: c.kwargs["models"] for c in litellm.update_user.await_args_list}
    assert calls == {"USER001": ["no-default-models"], "USER003": ["all-proxy-models"]}
    assert resp.json()["revoked_users"] == 2


async def test_group_on_config_models_cannot_be_deleted(client_for_user, super_user, env):
    _, litellm = env
    async with client_for_user(super_user) as client:
        resp = await client.delete(f"{URL}/legacy")
    assert resp.status_code == 409 and "config" in resp.text
    litellm.delete_access_group.assert_not_awaited()


# ─── user grants ────────────────────────────────────────────────────────────


async def test_add_users_dry_run_classifies_without_writing(client_for_user, super_user, env):
    _, litellm = env
    ids = ["user001", "user002", " user002 ", "user009", "홍길동", ""]
    async with client_for_user(super_user) as client:
        resp = await client.post(f"{URL}/reasoning/users", json={"user_ids": ids, "dry_run": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["results"] == [
        {"input": "user001", "user_id": "USER001", "status": "already", "logged_in": True, "display_name": "홍길동"},
        {"input": "user002", "user_id": "USER002", "status": "added", "logged_in": True, "display_name": "김영희"},
        {"input": "user009", "user_id": "USER009", "status": "pregranted", "logged_in": False, "display_name": None},
        {"input": "홍길동", "user_id": None, "status": "invalid", "logged_in": False, "display_name": None},
    ]
    litellm.update_user.assert_not_awaited()
    litellm.create_user.assert_not_awaited()


async def test_add_users_writes_grants_and_pregrants_new_users(client_for_user, super_user, env):
    _, litellm = env
    async with client_for_user(super_user) as client:
        resp = await client.post(f"{URL}/reasoning/users", json={"user_ids": ["user002", "user009"]})
    assert resp.status_code == 200, resp.text
    litellm.update_user.assert_awaited_once_with("USER002", models=["gpt-oss", "reasoning"])
    litellm.create_user.assert_awaited_once_with(
        "USER009", None, models=["reasoning"], metadata={"portal_pregrant": True}
    )
    assert [r["status"] for r in resp.json()["results"]] == ["added", "pregranted"]


async def test_add_users_reports_a_failed_write_and_continues(client_for_user, super_user, env):
    _, litellm = env
    litellm.update_user = AsyncMock(side_effect=RuntimeError("litellm down"))
    async with client_for_user(super_user) as client:
        resp = await client.post(f"{URL}/reasoning/users", json={"user_ids": ["user002", "user009"]})
    assert resp.status_code == 200, resp.text
    assert [r["status"] for r in resp.json()["results"]] == ["error", "pregranted"]


async def test_add_users_to_unknown_group(client_for_user, super_user, env):
    async with client_for_user(super_user) as client:
        resp = await client.post(f"{URL}/nope/users", json={"user_ids": ["user001"]})
    assert resp.status_code == 404


async def test_remove_users(client_for_user, super_user, env):
    _, litellm = env
    async with client_for_user(super_user) as client:
        resp = await client.post(f"{URL}/reasoning/users/remove", json={"user_ids": ["user001", "user002"]})
    assert resp.status_code == 200, resp.text
    litellm.update_user.assert_awaited_once_with("USER001", models=["no-default-models"])
    assert [r["status"] for r in resp.json()["results"]] == ["removed", "not_member"]
