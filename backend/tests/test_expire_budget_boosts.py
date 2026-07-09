"""Worker: revert expired member budget boosts."""

import types
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from app.jobs.expire_budget_boosts import revert_expired_boosts

NOW = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)


def _boost(**kw):
    base = dict(
        id=uuid.uuid4(), team_id="t", user_id="u",
        original_max_budget=10.0, boost_max_budget=100.0,
        status="active", reverted_at=None,
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def _portal_db(expired):
    """Portal session: one execute → select(...).scalars().all() == expired."""
    db = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = expired
    select_result = MagicMock()
    select_result.scalars.return_value = scalars
    db.execute = AsyncMock(return_value=select_result)
    db.commit = AsyncMock()
    return db


def _litellm_db(membership_exists=True):
    """LiteLLM session: each execute → membership-existence .scalar()."""
    db = MagicMock()
    membership_result = MagicMock()
    membership_result.scalar.return_value = 1 if membership_exists else None
    db.execute = AsyncMock(return_value=membership_result)
    return db


async def test_revert_restores_original_and_marks_reverted():
    b = _boost()
    portal_db = _portal_db([b])
    litellm = MagicMock()
    litellm.update_team_member = AsyncMock()
    with patch("app.jobs.expire_budget_boosts.async_session_factory", _ctx(portal_db)), \
         patch("app.jobs.expire_budget_boosts.litellm_session_factory", _ctx(_litellm_db())), \
         patch("app.jobs.expire_budget_boosts.LiteLLMClient", return_value=litellm):
        n = await revert_expired_boosts(NOW)
    assert n == 1
    litellm.update_team_member.assert_awaited_once_with("t", "u", max_budget_in_team=10.0)
    assert b.status == "reverted" and b.reverted_at is not None
    portal_db.commit.assert_awaited()


async def test_revert_marks_reverted_without_api_when_membership_gone():
    b = _boost()
    litellm = MagicMock()
    litellm.update_team_member = AsyncMock()
    with patch("app.jobs.expire_budget_boosts.async_session_factory", _ctx(_portal_db([b]))), \
         patch("app.jobs.expire_budget_boosts.litellm_session_factory", _ctx(_litellm_db(membership_exists=False))), \
         patch("app.jobs.expire_budget_boosts.LiteLLMClient", return_value=litellm):
        n = await revert_expired_boosts(NOW)
    assert n == 1
    litellm.update_team_member.assert_not_awaited()
    assert b.status == "reverted"


async def test_revert_leaves_active_on_litellm_failure():
    b = _boost()
    litellm = MagicMock()
    litellm.update_team_member = AsyncMock(side_effect=RuntimeError("boom"))
    with patch("app.jobs.expire_budget_boosts.async_session_factory", _ctx(_portal_db([b]))), \
         patch("app.jobs.expire_budget_boosts.litellm_session_factory", _ctx(_litellm_db())), \
         patch("app.jobs.expire_budget_boosts.LiteLLMClient", return_value=litellm):
        n = await revert_expired_boosts(NOW)
    assert n == 0
    assert b.status == "active" and b.reverted_at is None


def _ctx(db):
    """A callable returning an async-context-manager yielding db (session factory stub)."""
    class _CM:
        async def __aenter__(self):
            return db
        async def __aexit__(self, *a):
            return False
    return lambda: _CM()
