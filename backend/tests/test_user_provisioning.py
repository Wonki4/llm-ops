"""upsert_portal_user: the portal user row behind every Keycloak identity."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.auth.jwt import TokenPayload
from app.config import settings
from app.db.models.custom_user import CustomUser, GlobalRole
from app.services.user_provisioning import IdentityMismatchError, canonical_user_id, upsert_portal_user


def _token(**kw) -> TokenPayload:
    base = dict(sub="s", preferred_username="user001", email="u@example.com", name="U")
    base.update(kw)
    return TokenPayload(**base)


def _db(*lookups, claim_rows: int = 1, pinned_by_other: str | None = None):
    """execute() returns the given users for successive SELECTs (None = missing).

    The sub-pin UPDATE reports ``claim_rows`` affected rows; a lost race
    (0 rows) makes ``refresh`` load ``pinned_by_other`` as the pinned sub.
    """
    queue = list(lookups)
    sql: list[str] = []

    async def execute(stmt, params=None):
        text_ = str(stmt)
        sql.append(text_)
        r = MagicMock()
        if text_.lstrip().upper().startswith("SELECT"):
            user = queue.pop(0)
            r.scalar_one_or_none.return_value = user
            r.scalar_one.return_value = user
        elif text_.lstrip().upper().startswith("UPDATE"):
            r.rowcount = claim_rows
        return r

    async def refresh(user, attrs=None):
        user.keycloak_sub = pinned_by_other

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=execute)
    db.refresh = AsyncMock(side_effect=refresh)
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


# ─── Keycloak sub pinning ────────────────────────────────────


def _pin_updates(db) -> list[str]:
    return [q for q in db.sql if q.lstrip().upper().startswith("UPDATE")]


@pytest.mark.asyncio
async def test_new_user_is_inserted_with_its_sub(monkeypatch):
    monkeypatch.setattr(settings, "keycloak_sub_pinning", "enforce")
    created = CustomUser(user_id="USER001", global_role=GlobalRole.USER, keycloak_sub="sub-a")
    db = _db(None, created)
    user = await upsert_portal_user(db, _token(sub="sub-a"))
    assert user is created
    insert = next(q for q in db.sql if "INSERT INTO custom_users" in q)
    assert "keycloak_sub" in insert
    assert _pin_updates(db) == []


@pytest.mark.asyncio
async def test_unpinned_user_is_pinned_on_next_login(monkeypatch):
    monkeypatch.setattr(settings, "keycloak_sub_pinning", "enforce")
    existing = CustomUser(user_id="USER001", global_role=GlobalRole.USER, keycloak_sub=None)
    db = _db(existing)
    user = await upsert_portal_user(db, _token(sub="sub-a"))
    assert user.keycloak_sub == "sub-a"
    (update,) = _pin_updates(db)
    assert "keycloak_sub IS NULL" in update  # only claims an unpinned row


@pytest.mark.asyncio
async def test_same_sub_passes_without_writing(monkeypatch):
    monkeypatch.setattr(settings, "keycloak_sub_pinning", "enforce")
    existing = CustomUser(user_id="USER001", global_role=GlobalRole.USER, keycloak_sub="sub-a")
    db = _db(existing)
    assert await upsert_portal_user(db, _token(sub="sub-a")) is existing
    assert _pin_updates(db) == []


@pytest.mark.asyncio
async def test_reused_user_id_is_rejected_when_enforced(monkeypatch):
    """사번 reused for a new person: different Keycloak account, same username."""
    monkeypatch.setattr(settings, "keycloak_sub_pinning", "enforce")
    existing = CustomUser(user_id="ADMIN001", global_role=GlobalRole.SUPER_USER, keycloak_sub="old-sub")
    db = _db(existing)
    with pytest.raises(IdentityMismatchError):
        await upsert_portal_user(db, _token(sub="new-sub", preferred_username="admin001"))
    assert existing.keycloak_sub == "old-sub"


@pytest.mark.asyncio
async def test_mismatch_is_only_logged_in_record_mode(monkeypatch, caplog):
    monkeypatch.setattr(settings, "keycloak_sub_pinning", "record")
    existing = CustomUser(user_id="USER001", global_role=GlobalRole.USER, keycloak_sub="old-sub")
    db = _db(existing)
    assert await upsert_portal_user(db, _token(sub="new-sub")) is existing
    assert "sub mismatch for user USER001" in caplog.text
    assert existing.keycloak_sub == "old-sub"


@pytest.mark.asyncio
async def test_lost_first_claim_race_compares_with_winner(monkeypatch):
    monkeypatch.setattr(settings, "keycloak_sub_pinning", "enforce")
    existing = CustomUser(user_id="USER001", global_role=GlobalRole.USER, keycloak_sub=None)
    db = _db(existing, claim_rows=0, pinned_by_other="sub-winner")
    with pytest.raises(IdentityMismatchError):
        await upsert_portal_user(db, _token(sub="sub-loser"))


@pytest.mark.asyncio
async def test_token_without_sub(monkeypatch):
    existing = CustomUser(user_id="USER001", global_role=GlobalRole.USER, keycloak_sub="sub-a")
    monkeypatch.setattr(settings, "keycloak_sub_pinning", "enforce")
    with pytest.raises(IdentityMismatchError):
        await upsert_portal_user(_db(existing), _token(sub=None))
    monkeypatch.setattr(settings, "keycloak_sub_pinning", "record")
    assert await upsert_portal_user(_db(existing), _token(sub=None)) is existing


@pytest.mark.asyncio
async def test_pinning_off_ignores_sub(monkeypatch):
    monkeypatch.setattr(settings, "keycloak_sub_pinning", "off")
    existing = CustomUser(user_id="USER001", global_role=GlobalRole.USER, keycloak_sub="old-sub")
    db = _db(existing)
    assert await upsert_portal_user(db, _token(sub="new-sub")) is existing
    assert _pin_updates(db) == []
