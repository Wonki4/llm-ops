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


@pytest.mark.asyncio
async def test_team_usage_returns_token_breakdown(admin_client: AsyncClient, mock_litellm, mock_db):
    """Usage rows/totals/series split tokens into input/output + cache-read."""
    from app.db.session import get_litellm_db
    from app.main import app

    async def fake_execute(statement, params=None):
        sql = str(statement)
        if "LiteLLM_VerificationToken" in sql:
            return _FakeMappingsResult([{"token": "sk-x", "user_id": "user002"}])
        if "GROUP BY api_key" in sql:
            return _FakeMappingsResult(
                [
                    {
                        "api_key": "sk-x",
                        "total_tokens": 165000,
                        "input_tokens": 120000,
                        "output_tokens": 45000,
                        "cache_read_tokens": 30000,
                        "api_requests": 10,
                        "spend": 1.5,
                    }
                ]
            )
        return _FakeMappingsResult(
            [
                {
                    "bucket": "2026-07-01",
                    "total_tokens": 165000,
                    "input_tokens": 120000,
                    "output_tokens": 45000,
                    "cache_read_tokens": 30000,
                    "api_requests": 10,
                    "spend": 1.5,
                }
            ]
        )

    mock_db.execute = fake_execute
    app.dependency_overrides[get_litellm_db] = lambda: mock_db

    resp = await admin_client.get("/api/teams/team-1/usage?start_date=2026-07-01&end_date=2026-07-02")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    m = body["members"][0]
    assert (m["input_tokens"], m["output_tokens"], m["cache_read_tokens"]) == (120000, 45000, 30000)
    assert m["total_tokens"] == m["input_tokens"] + m["output_tokens"]
    t = body["totals"]
    assert (t["input_tokens"], t["output_tokens"], t["cache_read_tokens"]) == (120000, 45000, 30000)
    s = body["series"][0]
    assert (s["input_tokens"], s["output_tokens"], s["cache_read_tokens"]) == (120000, 45000, 30000)


@pytest.mark.asyncio
async def test_member_usage_by_model_returns_token_breakdown(admin_client: AsyncClient, mock_litellm, mock_db):
    from app.db.session import get_litellm_db
    from app.main import app

    async def fake_execute(statement, params=None):
        sql = str(statement)
        if "LiteLLM_VerificationToken" in sql:
            return _FakeMappingsResult([{"token": "sk-x"}])
        return _FakeMappingsResult(
            [
                {
                    "label": "gpt-4o",
                    "total_tokens": 1100,
                    "input_tokens": 1000,
                    "output_tokens": 100,
                    "cache_read_tokens": 400,
                    "api_requests": 3,
                    "spend": 0.5,
                }
            ]
        )

    mock_db.execute = fake_execute
    app.dependency_overrides[get_litellm_db] = lambda: mock_db

    resp = await admin_client.get(
        "/api/teams/team-1/usage/user002/by-model?start_date=2026-01-01&end_date=2026-12-31"
    )
    assert resp.status_code == 200, resp.text
    mm = resp.json()["models"][0]
    assert (mm["input_tokens"], mm["output_tokens"], mm["cache_read_tokens"]) == (1000, 100, 400)


@pytest.mark.asyncio
async def test_team_usage_member_sees_only_own_usage(user_client: AsyncClient, mock_litellm, mock_db):
    """A regular team member can open the usage tab, scoped to their own keys."""
    from app.db.session import get_litellm_db
    from app.main import app

    async def fake_execute(statement, params=None):
        sql = str(statement)
        if "LiteLLM_TeamTable" in sql:
            return _FakeMappingsResult([{"admins": ["someone-else"], "members": ["user001", "user002"]}])
        if "LiteLLM_VerificationToken" in sql:
            return _FakeMappingsResult(
                [
                    {"token": "sk-mine", "user_id": "user001"},
                    {"token": "sk-other", "user_id": "user002"},
                ]
            )
        assert params["tokens"] == ["sk-mine"], params["tokens"]  # self-scoped aggregation
        if "GROUP BY api_key" in sql:
            return _FakeMappingsResult(
                [
                    {
                        "api_key": "sk-mine",
                        "total_tokens": 100,
                        "input_tokens": 80,
                        "output_tokens": 20,
                        "cache_read_tokens": 10,
                        "api_requests": 2,
                        "spend": 0.5,
                    }
                ]
            )
        return _FakeMappingsResult([])

    mock_db.execute = fake_execute
    app.dependency_overrides[get_litellm_db] = lambda: mock_db

    resp = await user_client.get("/api/teams/team-1/usage?start_date=2026-07-01&end_date=2026-07-02")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [m["user_id"] for m in body["members"]] == ["user001"]
    assert body["totals"]["total_tokens"] == 100


@pytest.mark.asyncio
async def test_team_usage_non_member_forbidden(user_client: AsyncClient, mock_litellm, mock_db):
    from app.db.session import get_litellm_db
    from app.main import app

    async def fake_execute(statement, params=None):
        return _FakeMappingsResult([{"admins": [], "members": ["user002"]}])

    mock_db.execute = fake_execute
    app.dependency_overrides[get_litellm_db] = lambda: mock_db

    resp = await user_client.get("/api/teams/team-1/usage?start_date=2026-07-01&end_date=2026-07-02")
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_team_usage_team_admin_sees_all_members(user_client: AsyncClient, mock_litellm, mock_db):
    """A team admin (not a super user) still gets every member's usage."""
    from app.db.session import get_litellm_db
    from app.main import app

    async def fake_execute(statement, params=None):
        sql = str(statement)
        if "LiteLLM_TeamTable" in sql:
            return _FakeMappingsResult([{"admins": ["user001"], "members": ["user001", "user002"]}])
        if "LiteLLM_VerificationToken" in sql:
            return _FakeMappingsResult(
                [
                    {"token": "sk-mine", "user_id": "user001"},
                    {"token": "sk-other", "user_id": "user002"},
                ]
            )
        if "GROUP BY api_key" in sql:
            assert sorted(params["tokens"]) == ["sk-mine", "sk-other"], params["tokens"]
            return _FakeMappingsResult(
                [
                    {
                        "api_key": "sk-mine",
                        "total_tokens": 100,
                        "input_tokens": 80,
                        "output_tokens": 20,
                        "cache_read_tokens": 10,
                        "api_requests": 2,
                        "spend": 0.5,
                    },
                    {
                        "api_key": "sk-other",
                        "total_tokens": 200,
                        "input_tokens": 150,
                        "output_tokens": 50,
                        "cache_read_tokens": 60,
                        "api_requests": 4,
                        "spend": 1.0,
                    },
                ]
            )
        return _FakeMappingsResult([])

    mock_db.execute = fake_execute
    app.dependency_overrides[get_litellm_db] = lambda: mock_db

    resp = await user_client.get("/api/teams/team-1/usage?start_date=2026-07-01&end_date=2026-07-02")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert {m["user_id"] for m in body["members"]} == {"user001", "user002"}
    assert body["totals"]["total_tokens"] == 300


@pytest.mark.asyncio
async def test_member_usage_by_model_self_allowed_others_forbidden(user_client: AsyncClient, mock_litellm, mock_db):
    from app.db.session import get_litellm_db
    from app.main import app

    async def fake_execute(statement, params=None):
        sql = str(statement)
        if "LiteLLM_TeamTable" in sql:
            return _FakeMappingsResult([{"admins": [], "members": ["user001", "user002"]}])
        if "LiteLLM_VerificationToken" in sql:
            return _FakeMappingsResult([{"token": "sk-mine"}])
        return _FakeMappingsResult([])

    mock_db.execute = fake_execute
    app.dependency_overrides[get_litellm_db] = lambda: mock_db

    ok = await user_client.get(
        "/api/teams/team-1/usage/user001/by-model?start_date=2026-01-01&end_date=2026-12-31"
    )
    assert ok.status_code == 200, ok.text

    denied = await user_client.get(
        "/api/teams/team-1/usage/user002/by-model?start_date=2026-01-01&end_date=2026-12-31"
    )
    assert denied.status_code == 403, denied.text
