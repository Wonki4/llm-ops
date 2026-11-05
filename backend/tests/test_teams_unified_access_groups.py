"""Teams carry the LiteLLM unified access groups attached to them (``access_group_ids``).

When a team's own ``models`` deny a model, LiteLLM falls back to the
``access_model_names`` of these groups (``can_team_access_model``), so the
portal shows those models as the team's too.
"""

import pytest
from httpx import AsyncClient

from app.db.session import get_db, get_litellm_db
from app.main import app

TEAM_ROW = {
    "team_alias": "Alpha Team",
    "max_budget": None,
    "spend": 0.0,
    "budget_duration": None,
    "budget_reset_at": None,
    "models": ["gpt-4o-mini"],
    "members": [],
    "admins": [],
    "metadata": {},
}

GROUP_ROWS = [
    {"team_id": "team-1", "access_group_id": "ag-1", "access_group_name": "Research",
     "access_model_names": ["claude-sonnet", "gpt-4o"]},
    {"team_id": "team-1", "access_group_id": "ag-2", "access_group_name": "Vision", "access_model_names": None},
]


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def __iter__(self):
        return iter(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return None


class _FakeSession:
    """Answers by SQL text: team rows, unified access group rows, nothing for the rest."""

    def __init__(self, teams: list[dict]):
        self.teams = teams
        self.group_queries: list[tuple[str, dict]] = []

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        if '"LiteLLM_AccessGroupTable"' in sql:
            self.group_queries.append((sql, params))
            return _Result([r for r in GROUP_ROWS if r["team_id"] in params["team_ids"]])
        if 'FROM "LiteLLM_TeamTable"' in sql:
            return _Result(self.teams)
        return _Result([])


def _use(session: _FakeSession) -> None:
    app.dependency_overrides[get_litellm_db] = lambda: session
    app.dependency_overrides[get_db] = lambda: session


@pytest.mark.asyncio
async def test_my_teams_carry_their_unified_access_groups(user_client: AsyncClient):
    session = _FakeSession([{**TEAM_ROW, "team_id": "team-1"}, {**TEAM_ROW, "team_id": "team-2"}])
    _use(session)
    resp = await user_client.get("/api/teams")
    assert resp.status_code == 200
    teams = {t["team_id"]: t for t in resp.json()["teams"]}
    assert teams["team-1"]["unified_access_groups"] == [
        {"access_group_id": "ag-1", "access_group_name": "Research", "models": ["claude-sonnet", "gpt-4o"]},
        {"access_group_id": "ag-2", "access_group_name": "Vision", "models": []},
    ]
    assert teams["team-2"]["unified_access_groups"] == []
    ((sql, params),) = session.group_queries  # one query for all the teams
    assert params == {"team_ids": ["team-1", "team-2"]}
    # the same link LiteLLM's team model check follows
    assert "ANY(t.access_group_ids)" in sql and "access_model_names" in sql


@pytest.mark.asyncio
async def test_no_teams_skip_the_group_query(user_client: AsyncClient):
    session = _FakeSession([])
    _use(session)
    resp = await user_client.get("/api/teams")
    assert resp.status_code == 200
    assert resp.json()["teams"] == []
    assert session.group_queries == []


@pytest.mark.asyncio
async def test_team_detail_carries_its_unified_access_groups(user_client: AsyncClient):
    session = _FakeSession([{**TEAM_ROW, "team_id": "team-1"}])
    _use(session)
    resp = await user_client.get("/api/teams/team-1")
    assert resp.status_code == 200
    groups = resp.json()["team"]["unified_access_groups"]
    assert [g["access_group_name"] for g in groups] == ["Research", "Vision"]
    ((_, params),) = session.group_queries
    assert params == {"team_ids": ["team-1"]}
