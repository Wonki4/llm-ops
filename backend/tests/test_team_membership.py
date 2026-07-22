"""Shared team-member removal helper: LiteLLM-authoritative delete + portal sync."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException

from app.services.team_membership import remove_member_from_team


class _Row:
    def __init__(self, mapping):
        self._mapping = mapping

    def mappings(self):
        return MagicMock(first=lambda: self._mapping)


def _http_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://litellm/team/member_delete")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


async def test_calls_litellm_then_syncs_arrays_and_expiry_row():
    litellm = MagicMock()
    litellm.remove_team_member = AsyncMock()
    litellm_db = MagicMock()
    litellm_db.execute = AsyncMock(return_value=_Row({"members": ["u1", "u2"], "admins": ["u1"]}))
    litellm_db.commit = AsyncMock()
    db = MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()

    await remove_member_from_team(litellm, litellm_db, db, team_id="t1", user_id="u2")

    # LiteLLM is authoritative and called first.
    litellm.remove_team_member.assert_awaited_once_with("t1", "u2")
    # members/admins String[] synced (SELECT then UPDATE), removing only u2.
    update_call = litellm_db.execute.await_args_list[1]
    assert "UPDATE" in str(update_call.args[0])
    assert update_call.args[1]["members"] == ["u1"]
    assert update_call.args[1]["admins"] == ["u1"]
    litellm_db.commit.assert_awaited_once()
    # Portal expiry row dropped.
    del_call = db.execute.await_args_list[0]
    assert "DELETE FROM custom_team_membership" in str(del_call.args[0])
    assert del_call.args[1] == {"user_id": "u2", "team_id": "t1"}
    db.commit.assert_awaited_once()


async def test_continues_portal_sync_when_member_already_gone_on_litellm():
    # The exact drift this repairs: LiteLLM says "not in team" (400) but the
    # portal's members/admins/expiry state is stale — clean it anyway.
    litellm = MagicMock()
    litellm.remove_team_member = AsyncMock(side_effect=_http_error(400))
    litellm_db = MagicMock()
    litellm_db.execute = AsyncMock(return_value=_Row({"members": ["u2"], "admins": []}))
    litellm_db.commit = AsyncMock()
    db = MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()

    await remove_member_from_team(litellm, litellm_db, db, team_id="t1", user_id="u2")

    # Portal arrays + expiry still cleaned.
    assert litellm_db.execute.await_args_list[1].args[1]["members"] == []
    db.commit.assert_awaited_once()


async def test_raises_502_on_litellm_server_error_without_touching_portal():
    litellm = MagicMock()
    litellm.remove_team_member = AsyncMock(side_effect=_http_error(503))
    litellm_db = MagicMock()
    litellm_db.execute = AsyncMock()
    db = MagicMock()
    db.execute = AsyncMock()

    with pytest.raises(HTTPException) as e:
        await remove_member_from_team(litellm, litellm_db, db, team_id="t1", user_id="u2")

    assert e.value.status_code == 502
    litellm_db.execute.assert_not_awaited()
    db.execute.assert_not_awaited()


# ─── Endpoint wiring (permission checks fire before the helper) ─────


@asynccontextmanager
async def _delete_client(user, mock_db):
    """Client with the portal AND litellm dbs mocked (the endpoint's first
    SELECT reads litellm_db, which client_for_user leaves unmocked)."""
    from httpx import ASGITransport, AsyncClient

    from app.auth.deps import get_current_user
    from app.clients.litellm import get_litellm_client
    from app.db.session import get_db, get_litellm_db
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_litellm_client] = lambda: MagicMock()
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_litellm_db] = lambda: mock_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


async def test_remove_team_member_blocks_self_removal(super_user, mock_db):
    from unittest.mock import patch

    mock_db.execute = AsyncMock(
        return_value=_Row({"members": [super_user.user_id], "admins": [super_user.user_id]})
    )
    with patch("app.api.teams.remove_member_from_team", AsyncMock()) as helper:
        async with _delete_client(super_user, mock_db) as client:
            resp = await client.delete(f"/api/teams/team-1/members/{super_user.user_id}")
    assert resp.status_code == 400
    helper.assert_not_awaited()


async def test_remove_team_member_blocks_last_admin(super_user, mock_db):
    from unittest.mock import patch

    mock_db.execute = AsyncMock(return_value=_Row({"members": ["m1"], "admins": ["adminX"]}))
    with patch("app.api.teams.remove_member_from_team", AsyncMock()) as helper:
        async with _delete_client(super_user, mock_db) as client:
            resp = await client.delete("/api/teams/team-1/members/adminX")
    assert resp.status_code == 400
    helper.assert_not_awaited()


async def test_remove_team_member_calls_helper_on_success(super_user, mock_db):
    from unittest.mock import patch

    mock_db.execute = AsyncMock(return_value=_Row({"members": ["m1", "m2"], "admins": ["adminX"]}))
    with patch("app.api.teams.remove_member_from_team", AsyncMock()) as helper:
        async with _delete_client(super_user, mock_db) as client:
            resp = await client.delete("/api/teams/team-1/members/m2")
    assert resp.status_code == 200, resp.text
    helper.assert_awaited_once()
    assert helper.await_args.kwargs == {"team_id": "team-1", "user_id": "m2"}
