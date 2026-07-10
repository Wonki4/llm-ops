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


@pytest.mark.asyncio
async def test_approve_budget_request_delegates_to_member_update(
    admin_client: AsyncClient, mock_litellm, mock_db
):
    """Budget-increase approval goes through the same LiteLLM /team/member_update
    path as a manual per-member budget change — unified, with clone-on-write so
    two approvals for the same amount never share one budget row (the old
    reuse-by-amount SQL did)."""
    from app.db.models.custom_team_join_request import JoinRequestStatus
    from app.db.session import get_litellm_db
    from app.main import app

    req = MagicMock()
    req.status = JoinRequestStatus.PENDING
    req.request_type = "budget"
    req.team_id = "team-1"
    req.requester_id = "user002"
    req.requested_budget = 300.0
    req.requested_duration_days = None

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = req
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.flush = AsyncMock()

    app.dependency_overrides[get_litellm_db] = lambda: mock_db
    mock_litellm.update_team_member = AsyncMock(return_value={"status": "ok"})

    resp = await admin_client.post(
        "/api/team-requests/00000000-0000-0000-0000-000000000001/approve"
    )

    assert resp.status_code == 200, resp.text
    mock_litellm.update_team_member.assert_awaited_once_with(
        "team-1", "user002", max_budget_in_team=300.0
    )


@pytest.mark.asyncio
async def test_create_budget_request_stores_duration(user_client: AsyncClient, mock_db):
    """Creating a budget request persists the requested duration on the row."""
    from app.db.session import get_litellm_db
    from app.main import app

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_result.mappings.return_value.first.return_value = {"team_alias": "Alpha Team"}
    mock_db.execute = AsyncMock(return_value=mock_result)

    app.dependency_overrides[get_litellm_db] = lambda: mock_db

    resp = await user_client.post(
        "/api/team-requests/budget",
        json={"team_id": "team-1", "requested_budget": 100.0, "requested_duration_days": 30},
    )

    assert resp.status_code == 201, resp.text
    row = mock_db.add.call_args.args[0]
    assert row.requested_duration_days == 30


@pytest.mark.asyncio
async def test_approve_budget_request_with_duration_applies_boost(
    admin_client: AsyncClient, mock_litellm, mock_db
):
    """A pending budget request with a requested duration is applied as a
    time-limited member budget boost (rather than a permanent change)."""
    from datetime import UTC, datetime

    from app.db.models.custom_team_join_request import JoinRequestStatus
    from app.db.session import get_litellm_db
    from app.main import app

    req = MagicMock()
    req.status = JoinRequestStatus.PENDING
    req.request_type = "budget"
    req.team_id = "team-1"
    req.requester_id = "user002"
    req.requested_budget = 100.0
    req.requested_duration_days = 30

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = req
    mock_result.mappings.return_value.first.return_value = {"max_budget": 50.0}
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.flush = AsyncMock()

    app.dependency_overrides[get_litellm_db] = lambda: mock_db

    with patch("app.api.team_requests.apply_member_budget_boost", AsyncMock()) as m:
        resp = await admin_client.post(
            "/api/team-requests/00000000-0000-0000-0000-000000000001/approve"
        )

    assert resp.status_code == 200, resp.text
    kwargs = m.await_args.kwargs
    assert kwargs["boost_max_budget"] == 100.0
    assert 29 <= (kwargs["expires_at"] - datetime.now(UTC)).days <= 30


@pytest.mark.asyncio
async def test_approve_budget_request_without_duration_is_permanent(
    admin_client: AsyncClient, mock_litellm, mock_db
):
    """A pending budget request with no requested duration still takes the
    permanent update_team_member path — the boost helper is never called."""
    from app.db.models.custom_team_join_request import JoinRequestStatus
    from app.db.session import get_litellm_db
    from app.main import app

    req = MagicMock()
    req.status = JoinRequestStatus.PENDING
    req.request_type = "budget"
    req.team_id = "team-1"
    req.requester_id = "user002"
    req.requested_budget = 300.0
    req.requested_duration_days = None

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = req
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.flush = AsyncMock()

    app.dependency_overrides[get_litellm_db] = lambda: mock_db
    mock_litellm.update_team_member = AsyncMock(return_value={"status": "ok"})

    with patch("app.api.team_requests.apply_member_budget_boost", AsyncMock()) as m:
        resp = await admin_client.post(
            "/api/team-requests/00000000-0000-0000-0000-000000000001/approve"
        )

    assert resp.status_code == 200, resp.text
    m.assert_not_awaited()
    mock_litellm.update_team_member.assert_awaited_once()
