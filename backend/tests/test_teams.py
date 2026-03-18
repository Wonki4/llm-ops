"""Tests for team endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_my_teams(user_client: AsyncClient, mock_litellm):
    resp = await user_client.get("/api/teams")
    assert resp.status_code == 200
    data = resp.json()
    assert "teams" in data
    assert len(data["teams"]) == 1
    assert data["teams"][0]["team_alias"] == "Alpha Team"


@pytest.mark.asyncio
async def test_discover_teams_marks_membership(user_client: AsyncClient, mock_litellm):
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
async def test_get_team_detail(user_client: AsyncClient, mock_litellm):
    resp = await user_client.get("/api/teams/team-1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["team"]["team_alias"] == "Alpha Team"
    assert data["team"]["spend"] == 120.0
    assert isinstance(data["my_keys"], list)
    # user001 is not in admins list (admin001 is)
    assert data["is_admin"] is False


@pytest.mark.asyncio
async def test_get_team_detail_admin_flag(admin_client: AsyncClient, mock_litellm):
    resp = await admin_client.get("/api/teams/team-1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_admin"] is True
