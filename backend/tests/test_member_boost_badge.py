"""The members endpoint reports each member's active boost directly, so the
boost badge never depends on a separately-fetched, capped boost list."""

import types
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient


class _Res:
    def __init__(self, *, scalar=None, rows=None, scalars=None):
        self._scalar = scalar
        self._rows = rows or []
        self._scalars = scalars or []

    def scalar(self):
        return self._scalar

    def mappings(self):
        rows = self._rows

        class _M:
            def first(self):
                return rows[0] if rows else None

            def __iter__(self):
                return iter(rows)

        return _M()

    def scalars(self):
        objs = self._scalars

        class _S:
            def all(self):
                return objs

        return _S()


def _member_row(user_id, max_budget):
    return {
        "user_id": user_id,
        "membership_spend": 0,
        "membership_max_budget": max_budget,
        "membership_tpm_limit": None,
        "membership_rpm_limit": None,
        "key_count": 0,
    }


def _boost(user_id):
    now = datetime(2026, 7, 28, tzinfo=UTC)
    return types.SimpleNamespace(
        id=uuid.uuid4(), team_id="t1", user_id=user_id,
        original_max_budget=10.0, boost_max_budget=100.0,
        expires_at=now, status="active", reverted_at=None,
        created_by="admin", created_at=now,
    )


def _client(user, mock_db) -> AsyncClient:
    from app.auth.deps import get_current_user
    from app.db.session import get_db, get_litellm_db
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_litellm_db] = lambda: mock_db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _clear():
    from app.main import app

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_members_endpoint_reports_active_boost_per_member(super_user, mock_db):
    # Query order: admins, count, membership(paged), keys, expiry, active-boosts.
    mock_db.execute = AsyncMock(
        side_effect=[
            _Res(rows=[{"admins": ["admin1"]}]),
            _Res(scalar=2),
            _Res(rows=[_member_row("u1", 100), _member_row("u2", 20)]),
            _Res(rows=[]),   # keys
            _Res(rows=[]),   # expiry
            _Res(scalars=[_boost("u1")]),  # only u1 has an active boost
        ]
    )
    client = _client(super_user, mock_db)
    try:
        resp = await client.get("/api/teams/t1/members")
    finally:
        _clear()
    assert resp.status_code == 200, resp.text
    by_id = {m["user_id"]: m for m in resp.json()["members"]}
    # u1 is flagged directly from the endpoint (no separate capped fetch).
    assert by_id["u1"]["active_boost"] is not None
    assert by_id["u1"]["active_boost"]["boost_max_budget"] == 100.0
    assert by_id["u1"]["active_boost"]["expires_at"].startswith("2026-07-28")
    # u2 has no active boost.
    assert by_id["u2"]["active_boost"] is None

    # The active-boost query is scoped to the page's user_ids (no team-wide cap).
    boost_stmt = mock_db.execute.await_args_list[5].args[0]
    boost_sql = str(boost_stmt)
    assert "custom_member_budget_boost" in boost_sql
