"""Tests for team endpoints."""

import pytest
from httpx import AsyncClient


class _FakeMappingsResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def __iter__(self):
        return iter(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


@pytest.mark.asyncio
async def test_list_my_teams(user_client: AsyncClient, mock_litellm, mock_db):
    mock_db.execute.return_value = _FakeMappingsResult(
        [
            {
                "team_id": "team-1",
                "team_alias": "Alpha Team",
                "max_budget": 500.0,
                "spend": 120.0,
                "budget_duration": "30d",
                "budget_reset_at": None,
                "models": ["gpt-4o", "claude-sonnet"],
                "members": ["user001", "user002"],
                "admins": ["admin001"],
            }
        ]
    )
    resp = await user_client.get("/api/teams")
    assert resp.status_code == 200
    data = resp.json()
    assert "teams" in data
    assert len(data["teams"]) == 1
    assert data["teams"][0]["team_alias"] == "Alpha Team"


@pytest.mark.asyncio
async def test_discover_teams_marks_membership(user_client: AsyncClient, mock_litellm, mock_db):
    mock_db.execute.side_effect = [
        _FakeMappingsResult(
            [
                {
                    "team_id": "team-1",
                    "team_alias": "Alpha Team",
                    "max_budget": 500.0,
                    "spend": 120.0,
                    "budget_duration": "30d",
                    "budget_reset_at": None,
                    "models": ["gpt-4o", "claude-sonnet"],
                    "members": ["user001", "user002"],
                    "admins": ["admin001"],
                },
                {
                    "team_id": "team-2",
                    "team_alias": "Beta Team",
                    "max_budget": 200.0,
                    "spend": 50.0,
                    "budget_duration": None,
                    "budget_reset_at": None,
                    "models": ["gpt-4o-mini"],
                    "members": ["user002"],
                    "admins": ["admin001"],
                },
            ]
        ),
        _FakeMappingsResult(
            [
                {"team_id": "team-1"},
            ]
        ),
    ]
    resp = await user_client.get("/api/teams/discover")
    assert resp.status_code == 200
    teams = resp.json()["teams"]
    assert len(teams) == 2

    # user001 is in team-1 but not team-2
    team1 = next(t for t in teams if t["team_id"] == "team-1")
    team2 = next(t for t in teams if t["team_id"] == "team-2")
    assert team1["is_member"] is True
    assert team2["is_member"] is False


@pytest.mark.asyncio
async def test_get_team_detail(user_client: AsyncClient, mock_litellm, mock_db):
    mock_db.execute.side_effect = [
        _FakeMappingsResult(
            [
                {
                    "team_id": "team-1",
                    "team_alias": "Alpha Team",
                    "max_budget": 500.0,
                    "spend": 120.0,
                    "budget_duration": "30d",
                    "budget_reset_at": None,
                    "models": ["gpt-4o", "claude-sonnet"],
                    "members": ["user001", "user002"],
                    "admins": ["admin001"],
                }
            ]
        ),
        _FakeMappingsResult(
            [
                {
                    "token": "sk-abc123xxxxxxxxxxxx",
                    "key_name": None,
                    "key_alias": "user001-team-1",
                    "team_id": "team-1",
                    "user_id": "user001",
                    "spend": 10.0,
                    "max_budget": 50.0,
                    "budget_duration": None,
                    "budget_reset_at": None,
                    "models": ["gpt-4o"],
                    "expires": None,
                    "created_at": None,
                }
            ]
        ),
    ]
    resp = await user_client.get("/api/teams/team-1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["team"]["team_alias"] == "Alpha Team"
    assert data["team"]["spend"] == 120.0
    assert isinstance(data["my_keys"], list)
    # user001 is not in admins list (admin001 is)
    assert data["is_admin"] is False


@pytest.mark.asyncio
async def test_get_team_detail_admin_flag(admin_client: AsyncClient, mock_litellm, mock_db):
    mock_db.execute.side_effect = [
        _FakeMappingsResult(
            [
                {
                    "team_id": "team-1",
                    "team_alias": "Alpha Team",
                    "max_budget": 500.0,
                    "spend": 120.0,
                    "budget_duration": "30d",
                    "budget_reset_at": None,
                    "models": ["gpt-4o", "claude-sonnet"],
                    "members": ["user001", "user002"],
                    "admins": ["admin001"],
                }
            ]
        ),
        _FakeMappingsResult([]),
    ]
    resp = await admin_client.get("/api/teams/team-1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_admin"] is True


@pytest.mark.asyncio
async def test_list_my_teams_sql_includes_usertable_teams(user_client: AsyncClient, mock_db):
    captured = {}

    async def fake_execute(statement, params=None):
        captured["sql"] = str(statement)
        captured["params"] = params
        return _FakeMappingsResult([])

    mock_db.execute.side_effect = fake_execute

    resp = await user_client.get("/api/teams")

    assert resp.status_code == 200
    assert 'FROM "LiteLLM_UserTable" u' in captured["sql"]
    assert "t.team_id = ANY(u.teams)" in captured["sql"]


@pytest.mark.asyncio
async def test_discover_teams_sql_includes_usertable_teams(user_client: AsyncClient, mock_db):
    captured = []

    async def fake_execute(statement, params=None):
        captured.append(str(statement))
        if len(captured) == 1:
            return _FakeMappingsResult([])
        return _FakeMappingsResult([])

    mock_db.execute.side_effect = fake_execute

    resp = await user_client.get("/api/teams/discover")

    assert resp.status_code == 200
    assert 'FROM "LiteLLM_UserTable" WHERE user_id = :user_id' in captured[1]
    assert "UNNEST(COALESCE(teams, ARRAY[]::text[]))" in captured[1]
