"""Team-member budget boost: effective-budget resolution + API."""

import types
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.services.member_budget_boost import (
    apply_member_budget_boost,
    resolve_effective_budget,
    serialize_boost,
)


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


async def test_apply_boost_reserves_row_before_litellm(mock_db):
    from unittest.mock import AsyncMock, MagicMock

    litellm = MagicMock()
    litellm.update_team_member = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()
    mock_db.refresh = AsyncMock()
    litellm_db = MagicMock()
    with patch("app.services.member_budget_boost.resolve_effective_budget", AsyncMock(return_value=10.0)), \
         patch("app.services.member_budget_boost._active_boost_exists", AsyncMock(return_value=False)):
        boost = await apply_member_budget_boost(
            mock_db, litellm, litellm_db,
            team_id="t", user_id="u", boost_max_budget=100.0,
            expires_at=datetime.now(UTC) + timedelta(days=30), created_by="admin",
        )
    # Row reserved (add + flush) BEFORE the LiteLLM apply
    assert mock_db.add.called and mock_db.flush.await_count == 1
    litellm.update_team_member.assert_awaited_once_with("t", "u", max_budget_in_team=100.0)
    assert boost.original_max_budget == 10.0 and boost.boost_max_budget == 100.0
    assert boost.status == "active"


async def test_apply_boost_rejects_unlimited_member(mock_db):
    from unittest.mock import AsyncMock, MagicMock

    litellm = MagicMock()
    litellm.update_team_member = AsyncMock()
    with patch("app.services.member_budget_boost.resolve_effective_budget", AsyncMock(return_value=None)), \
         patch("app.services.member_budget_boost._active_boost_exists", AsyncMock(return_value=False)):
        with pytest.raises(HTTPException) as e:
            await apply_member_budget_boost(
                mock_db, litellm, MagicMock(),
                team_id="t", user_id="u", boost_max_budget=100.0,
                expires_at=datetime.now(UTC) + timedelta(days=1), created_by="a",
            )
    assert e.value.status_code == 400
    litellm.update_team_member.assert_not_awaited()


async def test_apply_boost_409_when_active_exists(mock_db):
    from unittest.mock import AsyncMock, MagicMock

    litellm = MagicMock()
    litellm.update_team_member = AsyncMock()
    with patch("app.services.member_budget_boost.resolve_effective_budget", AsyncMock(return_value=10.0)), \
         patch("app.services.member_budget_boost._active_boost_exists", AsyncMock(return_value=True)):
        with pytest.raises(HTTPException) as e:
            await apply_member_budget_boost(
                mock_db, litellm, MagicMock(),
                team_id="t", user_id="u", boost_max_budget=100.0,
                expires_at=datetime.now(UTC) + timedelta(days=1), created_by="a",
            )
    assert e.value.status_code == 409


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
        with patch("app.services.member_budget_boost.resolve_effective_budget", AsyncMock(return_value=10.0)), \
             patch("app.services.member_budget_boost._active_boost_exists", AsyncMock(return_value=False)):
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
        with patch("app.services.member_budget_boost.resolve_effective_budget", AsyncMock(return_value=None)), \
             patch("app.services.member_budget_boost._active_boost_exists", AsyncMock(return_value=False)):
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
        with patch("app.services.member_budget_boost.resolve_effective_budget", AsyncMock(return_value=10.0)), \
             patch("app.services.member_budget_boost._active_boost_exists", AsyncMock(return_value=False)):
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
        with patch("app.services.member_budget_boost.resolve_effective_budget", AsyncMock(return_value=10.0)), \
             patch("app.services.member_budget_boost._active_boost_exists", AsyncMock(return_value=True)):
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


async def test_list_budget_boosts_returns_rows(super_user, mock_litellm, mock_db):
    now = datetime(2026, 7, 9, tzinfo=UTC)
    rows = [
        types.SimpleNamespace(
            id=uuid.uuid4(), team_id="team-1", user_id="user002",
            original_max_budget=10.0, boost_max_budget=100.0,
            expires_at=now, status="active", reverted_at=None,
            created_by="admin", created_at=now,
        ),
        types.SimpleNamespace(
            id=uuid.uuid4(), team_id="team-1", user_id="user003",
            original_max_budget=5.0, boost_max_budget=50.0,
            expires_at=now, status="cancelled", reverted_at=now,
            created_by="admin", created_at=now,
        ),
    ]
    count_res = types.SimpleNamespace(scalar_one=lambda: 2)
    rows_res = types.SimpleNamespace(scalars=lambda: types.SimpleNamespace(all=lambda: rows))
    mock_db.execute = AsyncMock(side_effect=[count_res, rows_res])
    client = await _admin_client(super_user, mock_litellm, mock_db)
    try:
        resp = await client.get("/api/teams/team-1/budget-boosts")
    finally:
        from app.main import app
        app.dependency_overrides.clear()
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["boosts"]) == 2
    assert body["total"] == 2
    first = body["boosts"][0]
    assert first["user_id"] == "user002"
    assert first["original_max_budget"] == 10.0
    assert first["boost_max_budget"] == 100.0
    assert first["status"] == "active"
    assert first["reverted_at"] is None


async def test_list_budget_boosts_clamps_page_size(super_user, mock_litellm, mock_db):
    count_res = types.SimpleNamespace(scalar_one=lambda: 0)
    rows_res = types.SimpleNamespace(scalars=lambda: types.SimpleNamespace(all=lambda: []))
    mock_db.execute = AsyncMock(side_effect=[count_res, rows_res])
    client = await _admin_client(super_user, mock_litellm, mock_db)
    try:
        resp = await client.get("/api/teams/team-1/budget-boosts?page_size=9999")
    finally:
        from app.main import app
        app.dependency_overrides.clear()
    assert resp.status_code == 200, resp.text
    assert resp.json()["boosts"] == []
    assert resp.json()["total"] == 0
    # page_size clamped to 200
    rows_stmt = mock_db.execute.await_args_list[1].args[0]
    compiled = str(rows_stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "LIMIT 200" in compiled


async def test_list_budget_boosts_active_filter_and_pagination(super_user, mock_litellm, mock_db):
    count_res = types.SimpleNamespace(scalar_one=lambda: 120)
    rows_res = types.SimpleNamespace(scalars=lambda: types.SimpleNamespace(all=lambda: []))
    mock_db.execute = AsyncMock(side_effect=[count_res, rows_res])
    client = await _admin_client(super_user, mock_litellm, mock_db)
    try:
        resp = await client.get(
            "/api/teams/team-1/budget-boosts?status_filter=active&page=2&page_size=50"
        )
    finally:
        from app.main import app
        app.dependency_overrides.clear()
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 120
    count_stmt = mock_db.execute.await_args_list[0].args[0]
    rows_stmt = mock_db.execute.await_args_list[1].args[0]
    count_sql = str(count_stmt.compile(compile_kwargs={"literal_binds": True}))
    rows_sql = str(rows_stmt.compile(compile_kwargs={"literal_binds": True}))
    # status filter scopes BOTH the count and the rows query
    assert "status" in count_sql and "'active'" in count_sql
    assert "status" in rows_sql and "'active'" in rows_sql
    # page 2 with page_size 50 → OFFSET 50
    assert "LIMIT 50" in rows_sql and "OFFSET 50" in rows_sql


async def test_create_boost_naive_expires_at_is_accepted(super_user, mock_litellm, mock_db):
    mock_litellm.update_team_member = AsyncMock(return_value={"status": "ok"})
    naive_future = (datetime.now(UTC) + timedelta(days=1)).replace(tzinfo=None).isoformat()
    client = await _admin_client(super_user, mock_litellm, mock_db)
    try:
        with patch("app.services.member_budget_boost.resolve_effective_budget", AsyncMock(return_value=10.0)), \
             patch("app.services.member_budget_boost._active_boost_exists", AsyncMock(return_value=False)):
            resp = await client.post(
                "/api/teams/team-1/members/user002/budget-boost",
                json={"max_budget": 100.0, "expires_at": naive_future},
            )
    finally:
        from app.main import app
        app.dependency_overrides.clear()
    assert resp.status_code == 201, resp.text
