"""Tests for team join request workflow endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_join_request(user_client: AsyncClient, mock_litellm, mock_db):
    """Successfully create a join request for a team user is not in."""
    # Mock: no existing pending request
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=mock_result)

    # Mock: user not already a member (team-2, user001 not in members)
    mock_litellm.get_team_info.return_value = {
        "team_info": {
            "team_id": "team-2",
            "team_alias": "Beta Team",
            "members": ["user002"],
            "admins": ["admin001"],
        },
    }

    with patch("app.api.team_requests.send_slack_notification", new_callable=AsyncMock):
        resp = await user_client.post(
            "/api/team-requests",
            json={
                "team_id": "team-2",
                "message": "I'd like to join Beta Team",
            },
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["team_id"] == "team-2"
    assert data["status"] == "pending"
    mock_db.add.assert_called_once()


@pytest.mark.asyncio
async def test_create_join_request_duplicate_rejected(user_client: AsyncClient, mock_db):
    """Duplicate pending request returns 409."""
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = MagicMock()  # existing request
    mock_db.execute = AsyncMock(return_value=mock_result)

    resp = await user_client.post("/api/team-requests", json={"team_id": "team-2"})
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_create_join_request_already_member(user_client: AsyncClient, mock_litellm, mock_db):
    """Cannot request to join a team you're already in."""
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=mock_result)

    mock_litellm.get_team_info.return_value = {
        "team_info": {
            "team_id": "team-1",
            "team_alias": "Alpha Team",
            "members": ["user001", "user002"],
            "admins": ["admin001"],
        },
    }

    resp = await user_client.post("/api/team-requests", json={"team_id": "team-1"})
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_list_own_requests(user_client: AsyncClient, mock_db):
    """Regular user sees only their own requests."""
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute = AsyncMock(return_value=mock_result)

    resp = await user_client.get("/api/team-requests")
    assert resp.status_code == 200
    assert "requests" in resp.json()


@pytest.mark.asyncio
async def test_list_all_requests_super_user(admin_client: AsyncClient, mock_db):
    """Super user can see all requests."""
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute = AsyncMock(return_value=mock_result)

    resp = await admin_client.get("/api/team-requests")
    assert resp.status_code == 200
