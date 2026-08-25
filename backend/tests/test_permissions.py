"""get_team_access — membership recognition (mock_db, no real DB)."""

import types
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.auth.permissions import get_team_access
from app.db.models.custom_user import GlobalRole


def _user(uid="E1", role=GlobalRole.USER):
    return types.SimpleNamespace(user_id=uid, global_role=role)


def _team_row(admins=None, members=None):
    """A result for `SELECT admins, members ... ` -> .mappings().first()."""
    r = MagicMock()
    r.mappings.return_value.first.return_value = {"admins": admins or [], "members": members or []}
    return r


def _user_table_row(present: bool):
    """A result for the UserTable.teams membership probe -> .first()."""
    r = MagicMock()
    r.first.return_value = (1,) if present else None
    return r


async def test_super_user_is_admin_without_query():
    db = AsyncMock()
    assert await get_team_access(_user(role=GlobalRole.SUPER_USER), "t1", db) == "admin"
    db.execute.assert_not_called()


async def test_admin_in_admins_array():
    db = AsyncMock(execute=AsyncMock(return_value=_team_row(admins=["E1"])))
    assert await get_team_access(_user("E1"), "t1", db) == "admin"


async def test_member_in_members_array():
    db = AsyncMock(execute=AsyncMock(return_value=_team_row(members=["E1"])))
    assert await get_team_access(_user("E1"), "t1", db) == "member"


async def test_member_via_user_table_teams_fallback():
    # Absent from the TeamTable arrays but present in UserTable.teams (the source
    # "My Teams" uses) -> must be "member", NOT 403. This is the reported bug.
    db = AsyncMock(execute=AsyncMock(side_effect=[_team_row(), _user_table_row(True)]))
    assert await get_team_access(_user("E1"), "t1", db) == "member"


async def test_non_member_403():
    db = AsyncMock(execute=AsyncMock(side_effect=[_team_row(), _user_table_row(False)]))
    with pytest.raises(HTTPException) as exc:
        await get_team_access(_user("E1"), "t1", db)
    assert exc.value.status_code == 403
