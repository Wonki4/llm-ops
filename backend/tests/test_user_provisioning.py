"""upsert_portal_user: the portal user row behind every Keycloak identity."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.auth.jwt import TokenPayload
from app.config import settings
from app.db.models.custom_user import CustomUser, GlobalRole
from app.services.user_provisioning import canonical_user_id, upsert_portal_user


def _token(**kw) -> TokenPayload:
    base = dict(sub="s", preferred_username="user001", email="u@example.com", name="U")
    base.update(kw)
    return TokenPayload(**base)


def _db(*lookups):
    """execute() returns the given users for successive SELECTs (None = missing)."""
    queue = list(lookups)
    sql: list[str] = []

    async def execute(stmt, params=None):
        sql.append(str(stmt))
        r = MagicMock()
        if str(stmt).lstrip().upper().startswith("SELECT"):
            user = queue.pop(0)
            r.scalar_one_or_none.return_value = user
            r.scalar_one.return_value = user
        return r

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=execute)
    db.sql = sql
    return db


def test_canonical_user_id_is_upper_cased_preferred_username():
    assert canonical_user_id(_token(preferred_username="ab12")) == "AB12"


@pytest.mark.asyncio
async def test_missing_user_is_inserted(monkeypatch):
    monkeypatch.setattr(settings, "super_user_role", "super_user")
    created = CustomUser(user_id="USER001", global_role=GlobalRole.USER)
    db = _db(None, created)
    user = await upsert_portal_user(db, _token())
    assert user is created
    assert any("INSERT INTO custom_users" in s for s in db.sql)


@pytest.mark.asyncio
async def test_existing_user_role_is_promoted_from_token_roles(monkeypatch):
    monkeypatch.setattr(settings, "super_user_role", "super_user")
    existing = CustomUser(user_id="USER001", global_role=GlobalRole.USER)
    db = _db(existing)
    user = await upsert_portal_user(db, _token(client_roles=["super_user"]))
    assert user.global_role == GlobalRole.SUPER_USER
    db.flush.assert_awaited()


@pytest.mark.asyncio
async def test_existing_super_user_is_not_demoted(monkeypatch):
    existing = CustomUser(user_id="USER001", global_role=GlobalRole.SUPER_USER)
    db = _db(existing)
    user = await upsert_portal_user(db, _token())
    assert user.global_role == GlobalRole.SUPER_USER
    db.flush.assert_not_awaited()
