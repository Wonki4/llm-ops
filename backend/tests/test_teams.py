"""Tests for team endpoints."""

from unittest.mock import AsyncMock

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


@pytest.mark.asyncio
async def test_change_member_budget_delegates_to_member_update(admin_client: AsyncClient, mock_litellm, mock_db):
    """Per-member budget + TPM/RPM go through LiteLLM /team/member_update.

    LiteLLM clone-on-writes a dedicated budget row for the member, so we no
    longer touch the BudgetTable with raw SQL (the old path reused any row with
    a matching amount, silently sharing budgets between members).
    """
    from app.db.session import get_litellm_db
    from app.main import app

    app.dependency_overrides[get_litellm_db] = lambda: mock_db
    mock_litellm.update_team_member = AsyncMock(return_value={"status": "ok"})

    resp = await admin_client.put(
        "/api/teams/team-1/members/user002/budget",
        json={"max_budget": 100.0, "tpm_limit": 5000, "rpm_limit": 60},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tpm_limit"] == 5000 and body["rpm_limit"] == 60
    mock_litellm.update_team_member.assert_awaited_once_with(
        "team-1",
        "user002",
        max_budget_in_team=100.0,
        tpm_limit=5000,
        rpm_limit=60,
    )
    # Super-user short-circuits require_team_admin, and the budget write is fully
    # delegated — so the portal performs no raw SQL on the LiteLLM DB.
    mock_db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_change_member_budget_omits_unset_limits(admin_client: AsyncClient, mock_litellm, mock_db):
    """Budget-only edit sends tpm/rpm as None so LiteLLM leaves them untouched."""
    from app.db.session import get_litellm_db
    from app.main import app

    app.dependency_overrides[get_litellm_db] = lambda: mock_db
    mock_litellm.update_team_member = AsyncMock(return_value={"status": "ok"})

    resp = await admin_client.put(
        "/api/teams/team-1/members/user002/budget",
        json={"max_budget": 250.0},
    )

    assert resp.status_code == 200, resp.text
    mock_litellm.update_team_member.assert_awaited_once_with(
        "team-1",
        "user002",
        max_budget_in_team=250.0,
        tpm_limit=None,
        rpm_limit=None,
    )


@pytest.mark.asyncio
async def test_member_usage_group_by_model_group_uses_coalesce(admin_client: AsyncClient, mock_litellm, mock_db):
    """group_by=model_group buckets by the public group, falling back to model
    (COALESCE(NULLIF(model_group,''), model)) so there are no (unknown) rows."""
    from app.db.session import get_litellm_db
    from app.main import app

    captured: list[str] = []

    async def fake_execute(statement, params=None):
        captured.append(str(statement))
        if len(captured) == 1:
            return _FakeMappingsResult([{"token": "sk-x"}])  # member's keys
        return _FakeMappingsResult([])  # grouped rows

    mock_db.execute = fake_execute
    app.dependency_overrides[get_litellm_db] = lambda: mock_db

    resp = await admin_client.get(
        "/api/teams/team-1/usage/user002/by-model"
        "?start_date=2026-01-01&end_date=2026-12-31&group_by=model_group"
    )

    assert resp.status_code == 200, resp.text
    assert any("COALESCE(NULLIF(model_group, '')" in s for s in captured), captured


@pytest.mark.asyncio
async def test_member_usage_default_groups_by_plain_model(admin_client: AsyncClient, mock_litellm, mock_db):
    """Default (group_by=model) groups by the raw model column, not model_group."""
    from app.db.session import get_litellm_db
    from app.main import app

    captured: list[str] = []

    async def fake_execute(statement, params=None):
        captured.append(str(statement))
        if len(captured) == 1:
            return _FakeMappingsResult([{"token": "sk-x"}])
        return _FakeMappingsResult([])

    mock_db.execute = fake_execute
    app.dependency_overrides[get_litellm_db] = lambda: mock_db

    resp = await admin_client.get(
        "/api/teams/team-1/usage/user002/by-model?start_date=2026-01-01&end_date=2026-12-31"
    )

    assert resp.status_code == 200, resp.text
    grouped_sql = captured[1]
    assert "GROUP BY model " in grouped_sql and "COALESCE" not in grouped_sql, grouped_sql
