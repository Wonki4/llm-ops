"""Sub pinning at the HTTP edges: portal 403 on mismatch, admin unpin."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.auth import deps
from app.auth.jwt import TokenPayload
from app.services.user_provisioning import IdentityMismatchError


@pytest.mark.asyncio
async def test_portal_rejects_mismatched_account_with_403(monkeypatch):
    monkeypatch.setattr(deps, "upsert_portal_user", AsyncMock(side_effect=IdentityMismatchError("ADMIN001")))
    token = TokenPayload(sub="new-sub", preferred_username="admin001")
    with pytest.raises(HTTPException) as exc:
        await deps.get_current_user(token=token, db=AsyncMock())
    assert exc.value.status_code == 403
    assert exc.value.detail == "계정 확인이 필요합니다. 관리자에게 문의하세요."


def _update_result(found: bool) -> MagicMock:
    r = MagicMock()
    r.first.return_value = (1,) if found else None
    return r


@pytest.mark.asyncio
async def test_admin_unpins_user(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_update_result(True))
    async with client_for_user(super_user) as client:
        resp = await client.delete("/api/admin/users/ADMIN001/keycloak-sub")
    assert resp.status_code == 200
    assert resp.json() == {"status": "unpinned", "user_id": "ADMIN001"}
    sql, params = mock_db.execute.await_args.args
    assert "SET keycloak_sub = NULL" in str(sql)
    assert params == {"uid": "ADMIN001"}


@pytest.mark.asyncio
async def test_admin_unpin_unknown_user_is_404(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_update_result(False))
    async with client_for_user(super_user) as client:
        resp = await client.delete("/api/admin/users/NOBODY/keycloak-sub")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_unpin_requires_super_user(client_for_user, regular_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_update_result(True))
    async with client_for_user(regular_user) as client:
        resp = await client.delete("/api/admin/users/ADMIN001/keycloak-sub")
    assert resp.status_code == 403
    mock_db.execute.assert_not_awaited()
