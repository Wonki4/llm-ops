"""Team-member budget boost: effective-budget resolution + API."""

import types
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from httpx import ASGITransport, AsyncClient

from app.services.member_budget_boost import resolve_effective_budget, serialize_boost


def _future_iso(hours=24):
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat()


class _Result:
    def __init__(self, value=None, first=None):
        self._value = value
        self._first = first

    def scalar(self):
        return self._value

    def mappings(self):
        return types.SimpleNamespace(first=lambda: self._first)


async def test_resolve_effective_prefers_dedicated_row():
    db = MagicMock()
    db.execute = AsyncMock(return_value=_Result(first={"max_budget": 50.0}))
    assert await resolve_effective_budget(db, "t", "u") == 50.0
    # A dedicated row short-circuits — no team-default query needed.
    assert db.execute.await_count == 1


async def test_resolve_effective_falls_back_to_team_default():
    db = MagicMock()
    # 1) membership dedicated row → no max_budget; 2) team metadata → budget_id;
    # 3) default BudgetTable row → 20.0.
    db.execute = AsyncMock(
        side_effect=[
            _Result(first={"max_budget": None}),
            _Result(value={"team_member_budget_id": "bud-1"}),
            _Result(first={"max_budget": 20.0}),
        ]
    )
    assert await resolve_effective_budget(db, "t", "u") == 20.0


async def test_resolve_effective_none_when_unlimited():
    db = MagicMock()
    # membership row unlimited; team has no default member budget metadata.
    db.execute = AsyncMock(
        side_effect=[
            _Result(first={"max_budget": None}),
            _Result(value=None),
        ]
    )
    assert await resolve_effective_budget(db, "t", "u") is None


def test_serialize_boost_shape():
    now = datetime(2026, 7, 9, tzinfo=UTC)
    row = types.SimpleNamespace(
        id=uuid.uuid4(), team_id="t", user_id="u",
        original_max_budget=10.0, boost_max_budget=50.0,
        expires_at=now, status="active", reverted_at=None,
        created_by="admin", created_at=now, updated_at=now,
    )
    out = serialize_boost(row)
    assert out["user_id"] == "u" and out["original_max_budget"] == 10.0
    assert out["boost_max_budget"] == 50.0 and out["status"] == "active"
    assert out["expires_at"].startswith("2026-07-09")
    assert out["reverted_at"] is None


# ─── API ─────────────────────────────────────────────────────

async def _admin_client(super_user, mock_litellm, mock_db):
    from app.auth.deps import get_current_user
    from app.clients.litellm import get_litellm_client
    from app.db.session import get_db, get_litellm_db
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: super_user
    app.dependency_overrides[get_litellm_client] = lambda: mock_litellm
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_litellm_db] = lambda: mock_db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_create_boost_snapshots_and_applies(super_user, mock_litellm, mock_db):
    mock_litellm.update_team_member = AsyncMock(return_value={"status": "ok"})
    client = await _admin_client(super_user, mock_litellm, mock_db)
    try:
        with patch("app.api.teams.resolve_effective_budget", AsyncMock(return_value=10.0)), \
             patch("app.api.teams._active_boost_exists", AsyncMock(return_value=False)):
            resp = await client.post(
                "/api/teams/team-1/members/user002/budget-boost",
                json={"max_budget": 100.0, "expires_at": _future_iso()},
            )
    finally:
        from app.main import app
        app.dependency_overrides.clear()
    assert resp.status_code == 201, resp.text
    mock_litellm.update_team_member.assert_awaited_once_with(
        "team-1", "user002", max_budget_in_team=100.0
    )
    boost = mock_db.add.call_args.args[0]
    assert boost.original_max_budget == 10.0 and boost.boost_max_budget == 100.0
    assert boost.status == "active"


async def test_create_boost_rejects_unlimited_member(super_user, mock_litellm, mock_db):
    client = await _admin_client(super_user, mock_litellm, mock_db)
    try:
        with patch("app.api.teams.resolve_effective_budget", AsyncMock(return_value=None)), \
             patch("app.api.teams._active_boost_exists", AsyncMock(return_value=False)):
            resp = await client.post(
                "/api/teams/team-1/members/user002/budget-boost",
                json={"max_budget": 100.0, "expires_at": _future_iso()},
            )
    finally:
        from app.main import app
        app.dependency_overrides.clear()
    assert resp.status_code == 400


async def test_create_boost_rejects_past_expiry(super_user, mock_litellm, mock_db):
    client = await _admin_client(super_user, mock_litellm, mock_db)
    try:
        with patch("app.api.teams.resolve_effective_budget", AsyncMock(return_value=10.0)), \
             patch("app.api.teams._active_boost_exists", AsyncMock(return_value=False)):
            resp = await client.post(
                "/api/teams/team-1/members/user002/budget-boost",
                json={"max_budget": 100.0, "expires_at": _future_iso(hours=-1)},
            )
    finally:
        from app.main import app
        app.dependency_overrides.clear()
    assert resp.status_code == 400


async def test_create_boost_conflict_when_active_exists(super_user, mock_litellm, mock_db):
    client = await _admin_client(super_user, mock_litellm, mock_db)
    try:
        with patch("app.api.teams.resolve_effective_budget", AsyncMock(return_value=10.0)), \
             patch("app.api.teams._active_boost_exists", AsyncMock(return_value=True)):
            resp = await client.post(
                "/api/teams/team-1/members/user002/budget-boost",
                json={"max_budget": 100.0, "expires_at": _future_iso()},
            )
    finally:
        from app.main import app
        app.dependency_overrides.clear()
    assert resp.status_code == 409


async def test_cancel_boost_restores_original(super_user, mock_litellm, mock_db):
    mock_litellm.update_team_member = AsyncMock(return_value={"status": "ok"})
    active = types.SimpleNamespace(
        id=uuid.uuid4(), team_id="team-1", user_id="user002",
        original_max_budget=10.0, boost_max_budget=100.0, status="active",
        reverted_at=None, expires_at=None, created_by="admin",
        created_at=datetime(2026, 7, 9, tzinfo=UTC),
    )
    mock_db.execute = AsyncMock(
        return_value=types.SimpleNamespace(scalar_one_or_none=lambda: active)
    )
    client = await _admin_client(super_user, mock_litellm, mock_db)
    try:
        resp = await client.delete("/api/teams/team-1/members/user002/budget-boost")
    finally:
        from app.main import app
        app.dependency_overrides.clear()
    assert resp.status_code == 200, resp.text
    mock_litellm.update_team_member.assert_awaited_once_with(
        "team-1", "user002", max_budget_in_team=10.0
    )
    assert active.status == "cancelled" and active.reverted_at is not None


async def test_cancel_boost_404_when_none_active(super_user, mock_litellm, mock_db):
    mock_db.execute = AsyncMock(
        return_value=types.SimpleNamespace(scalar_one_or_none=lambda: None)
    )
    client = await _admin_client(super_user, mock_litellm, mock_db)
    try:
        resp = await client.delete("/api/teams/team-1/members/user002/budget-boost")
    finally:
        from app.main import app
        app.dependency_overrides.clear()
    assert resp.status_code == 404
