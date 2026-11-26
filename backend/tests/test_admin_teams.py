from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.admin_teams import team_access
from app.db.session import get_litellm_db
from app.main import app

ROWS = [
    {"model_name": "shared", "model_info": {"access_groups": ["research"]}},
    {"model_name": "other", "model_info": {}},
]
INHERITED = [{"access_group_id": "ag", "access_group_name": "Inherited", "models": ["shared"]}]


def test_direct_none_with_inheritance_is_not_total_denial():
    data = team_access({"models": ["no-default-models"]}, ROWS, INHERITED)
    assert data["mode"] == "none"
    assert data["effective_models"] == [{"model_name": "shared", "via": ["inherited:Inherited"]}]
    assert team_access({"models": ["no-default-models"]}, ROWS, [])["effective_models"] == []


def test_team_empty_is_litellm_all_and_groups_expand():
    assert len(team_access({"models": []}, ROWS, [])["effective_models"]) == 2
    assert team_access({"models": ["research"]}, ROWS, [])["effective_models"] == [
        {"model_name": "shared", "via": ["group:research"]}
    ]


@pytest.fixture
def grants(mock_litellm):
    teams = {
        "B": {"team_id": "B", "team_alias": "B", "models": ["research"]},
        "C": {"team_id": "C", "team_alias": "C", "models": ["no-default-models"]},
    }

    async def execute(stmt, params):
        result = MagicMock()
        if "LiteLLM_AccessGroupTable" in str(stmt):
            result.mappings.return_value.__iter__.side_effect = lambda: iter([])
        else:
            result.mappings.return_value.first.return_value = teams.get(params["id"])
        return result

    async def update(team_id, **kwargs):
        teams[team_id].update(kwargs)

    db = MagicMock(execute=AsyncMock(side_effect=execute))
    app.dependency_overrides[get_litellm_db] = lambda: db
    mock_litellm.get_model_info = AsyncMock(return_value=ROWS)
    mock_litellm.update_team = AsyncMock(side_effect=update)
    yield teams
    app.dependency_overrides.pop(get_litellm_db, None)


async def test_shared_b_and_denied_c_read_live_grants(admin_client, grants, mock_litellm):
    b = (await admin_client.get("/api/admin/teams/B/access")).json()
    c = (await admin_client.get("/api/admin/teams/C/access")).json()
    assert b["effective_models"][0]["model_name"] == "shared"
    assert not c["effective_models"]
    # Removing the last grant never sends [] (LiteLLM's unrestricted value).
    r = await admin_client.put("/api/admin/teams/B/access", json={"mode": "none", "expected_models": ["research"]})
    assert r.status_code == 200
    mock_litellm.update_team.assert_awaited_once_with("B", models=["no-default-models"])
    assert not (await admin_client.get("/api/admin/teams/B/access")).json()["effective_models"]


async def test_direct_litellm_edit_detected_and_unknown_rejected(admin_client, grants, mock_litellm):
    grants["B"]["models"] = ["other"]
    r = await admin_client.put("/api/admin/teams/B/access", json={"mode": "none", "expected_models": ["research"]})
    assert r.status_code == 409
    r = await admin_client.put(
        "/api/admin/teams/B/access", json={"mode": "custom", "selection": ["typo"], "expected_models": ["other"]}
    )
    assert r.status_code == 400
    mock_litellm.update_team.assert_not_awaited()


async def test_not_team_admin_privilege(user_client, grants, mock_litellm):
    assert (await user_client.get("/api/admin/teams")).status_code == 403
    assert (await user_client.get("/api/admin/teams/B/access")).status_code == 403
    r = await user_client.put("/api/admin/teams/B/access", json={"mode": "all", "expected_models": []})
    assert r.status_code == 403
    mock_litellm.update_team.assert_not_awaited()
