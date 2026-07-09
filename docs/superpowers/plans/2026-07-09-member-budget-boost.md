# Team Member Budget Boost Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a team admin raise a member's budget for a fixed period; the portal snapshots the current budget, applies the new one via LiteLLM, and a worker restores the snapshot at expiry. Every boost is kept as history.

**Architecture:** A portal-DB table `custom_member_budget_boost` records each boost (active/reverted/cancelled). Three admin endpoints in the teams router (create/cancel/list) snapshot the member's effective budget and drive changes through the existing `LiteLLMClient.update_team_member`. A worker loop reverts expired boosts. Frontend adds a boost dialog, an active badge, and a history card. Spec: `docs/superpowers/specs/2026-07-09-member-budget-boost-design.md`.

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic; asyncio worker loop; Next.js + next-intl + TanStack Query.

## Global Constraints

- Revert is **unconditional**: at expiry (and on cancel) the member's budget is set back to the snapshotted `original_max_budget`, overwriting any manual edit made during the window.
- Snapshot is the member's **effective** budget at boost time: dedicated membership budget row (`LiteLLM_TeamMembership.budget_id → LiteLLM_BudgetTable.max_budget`) if present, else the team default member budget (`_get_default_member_limits(...)["budget"]`).
- Boosting a member whose effective budget is unset/unlimited (None) is rejected (400) — reverting to NULL is not expressible through `update_team_member`.
- One `active` boost per `(team_id, user_id)` — enforced by a partial unique index; the create endpoint returns 409 on a second.
- All three endpoints require team admin via `require_team_admin(user, team_id, litellm_db)` (super-user short-circuits it).
- Budget changes go through `LiteLLMClient.update_team_member(team_id, user_id, max_budget_in_team=<float>)` — never raw SQL on the LiteLLM DB.
- Migration is revision `038_member_budget_boost`, `down_revision="037_cluster_argocd_placement"` (verify against the actual `revision` string in `037_cluster_argocd_placement.py`).
- Backend gates: `cd backend && .venv/bin/python -m pytest tests/ -q` 0 NEW failures (baseline 21); `.venv/bin/ruff check app/ tests/` 0 NEW (baseline 78). Imports top-of-file.
- Frontend gates: `cd frontend && npm run lint` 0 NEW (baseline 4 errors/13 warnings); `npm run build` passes.
- Branch `feat/member-budget-boost` (already checked out). Commits end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Migration 038 + boost model

**Files:**
- Create: `backend/migrations/versions/038_member_budget_boost.py`
- Create: `backend/app/db/models/custom_member_budget_boost.py`

**Interfaces:**
- Produces: ORM class `CustomMemberBudgetBoost` (table `custom_member_budget_boost`) with columns `id`, `team_id`, `user_id`, `original_max_budget`, `boost_max_budget`, `expires_at`, `status`, `reverted_at`, `created_by`, `created_at`, `updated_at`, consumed by Tasks 2–3.

- [ ] **Step 1: Write the migration**

Create `backend/migrations/versions/038_member_budget_boost.py`:

```python
"""Temporary team-member budget boosts.

One row per boost: snapshot of the member's budget at boost time, the boosted
value, the expiry, and a status the worker moves active -> reverted (or an
admin moves active -> cancelled). At most one active boost per (team, user).

Revision ID: 038_member_budget_boost
Revises: 037_cluster_argocd_placement
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "038_member_budget_boost"
down_revision = "037_cluster_argocd_placement"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_member_budget_boost",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("original_max_budget", sa.Float(), nullable=False),
        sa.Column("boost_max_budget", sa.Float(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("reverted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_member_boost_team_user",
        "custom_member_budget_boost",
        ["team_id", "user_id"],
    )
    # One active boost per (team, user).
    op.create_index(
        "uq_member_boost_one_active",
        "custom_member_budget_boost",
        ["team_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index("uq_member_boost_one_active", table_name="custom_member_budget_boost")
    op.drop_index("ix_member_boost_team_user", table_name="custom_member_budget_boost")
    op.drop_table("custom_member_budget_boost")
```

Before committing, open `backend/migrations/versions/037_cluster_argocd_placement.py` and confirm its `revision = "..."` equals this file's `down_revision`; fix if different.

- [ ] **Step 2: Write the model**

Create `backend/app/db/models/custom_member_budget_boost.py`:

```python
"""A temporary budget boost applied to one team member.

The portal snapshots the member's effective budget (original_max_budget),
applies boost_max_budget via LiteLLM, and a worker restores the snapshot when
expires_at passes (status active -> reverted) unless an admin cancels first
(active -> cancelled). Rows are never deleted — they are the boost history.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import CustomBase


class CustomMemberBudgetBoost(CustomBase):
    __tablename__ = "custom_member_budget_boost"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    original_max_budget: Mapped[float] = mapped_column(Float, nullable=False)
    boost_max_budget: Mapped[float] = mapped_column(Float, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active"
    )
    reverted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
```

- [ ] **Step 3: Apply the migration to the local docker DB and verify**

```bash
cd backend && APP_DATABASE_URL="postgresql+asyncpg://llmproxy:dbpassword9090@localhost:5432/litellm_portal" .venv/bin/alembic upgrade head
cd backend && APP_DATABASE_URL="postgresql+asyncpg://llmproxy:dbpassword9090@localhost:5432/litellm_portal" .venv/bin/alembic current
```

Expected: runs `037… -> 038_member_budget_boost`; `current` prints `038_member_budget_boost (head)`.

- [ ] **Step 4: Backend gates**

```bash
cd backend && .venv/bin/python -m pytest tests/ -q 2>&1 | tail -3
cd backend && .venv/bin/ruff check app/ tests/ 2>&1 | tail -2
```

Expected: 21 pre-existing failures (0 new), ruff 78 (0 new).

- [ ] **Step 5: Commit**

```bash
git add backend/migrations/versions/038_member_budget_boost.py backend/app/db/models/custom_member_budget_boost.py
git commit -m "feat(db): custom_member_budget_boost table + model

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Boost service helper + create/cancel/list endpoints

**Files:**
- Create: `backend/app/services/member_budget_boost.py`
- Modify: `backend/app/api/teams.py` (imports; new endpoints after `change_member_budget`, ~line 885)
- Test: `backend/tests/test_member_budget_boost.py`

**Interfaces:**
- Consumes: `CustomMemberBudgetBoost` (Task 1); `require_team_admin(user, team_id, litellm_db)`, `LiteLLMClient.update_team_member(team_id, user_id, *, max_budget_in_team=...)` (existing). The service is self-contained (inlines the team-default lookup) and does NOT import `app.api.teams`, to avoid a circular import.
- Produces: `async resolve_effective_budget(litellm_db, team_id, user_id) -> float | None` and `def serialize_boost(row) -> dict` in `app.services.member_budget_boost`, plus `async _team_default_member_budget(litellm_db, team_id) -> float | None`. `serialize_boost`'s dict shape is what Task 4's frontend type mirrors.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_member_budget_boost.py`:

```python
"""Team-member budget boost: effective-budget resolution + API."""

import types
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.services.member_budget_boost import resolve_effective_budget, serialize_boost


def _future_iso(hours=24):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


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
    now = datetime(2026, 7, 9, tzinfo=timezone.utc)
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
        created_at=datetime(2026, 7, 9, tzinfo=timezone.utc),
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
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd backend && .venv/bin/python -m pytest tests/test_member_budget_boost.py -q
```

Expected: ImportError (`resolve_effective_budget`/`serialize_boost` missing).

- [ ] **Step 3: Write the service helper**

Create `backend/app/services/member_budget_boost.py`:

Self-contained — it does NOT import `app.api.teams` (that would create a
cycle, since teams.py imports this module). The team-default lookup is
inlined here as its own two queries.

```python
"""Effective-budget resolution and serialization for member budget boosts."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.custom_member_budget_boost import CustomMemberBudgetBoost


async def _team_default_member_budget(litellm_db: AsyncSession, team_id: str) -> float | None:
    """The team's default member max_budget (metadata.team_member_budget_id row)."""
    metadata = (
        await litellm_db.execute(
            text('SELECT metadata FROM "LiteLLM_TeamTable" WHERE team_id = :team_id'),
            {"team_id": team_id},
        )
    ).scalar()
    if not metadata or not isinstance(metadata, dict):
        return None
    budget_id = metadata.get("team_member_budget_id")
    if not budget_id:
        return None
    row = (
        await litellm_db.execute(
            text('SELECT max_budget FROM "LiteLLM_BudgetTable" WHERE budget_id = :budget_id'),
            {"budget_id": budget_id},
        )
    ).mappings().first()
    if row and row["max_budget"] is not None:
        return float(row["max_budget"])
    return None


async def resolve_effective_budget(
    litellm_db: AsyncSession, team_id: str, user_id: str
) -> float | None:
    """The member's current effective max_budget.

    Prefers the membership's dedicated budget row; falls back to the team's
    default member budget. None means unset/unlimited.
    """
    result = await litellm_db.execute(
        text(
            "SELECT b.max_budget "
            'FROM "LiteLLM_TeamMembership" m '
            'LEFT JOIN "LiteLLM_BudgetTable" b ON m.budget_id = b.budget_id '
            "WHERE m.team_id = :team_id AND m.user_id = :user_id"
        ),
        {"team_id": team_id, "user_id": user_id},
    )
    row = result.mappings().first()
    if row and row["max_budget"] is not None:
        return float(row["max_budget"])
    return await _team_default_member_budget(litellm_db, team_id)


def serialize_boost(row: CustomMemberBudgetBoost) -> dict:
    return {
        "id": str(row.id),
        "team_id": row.team_id,
        "user_id": row.user_id,
        "original_max_budget": row.original_max_budget,
        "boost_max_budget": row.boost_max_budget,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "status": row.status,
        "reverted_at": row.reverted_at.isoformat() if row.reverted_at else None,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
```

- [ ] **Step 4: Add the endpoints to `backend/app/api/teams.py`**

1. Imports (top-of-file). teams.py currently imports only `from fastapi import APIRouter, Depends, HTTPException` and `from sqlalchemy import text`, has NO module-level `uuid`, `logging`/`logger`, `datetime`, `select`, or `status`. Make these exact changes:

- Change `from fastapi import APIRouter, Depends, HTTPException` → `from fastapi import APIRouter, Depends, HTTPException, status`.
- Change `from sqlalchemy import text` → `from sqlalchemy import select, text`.
- Add these new top-of-file imports (alongside the existing stdlib `import json`):

```python
import logging
import uuid
from datetime import datetime, timezone

from app.db.models.custom_member_budget_boost import CustomMemberBudgetBoost
from app.services.member_budget_boost import resolve_effective_budget, serialize_boost
```

- Add a module-level logger after the imports (if the file has none): `logger = logging.getLogger(__name__)`.

Note: teams.py has two FUNCTION-LOCAL `from datetime import ...` lines (inside `_parse_duration` and one other helper). Leaving them is harmless, but if ruff flags redefinition (F811) after adding the module import, delete the now-redundant local imports in those functions and use the module-level `datetime`/`timedelta` (add `timedelta` to the module import if you remove the local one).

2. Add after `change_member_budget` (the function ending ~line 885):

```python
class CreateBudgetBoostRequest(BaseModel):
    max_budget: float
    expires_at: datetime


async def _active_boost_exists(db: AsyncSession, team_id: str, member_id: str) -> bool:
    row = (
        await db.execute(
            select(CustomMemberBudgetBoost).where(
                CustomMemberBudgetBoost.team_id == team_id,
                CustomMemberBudgetBoost.user_id == member_id,
                CustomMemberBudgetBoost.status == "active",
            )
        )
    ).scalar_one_or_none()
    return row is not None


@router.post("/{team_id}/members/{member_id}/budget-boost", status_code=status.HTTP_201_CREATED)
async def create_budget_boost(
    team_id: str,
    member_id: str,
    body: CreateBudgetBoostRequest,
    user: CustomUser = Depends(get_current_user),
    litellm: LiteLLMClient = Depends(get_litellm_client),
    db: AsyncSession = Depends(get_db),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """Temporarily raise a member's budget until expires_at.

    Snapshots the member's effective budget, applies max_budget via LiteLLM,
    and records the boost. A worker restores the snapshot at expiry. Requires
    team admin or super user.
    """
    await require_team_admin(user, team_id, litellm_db)
    if body.max_budget <= 0:
        raise HTTPException(status_code=400, detail="max_budget must be positive")
    expires_at = body.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="expires_at must be in the future")
    if await _active_boost_exists(db, team_id, member_id):
        raise HTTPException(status_code=409, detail="An active boost already exists for this member")

    original = await resolve_effective_budget(litellm_db, team_id, member_id)
    if original is None:
        raise HTTPException(
            status_code=400,
            detail="Member has no budget limit to boost — set a budget first",
        )

    await litellm.update_team_member(team_id, member_id, max_budget_in_team=body.max_budget)

    boost = CustomMemberBudgetBoost(
        id=uuid.uuid4(),
        team_id=team_id,
        user_id=member_id,
        original_max_budget=original,
        boost_max_budget=body.max_budget,
        expires_at=expires_at,
        status="active",
        created_by=user.user_id,
    )
    db.add(boost)
    await db.flush()
    await db.refresh(boost)
    return serialize_boost(boost)


@router.delete("/{team_id}/members/{member_id}/budget-boost")
async def cancel_budget_boost(
    team_id: str,
    member_id: str,
    user: CustomUser = Depends(get_current_user),
    litellm: LiteLLMClient = Depends(get_litellm_client),
    db: AsyncSession = Depends(get_db),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """Cancel an active boost early, restoring the snapshotted budget."""
    await require_team_admin(user, team_id, litellm_db)
    boost = (
        await db.execute(
            select(CustomMemberBudgetBoost).where(
                CustomMemberBudgetBoost.team_id == team_id,
                CustomMemberBudgetBoost.user_id == member_id,
                CustomMemberBudgetBoost.status == "active",
            )
        )
    ).scalar_one_or_none()
    if boost is None:
        raise HTTPException(status_code=404, detail="No active boost for this member")

    try:
        await litellm.update_team_member(
            team_id, member_id, max_budget_in_team=boost.original_max_budget
        )
    except Exception as e:  # noqa: BLE001 — leave active so the worker still reverts
        logger.exception("Boost cancel restore failed for %s/%s", team_id, member_id)
        raise HTTPException(status_code=502, detail=f"Failed to restore budget: {e}")

    boost.status = "cancelled"
    boost.reverted_at = datetime.now(timezone.utc)
    await db.flush()
    return serialize_boost(boost)


@router.get("/{team_id}/budget-boosts")
async def list_budget_boosts(
    team_id: str,
    limit: int = 50,
    user: CustomUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """All budget boosts for the team, newest first (active + history)."""
    await require_team_admin(user, team_id, litellm_db)
    limit = max(1, min(limit, 200))
    rows = (
        await db.execute(
            select(CustomMemberBudgetBoost)
            .where(CustomMemberBudgetBoost.team_id == team_id)
            .order_by(CustomMemberBudgetBoost.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return {"boosts": [serialize_boost(r) for r in rows]}
```

`BaseModel`, `get_db`, `get_litellm_client`, `LiteLLMClient`, `require_team_admin`, `get_current_user`, `CustomUser`, `AsyncSession`, `HTTPException`, `Depends` are already imported in teams.py. `select`, `status`, `uuid`, `logging`/`logger`, `datetime`/`timezone`, and the two new module imports come from Step 4.1 above.

- [ ] **Step 5: Run tests and gates**

```bash
cd backend && .venv/bin/python -m pytest tests/test_member_budget_boost.py -q
cd backend && .venv/bin/python -m pytest tests/ -q 2>&1 | tail -3
cd backend && .venv/bin/ruff check app/ tests/ 2>&1 | tail -2
```

Expected: new file green; suite baseline unchanged; ruff 78.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/member_budget_boost.py backend/app/api/teams.py backend/tests/test_member_budget_boost.py
git commit -m "feat(teams): create/cancel/list member budget boosts

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Auto-revert worker job

**Files:**
- Create: `backend/app/jobs/expire_budget_boosts.py`
- Modify: `backend/app/worker.py`
- Test: `backend/tests/test_expire_budget_boosts.py`

**Interfaces:**
- Consumes: `CustomMemberBudgetBoost` (Task 1); `LiteLLMClient().update_team_member(...)`; `async_session_factory` / `litellm_session_factory` from `app.db.session`.
- Produces: `async revert_expired_boosts(now: datetime) -> int` (returns count reverted) and `async budget_boost_loop(interval_seconds: int) -> None` in `app.jobs.expire_budget_boosts`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_expire_budget_boosts.py`:

```python
"""Worker: revert expired member budget boosts."""

import types
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.jobs.expire_budget_boosts import revert_expired_boosts

NOW = datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)


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
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd backend && .venv/bin/python -m pytest tests/test_expire_budget_boosts.py -q
```

Expected: ImportError (`revert_expired_boosts` missing).

- [ ] **Step 3: Write the job**

Create `backend/app/jobs/expire_budget_boosts.py`:

```python
"""Cron job: revert member budget boosts whose window has ended.

Selects active boosts with expires_at <= now and restores each member's
snapshotted budget via LiteLLM. If the membership is gone, marks reverted
without an API call. On LiteLLM failure the boost stays active for the next
tick. Revert is unconditional — manual edits during the window are overwritten.
"""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select, text

from app.clients.litellm import LiteLLMClient
from app.db.models.custom_member_budget_boost import CustomMemberBudgetBoost
from app.db.session import async_session_factory, litellm_session_factory

logger = logging.getLogger(__name__)


async def _membership_exists(litellm_db, team_id: str, user_id: str) -> bool:
    result = await litellm_db.execute(
        text(
            'SELECT 1 FROM "LiteLLM_TeamMembership" '
            "WHERE team_id = :team_id AND user_id = :user_id"
        ),
        {"team_id": team_id, "user_id": user_id},
    )
    return result.scalar() is not None


async def revert_expired_boosts(now: datetime) -> int:
    """Revert all active boosts with expires_at <= now. Returns count reverted."""
    reverted = 0
    async with async_session_factory() as db:
        expired = (
            await db.execute(
                select(CustomMemberBudgetBoost).where(
                    CustomMemberBudgetBoost.status == "active",
                    CustomMemberBudgetBoost.expires_at <= now,
                )
            )
        ).scalars().all()
        if not expired:
            return 0

        litellm = LiteLLMClient()
        async with litellm_session_factory() as litellm_db:
            for boost in expired:
                try:
                    if await _membership_exists(litellm_db, boost.team_id, boost.user_id):
                        await litellm.update_team_member(
                            boost.team_id,
                            boost.user_id,
                            max_budget_in_team=boost.original_max_budget,
                        )
                    boost.status = "reverted"
                    boost.reverted_at = datetime.now(timezone.utc)
                    reverted += 1
                except Exception:  # noqa: BLE001 — leave active, retry next tick
                    logger.exception(
                        "Budget boost revert failed for %s/%s", boost.team_id, boost.user_id
                    )
        await db.commit()
    return reverted


async def budget_boost_loop(interval_seconds: int) -> None:
    logger.info("Starting budget-boost revert loop (interval=%ss)", interval_seconds)
    while True:
        try:
            n = await revert_expired_boosts(datetime.now(timezone.utc))
            if n:
                logger.info("Reverted %s expired budget boost(s)", n)
        except Exception:  # noqa: BLE001 — never let the loop die
            logger.exception("Budget-boost revert tick failed")
        await asyncio.sleep(interval_seconds)
```

Note: the test's `_db_with` returns the select result then one membership result per boost; the `await db.commit()` sits outside the litellm block so a per-boost API failure still commits the successfully-reverted siblings.

- [ ] **Step 4: Wire into `backend/app/worker.py`**

Add the import next to the other job imports:

```python
from app.jobs.expire_budget_boosts import budget_boost_loop
```

and add to the `asyncio.gather(...)` call:

```python
        budget_boost_loop(interval_seconds=300),
```

- [ ] **Step 5: Run tests and gates**

```bash
cd backend && .venv/bin/python -m pytest tests/test_expire_budget_boosts.py -q
cd backend && .venv/bin/python -m pytest tests/ -q 2>&1 | tail -3
cd backend && .venv/bin/ruff check app/ tests/ 2>&1 | tail -2
```

Expected: new file green (3 tests); suite baseline unchanged; ruff 78.

- [ ] **Step 6: Commit**

```bash
git add backend/app/jobs/expire_budget_boosts.py backend/app/worker.py backend/tests/test_expire_budget_boosts.py
git commit -m "feat(worker): revert expired member budget boosts

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Frontend hooks, types, boost dialog + active badge + cancel

**Files:**
- Modify: `frontend/src/types/index.ts` (add `MemberBudgetBoost`)
- Modify: `frontend/src/hooks/use-api.ts` (three hooks)
- Modify: `frontend/src/app/(app)/teams/[teamId]/page.tsx` (members tab: boost action, badge, cancel)
- Modify: `frontend/messages/en.json`, `frontend/messages/ko.json` (`teams` namespace)

**Interfaces:**
- Consumes: backend JSON from Task 2 (`serialize_boost` shape); `useTeamMembers` (existing).
- Produces: `useTeamBudgetBoosts(teamId)`, `useCreateBudgetBoost()`, `useCancelBudgetBoost()`, `MemberBudgetBoost` — Task 5's history card consumes `useTeamBudgetBoosts`.

- [ ] **Step 1: Baseline lint**

```bash
cd frontend && npm run lint 2>&1 | tail -5
```

Record counts (baseline 4 errors / 13 warnings).

- [ ] **Step 2: Type**

In `frontend/src/types/index.ts`, add:

```ts
export interface MemberBudgetBoost {
  id: string;
  team_id: string;
  user_id: string;
  original_max_budget: number;
  boost_max_budget: number;
  expires_at: string | null;
  status: "active" | "reverted" | "cancelled";
  reverted_at: string | null;
  created_by: string | null;
  created_at: string | null;
}
```

- [ ] **Step 3: Hooks**

In `frontend/src/hooks/use-api.ts`, add (near the other team hooks; import `MemberBudgetBoost` in the existing `@/types` import if hooks reference the type — otherwise the fetch generic carries it):

```ts
export function useTeamBudgetBoosts(teamId: string) {
  return useQuery({
    queryKey: ["teams", teamId, "budget-boosts"],
    queryFn: () =>
      apiFetch<{ boosts: MemberBudgetBoost[] }>(`/api/teams/${teamId}/budget-boosts`).then(
        (r) => r.boosts,
      ),
    enabled: !!teamId,
  });
}

export function useCreateBudgetBoost() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      teamId,
      userId,
      max_budget,
      expires_at,
    }: {
      teamId: string;
      userId: string;
      max_budget: number;
      expires_at: string;
    }) =>
      apiFetch<MemberBudgetBoost>(`/api/teams/${teamId}/members/${userId}/budget-boost`, {
        method: "POST",
        body: JSON.stringify({ max_budget, expires_at }),
      }),
    onSuccess: (_d, v) => {
      qc.invalidateQueries({ queryKey: ["teams", v.teamId, "budget-boosts"] });
      qc.invalidateQueries({ queryKey: ["teams", v.teamId, "members"] });
    },
  });
}

export function useCancelBudgetBoost() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ teamId, userId }: { teamId: string; userId: string }) =>
      apiFetch<MemberBudgetBoost>(`/api/teams/${teamId}/members/${userId}/budget-boost`, {
        method: "DELETE",
      }),
    onSuccess: (_d, v) => {
      qc.invalidateQueries({ queryKey: ["teams", v.teamId, "budget-boosts"] });
      qc.invalidateQueries({ queryKey: ["teams", v.teamId, "members"] });
    },
  });
}
```

Add `MemberBudgetBoost` to the existing `import type { ... } from "@/types";` list. Match the members-query key to whatever `useTeamMembers` actually uses (grep `useTeamMembers` — if its key root differs from `["teams", teamId, "members"]`, use that root so the invalidation lands).

- [ ] **Step 4: i18n keys**

In `frontend/messages/en.json` `"teams"` object, add:

```json
"boostBtn": "Boost budget",
"boostDialogTitle": "Temporary budget boost",
"boostDialogDesc": "Raise this member's budget until the end time, then it reverts automatically.",
"boostAmountLabel": "Boosted budget (USD)",
"boostExpiresLabel": "Ends at",
"boostSubmit": "Start boost",
"boostSuccess": "Budget boost started.",
"boostFail": "Failed to start the boost.",
"boostCancelBtn": "Cancel boost",
"boostCancelSuccess": "Boost cancelled and budget restored.",
"boostCancelFail": "Failed to cancel the boost.",
"boostActiveBadge": "Boosted until {date}",
"boostUnlimitedDisabled": "Set a budget for this member before boosting.",
"boostHistoryTitle": "Budget boost history",
"boostHistoryEmpty": "No budget boosts yet.",
"boostColMember": "Member",
"boostColChange": "Original → Boost",
"boostColExpires": "Ends",
"boostColStatus": "Status",
"boostColBy": "By",
"boostStatusActive": "Active",
"boostStatusReverted": "Reverted",
"boostStatusCancelled": "Cancelled",
"boostStatusPending": "Revert pending"
```

In `frontend/messages/ko.json` `"teams"` object, add:

```json
"boostBtn": "예산 부스트",
"boostDialogTitle": "기간 한정 예산 부스트",
"boostDialogDesc": "종료 시각까지 이 멤버의 예산을 올리고, 이후 자동으로 원래대로 되돌립니다.",
"boostAmountLabel": "부스트 예산 (USD)",
"boostExpiresLabel": "종료 시각",
"boostSubmit": "부스트 시작",
"boostSuccess": "예산 부스트를 시작했습니다.",
"boostFail": "부스트 시작에 실패했습니다.",
"boostCancelBtn": "부스트 취소",
"boostCancelSuccess": "부스트를 취소하고 예산을 되돌렸습니다.",
"boostCancelFail": "부스트 취소에 실패했습니다.",
"boostActiveBadge": "{date}까지 부스트",
"boostUnlimitedDisabled": "부스트하려면 먼저 이 멤버의 예산을 설정하세요.",
"boostHistoryTitle": "예산 부스트 히스토리",
"boostHistoryEmpty": "아직 예산 부스트가 없습니다.",
"boostColMember": "멤버",
"boostColChange": "원래 → 부스트",
"boostColExpires": "종료",
"boostColStatus": "상태",
"boostColBy": "요청자",
"boostStatusActive": "진행 중",
"boostStatusReverted": "원복됨",
"boostStatusCancelled": "취소됨",
"boostStatusPending": "원복 대기"
```

- [ ] **Step 5: Members-tab wiring**

In the members tab component of `frontend/src/app/(app)/teams/[teamId]/page.tsx`:

1. Near the other hooks in that component, add:

```tsx
  const { data: boosts } = useTeamBudgetBoosts(teamId);
  const createBoost = useCreateBudgetBoost();
  const cancelBoost = useCancelBudgetBoost();
  const activeBoostByUser = new Map(
    (boosts ?? []).filter((b) => b.status === "active").map((b) => [b.user_id, b]),
  );
  const [boostTarget, setBoostTarget] = useState<{ userId: string; currentBudget: number | null } | null>(null);
  const [boostAmount, setBoostAmount] = useState("");
  const [boostExpires, setBoostExpires] = useState("");
```

Import the three hooks from `@/hooks/use-api` (add to the existing import).

2. In the member row's action area (next to the existing budget-change control found via `budgetChangeTarget`), add a boost button + active badge. Locate the budget cell/menu and insert:

```tsx
                          {activeBoostByUser.has(member.user_id) ? (
                            <Badge variant="outline" className="gap-1">
                              {t("boostActiveBadge", {
                                date: new Date(
                                  activeBoostByUser.get(member.user_id)!.expires_at ?? "",
                                ).toLocaleDateString(),
                              })}
                              <button
                                type="button"
                                className="ml-1 rounded-full p-0.5 hover:bg-muted-foreground/20"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  cancelBoost.mutate(
                                    { teamId, userId: member.user_id },
                                    {
                                      onSuccess: () => toast.success(t("boostCancelSuccess")),
                                      onError: (err) =>
                                        toast.error(err instanceof Error ? err.message : t("boostCancelFail")),
                                    },
                                  );
                                }}
                              >
                                <X className="size-3" />
                              </button>
                            </Badge>
                          ) : (
                            <Button
                              variant="ghost"
                              size="sm"
                              disabled={member.total_max_budget === null}
                              title={member.total_max_budget === null ? t("boostUnlimitedDisabled") : undefined}
                              onClick={(e) => {
                                e.stopPropagation();
                                setBoostTarget({ userId: member.user_id, currentBudget: member.total_max_budget });
                                setBoostAmount("");
                                setBoostExpires("");
                              }}
                            >
                              {t("boostBtn")}
                            </Button>
                          )}
```

(Use the existing `TeamMember` field for the member's budget — grep the interface; the plan assumes `total_max_budget: number | null`. If the field is named differently, use that name consistently in the `disabled`/`currentBudget` checks.)

3. Add the boost dialog near the existing budget-change dialog (mirror its `Dialog` structure):

```tsx
      <Dialog open={!!boostTarget} onOpenChange={(o) => !o && setBoostTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("boostDialogTitle")}</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">{t("boostDialogDesc")}</p>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <label className="text-sm font-medium">{t("boostAmountLabel")}</label>
              <Input
                type="number"
                min={0}
                value={boostAmount}
                onChange={(e) => setBoostAmount(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium">{t("boostExpiresLabel")}</label>
              <Input
                type="datetime-local"
                value={boostExpires}
                onChange={(e) => setBoostExpires(e.target.value)}
              />
            </div>
          </div>
          <DialogFooter>
            <Button
              disabled={!boostAmount || !boostExpires || createBoost.isPending}
              onClick={() => {
                if (!boostTarget) return;
                createBoost.mutate(
                  {
                    teamId,
                    userId: boostTarget.userId,
                    max_budget: Number(boostAmount),
                    expires_at: new Date(boostExpires).toISOString(),
                  },
                  {
                    onSuccess: () => {
                      toast.success(t("boostSuccess"));
                      setBoostTarget(null);
                    },
                    onError: (err) => toast.error(err instanceof Error ? err.message : t("boostFail")),
                  },
                );
              }}
            >
              {createBoost.isPending ? <Loader2 className="size-4 animate-spin" /> : t("boostSubmit")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
```

Ensure `Dialog`, `DialogContent`, `DialogHeader`, `DialogTitle`, `DialogFooter`, `Input`, `Button`, `Badge`, `Loader2`, `X`, `toast`, `useState` are already imported in this file (they are used by the existing budget dialog) — add only what is missing.

- [ ] **Step 6: Gates**

```bash
cd frontend && npm run lint 2>&1 | tail -5
cd frontend && npm run build 2>&1 | tail -10
```

Expected: lint counts equal baseline (0 new); build succeeds.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/hooks/use-api.ts "frontend/src/app/(app)/teams/[teamId]/page.tsx" frontend/messages/en.json frontend/messages/ko.json
git commit -m "feat(teams): member budget boost dialog, active badge, cancel

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Budget boost history card

**Files:**
- Create: `frontend/src/components/team-boost-history.tsx`
- Modify: `frontend/src/app/(app)/teams/[teamId]/page.tsx` (render the card in the members tab)

**Interfaces:**
- Consumes: `useTeamBudgetBoosts(teamId)` and `MemberBudgetBoost` (Task 4); `Card`/`Table` UI primitives.
- Produces: `TeamBoostHistory` component (default or named export) taking `{ teamId: string }`.

- [ ] **Step 1: Write the history component**

Create `frontend/src/components/team-boost-history.tsx`:

```tsx
"use client";

import { useTranslations } from "next-intl";

import { useTeamBudgetBoosts } from "@/hooks/use-api";
import type { MemberBudgetBoost } from "@/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

// An active boost whose end time has passed is awaiting the worker (~5 min).
function effectiveStatus(b: MemberBudgetBoost): MemberBudgetBoost["status"] | "pending" {
  if (b.status === "active" && b.expires_at && new Date(b.expires_at) <= new Date()) {
    return "pending";
  }
  return b.status;
}

export function TeamBoostHistory({ teamId }: { teamId: string }) {
  const t = useTranslations("teams");
  const { data: boosts } = useTeamBudgetBoosts(teamId);

  const statusLabel: Record<string, string> = {
    active: t("boostStatusActive"),
    reverted: t("boostStatusReverted"),
    cancelled: t("boostStatusCancelled"),
    pending: t("boostStatusPending"),
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{t("boostHistoryTitle")}</CardTitle>
      </CardHeader>
      <CardContent>
        {!boosts || boosts.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("boostHistoryEmpty")}</p>
        ) : (
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("boostColMember")}</TableHead>
                  <TableHead>{t("boostColChange")}</TableHead>
                  <TableHead className="hidden sm:table-cell">{t("boostColExpires")}</TableHead>
                  <TableHead>{t("boostColStatus")}</TableHead>
                  <TableHead className="hidden md:table-cell">{t("boostColBy")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {boosts.map((b) => {
                  const st = effectiveStatus(b);
                  return (
                    <TableRow key={b.id}>
                      <TableCell className="font-medium">{b.user_id}</TableCell>
                      <TableCell className="font-mono text-xs">
                        ${b.original_max_budget} → ${b.boost_max_budget}
                      </TableCell>
                      <TableCell className="hidden sm:table-cell text-xs">
                        {b.expires_at ? new Date(b.expires_at).toLocaleString() : "-"}
                      </TableCell>
                      <TableCell>
                        <Badge variant={st === "active" ? "default" : "secondary"}>
                          {statusLabel[st]}
                        </Badge>
                      </TableCell>
                      <TableCell className="hidden md:table-cell text-xs text-muted-foreground">
                        {b.created_by ?? "-"}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 2: Render it in the members tab**

In `frontend/src/app/(app)/teams/[teamId]/page.tsx`, import it:

```tsx
import { TeamBoostHistory } from "@/components/team-boost-history";
```

and render it at the bottom of the members-tab content (after the members table / pagination block, inside the same `<div className="space-y-4">` the members component returns):

```tsx
      <TeamBoostHistory teamId={teamId} />
```

- [ ] **Step 3: Gates**

```bash
cd frontend && npm run lint 2>&1 | tail -5
cd frontend && npm run build 2>&1 | tail -10
python3 -c "
import json
for lang in ('en','ko'):
    s = json.load(open(f'frontend/messages/{lang}.json'))['teams']
    for k in ('boostHistoryTitle','boostStatusPending','boostColChange','boostBtn','boostActiveBadge'):
        assert k in s, f'{lang}:{k}'
print('i18n OK')
"
```

Expected: lint equals baseline (0 new); build succeeds; `i18n OK`.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/team-boost-history.tsx "frontend/src/app/(app)/teams/[teamId]/page.tsx"
git commit -m "feat(teams): budget boost history card on the members tab

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```
