# Time-Limited Budget-Increase Requests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a member set a period on a budget-increase request; on approval, apply it as a member budget boost (auto-reverts at expiry) instead of a permanent change. Defaults: amount $20, period 30 days.

**Architecture:** Add `requested_duration_days` to `custom_team_join_requests` (migration 040). Extract the boost-creation logic from `teams.create_budget_boost` into a shared `apply_member_budget_boost` service so the request-approval path reuses it. The approval handler branches: period + a revertable budget → boost; else → today's permanent `update_team_member`. Frontend budget dialog gains an amount default ($20) and a clearable period (default 30 days). Spec: `docs/superpowers/specs/2026-07-10-budget-request-duration-design.md`.

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic; Next.js + next-intl + TanStack Query.

## Global Constraints

- Period semantics: `requested_duration_days` NULL = permanent (today's behavior); a positive int = temporary (approve as boost). Defaults live in the frontend: amount 20, period 30.
- Approval branch: if `req.requested_duration_days` is set AND `resolve_effective_budget(litellm_db, team_id, requester_id)` is not None → `apply_member_budget_boost(..., boost_max_budget=req.requested_budget, expires_at=now+Nd)`; else → permanent `litellm.update_team_member(max_budget_in_team=req.requested_budget)`.
- Reuse, don't duplicate: the boost logic (validate → `_active_boost_exists` 409 → snapshot → reserve row via `db.flush()` IntegrityError→409 → `litellm.update_team_member` Exception→502) moves into `app/services/member_budget_boost.py::apply_member_budget_boost`; `teams.create_budget_boost` calls it (behavior-identical, its tests unchanged). The TOCTOU-safe reserve-before-apply ordering must be preserved.
- One active boost per (team, user) — a 409 from the service on approval keeps the request PENDING.
- Migration `040_budget_request_duration`, `down_revision="039_llmd_stack_chart_source"` (verify against that file's `revision`).
- Backend gates: `cd backend && .venv/bin/python -m pytest tests/ -q` 0 NEW failures (baseline 21); `.venv/bin/ruff check app/ tests/` 0 NEW (baseline 78). Use `datetime.UTC` not `timezone.utc` (repo ruff UP017). Imports top-of-file.
- Frontend gates: `cd frontend && npm run lint` 0 NEW (baseline 4 errors/13 warnings); `npm run build` passes.
- Branch `feat/budget-request-duration` (already checked out, rebased on current origin/main). Commits end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Migration 040 + model column

**Files:**
- Create: `backend/migrations/versions/040_budget_request_duration.py`
- Modify: `backend/app/db/models/custom_team_join_request.py`

**Interfaces:**
- Produces: `CustomTeamJoinRequest.requested_duration_days: Mapped[int | None]`, consumed by Task 3.

- [ ] **Step 1: Write the migration**

Create `backend/migrations/versions/040_budget_request_duration.py`:

```python
"""Requested period (days) on a budget-increase request.

NULL = permanent increase (today's behavior); a positive int = a temporary
increase applied as a member budget boost on approval.

Revision ID: 040_budget_request_duration
Revises: 039_llmd_stack_chart_source
"""

import sqlalchemy as sa
from alembic import op

revision = "040_budget_request_duration"
down_revision = "039_llmd_stack_chart_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_team_join_requests",
        sa.Column("requested_duration_days", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("custom_team_join_requests", "requested_duration_days")
```

Before committing, open `backend/migrations/versions/039_llmd_stack_chart_source.py` and confirm `revision = "039_llmd_stack_chart_source"` matches this file's `down_revision`; fix if different.

- [ ] **Step 2: Add the model column**

In `backend/app/db/models/custom_team_join_request.py`, add after the `requested_budget` column (line ~34), and add `Integer` to the sqlalchemy import (`from sqlalchemy import DateTime, Enum, Float, Integer, String, Text, func`):

```python
    # Requested period for a budget increase, in days. NULL = permanent; a
    # positive value = temporary (applied as a member budget boost on approval).
    requested_duration_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
```

- [ ] **Step 3: Apply the migration + verify**

```bash
cd backend && APP_DATABASE_URL="postgresql+asyncpg://llmproxy:dbpassword9090@localhost:5432/litellm_portal" .venv/bin/alembic upgrade head
cd backend && APP_DATABASE_URL="postgresql+asyncpg://llmproxy:dbpassword9090@localhost:5432/litellm_portal" .venv/bin/alembic current
```

Expected: runs `039… -> 040_budget_request_duration`; `current` prints `040_budget_request_duration (head)`.

- [ ] **Step 4: Backend gates**

```bash
cd backend && .venv/bin/python -m pytest tests/ -q 2>&1 | tail -3
cd backend && .venv/bin/ruff check app/ tests/ 2>&1 | tail -2
```

Expected: 21 pre-existing failures (0 new), ruff 78 (0 new).

- [ ] **Step 5: Commit**

```bash
git add backend/migrations/versions/040_budget_request_duration.py backend/app/db/models/custom_team_join_request.py
git commit -m "feat(db): requested_duration_days on budget-increase requests

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Extract `apply_member_budget_boost` service (DRY refactor)

**Files:**
- Modify: `backend/app/services/member_budget_boost.py` (add the function)
- Modify: `backend/app/api/teams.py` (`create_budget_boost` calls the service)
- Test: `backend/tests/test_member_budget_boost.py` (add a direct-service test; existing endpoint tests must stay green)

**Interfaces:**
- Consumes: `resolve_effective_budget` (same module), `CustomMemberBudgetBoost`, `LiteLLMClient`.
- Produces: `async apply_member_budget_boost(db, litellm, litellm_db, *, team_id: str, user_id: str, boost_max_budget: float, expires_at: datetime, created_by: str | None) -> CustomMemberBudgetBoost` — raises `HTTPException` (400 non-positive / non-future, 400 no revertable budget, 409 active boost exists, 502 LiteLLM failure). Consumed by `teams.create_budget_boost` (Task 2) and `team_requests.approve_request` (Task 3).

- [ ] **Step 1: Write the failing test**

In `backend/tests/test_member_budget_boost.py`, add:

```python
from datetime import UTC, datetime, timedelta

from app.services.member_budget_boost import apply_member_budget_boost


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

    litellm = MagicMock(); litellm.update_team_member = AsyncMock()
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

    litellm = MagicMock(); litellm.update_team_member = AsyncMock()
    with patch("app.services.member_budget_boost.resolve_effective_budget", AsyncMock(return_value=10.0)), \
         patch("app.services.member_budget_boost._active_boost_exists", AsyncMock(return_value=True)):
        with pytest.raises(HTTPException) as e:
            await apply_member_budget_boost(
                mock_db, litellm, MagicMock(),
                team_id="t", user_id="u", boost_max_budget=100.0,
                expires_at=datetime.now(UTC) + timedelta(days=1), created_by="a",
            )
    assert e.value.status_code == 409
```

Ensure the test file imports `pytest`, `HTTPException` (`from fastapi import HTTPException`), and `patch` (`from unittest.mock import patch`) at the top if not already present.

- [ ] **Step 2: Run to verify it fails**

```bash
cd backend && .venv/bin/python -m pytest tests/test_member_budget_boost.py -q
```

Expected: ImportError (`apply_member_budget_boost` missing).

- [ ] **Step 3: Implement the service**

In `backend/app/services/member_budget_boost.py`, add the needed imports at the top, MERGING into existing import lines (this module already imports `CustomMemberBudgetBoost` and likely `from sqlalchemy import text` and `AsyncSession` — do NOT duplicate; add `select` to the existing sqlalchemy line, keep `text`). Net new/merged imports:

```python
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select, text          # add `select` to the existing line
from sqlalchemy.exc import IntegrityError

from app.clients.litellm import LiteLLMClient
```

(If a name is already imported, leave it — the goal is `uuid`, `UTC`/`datetime`, `HTTPException`, `select`, `IntegrityError`, `LiteLLMClient` all available, with no duplicate import lines that ruff I001 would flag.)

Add an `_active_boost_exists` helper (moved here so the service is self-contained) and the `apply_member_budget_boost` function:

```python
async def _active_boost_exists(db, team_id: str, user_id: str) -> bool:
    row = (
        await db.execute(
            select(CustomMemberBudgetBoost).where(
                CustomMemberBudgetBoost.team_id == team_id,
                CustomMemberBudgetBoost.user_id == user_id,
                CustomMemberBudgetBoost.status == "active",
            )
        )
    ).scalar_one_or_none()
    return row is not None


async def apply_member_budget_boost(
    db,
    litellm: LiteLLMClient,
    litellm_db,
    *,
    team_id: str,
    user_id: str,
    boost_max_budget: float,
    expires_at: datetime,
    created_by: str | None,
) -> CustomMemberBudgetBoost:
    """Snapshot the member's effective budget, reserve the boost row, then apply
    the raised budget via LiteLLM. Reserve-before-apply so a race yields 409 and
    a LiteLLM failure rolls the reserved row back (get_db rolls back on the
    raised HTTPException). Raises 400 (non-positive / non-future / no revertable
    budget), 409 (active boost exists), 502 (LiteLLM failure)."""
    if boost_max_budget <= 0:
        raise HTTPException(status_code=400, detail="Boosted budget must be positive")
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= datetime.now(UTC):
        raise HTTPException(status_code=400, detail="Boost end time must be in the future")
    if await _active_boost_exists(db, team_id, user_id):
        raise HTTPException(status_code=409, detail="An active boost already exists for this member")

    original = await resolve_effective_budget(litellm_db, team_id, user_id)
    if original is None:
        raise HTTPException(
            status_code=400,
            detail="Member has no budget limit to boost — set a budget first",
        )

    boost = CustomMemberBudgetBoost(
        id=uuid.uuid4(),
        team_id=team_id,
        user_id=user_id,
        original_max_budget=original,
        boost_max_budget=boost_max_budget,
        expires_at=expires_at,
        status="active",
        created_by=created_by,
    )
    db.add(boost)
    try:
        await db.flush()
    except IntegrityError:
        raise HTTPException(status_code=409, detail="An active boost already exists for this member")

    try:
        await litellm.update_team_member(team_id, user_id, max_budget_in_team=boost_max_budget)
    except Exception as e:  # noqa: BLE001 — surfaced as 502; get_db rolls back the reserved row
        raise HTTPException(status_code=502, detail=f"Failed to apply boosted budget: {e}")

    await db.refresh(boost)
    return boost
```

- [ ] **Step 4: Refactor `teams.create_budget_boost` to call the service**

In `backend/app/api/teams.py`, replace the body of `create_budget_boost` (from the `if body.max_budget <= 0:` validation through `return serialize_boost(boost)`) with:

```python
    await require_team_admin(user, team_id, litellm_db)
    boost = await apply_member_budget_boost(
        db, litellm, litellm_db,
        team_id=team_id, user_id=member_id,
        boost_max_budget=body.max_budget, expires_at=body.expires_at,
        created_by=user.user_id,
    )
    return serialize_boost(boost)
```

Update the import to add `apply_member_budget_boost`:
`from app.services.member_budget_boost import apply_member_budget_boost, resolve_effective_budget, serialize_boost`

Delete the now-unused local `_active_boost_exists` in teams.py (it moved to the service) — but first grep for other callers:

```bash
grep -rn "_active_boost_exists" backend/app
```

Expected: after the edit, `teams.py` no longer references it; the only definition is in `member_budget_boost.py`. If teams.py has no other caller, delete its local `_active_boost_exists`. If ruff then flags `IntegrityError`/`resolve_effective_budget`/`CustomMemberBudgetBoost`/`UTC` as unused in teams.py, remove those imports too (grep each before removing — `resolve_effective_budget` may still be used elsewhere in teams.py; `datetime`/`UTC` likely still used by other endpoints).

- [ ] **Step 5: Run tests + gates**

```bash
cd backend && .venv/bin/python -m pytest tests/test_member_budget_boost.py -q
cd backend && .venv/bin/python -m pytest tests/ -q 2>&1 | tail -3
cd backend && .venv/bin/ruff check app/ tests/ 2>&1 | tail -2
```

Expected: new service tests + existing boost endpoint tests (test_create_boost_*, test_cancel_boost_*) all green; suite baseline unchanged; ruff 78.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/member_budget_boost.py backend/app/api/teams.py backend/tests/test_member_budget_boost.py
git commit -m "refactor(boost): extract apply_member_budget_boost; endpoint reuses it

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Request stores period; approval applies it as a boost

**Files:**
- Modify: `backend/app/api/team_requests.py`
- Test: `backend/tests/test_team_requests.py`

**Interfaces:**
- Consumes: Task 1's column; Task 2's `apply_member_budget_boost`; `resolve_effective_budget` (member_budget_boost).
- Produces: nothing downstream (frontend consumes the JSON shape).

- [ ] **Step 1: Write the failing tests**

FIRST read `backend/tests/test_team_requests.py` in full — specifically its existing budget-request create test and its approve test — and replicate their EXACT client construction, dependency overrides, and request-row setup (that harness is idiosyncratic to this file). Then add the three tests below, keeping the concrete assertions verbatim and filling the `...` scaffolding with that file's established pattern:

```python
async def test_create_budget_request_stores_duration(...):
    # POST /api/team-requests/budget with requested_duration_days=30 →
    # the persisted CustomTeamJoinRequest has requested_duration_days == 30.
    # (Use the file's existing pattern for building the client + asserting on
    # mock_db.add.call_args.args[0].)
    ...
    row = mock_db.add.call_args.args[0]
    assert row.requested_duration_days == 30


async def test_approve_budget_request_with_duration_applies_boost(...):
    # A pending budget request with requested_duration_days=30 and requested_budget=100.
    # Patch app.api.team_requests.apply_member_budget_boost (AsyncMock) and assert:
    #   approve → apply_member_budget_boost awaited once with boost_max_budget=100.0
    #   and expires_at within ~30 days of now; request status APPROVED.
    from unittest.mock import AsyncMock, patch
    ...
    with patch("app.api.team_requests.apply_member_budget_boost", AsyncMock()) as m:
        resp = await client.post(f"/api/team-requests/{req_id}/approve", json={})
    assert resp.status_code == 200
    kwargs = m.await_args.kwargs
    assert kwargs["boost_max_budget"] == 100.0
    assert 29 <= (kwargs["expires_at"] - datetime.now(UTC)).days <= 30


async def test_approve_budget_request_without_duration_is_permanent(...):
    # requested_duration_days is None → the permanent update_team_member path,
    # apply_member_budget_boost NOT called.
    from unittest.mock import AsyncMock, patch
    ...
    with patch("app.api.team_requests.apply_member_budget_boost", AsyncMock()) as m:
        resp = await client.post(f"/api/team-requests/{req_id}/approve", json={})
    assert resp.status_code == 200
    m.assert_not_awaited()
    mock_litellm.update_team_member.assert_awaited_once()
```

Fill the `...` using the exact fixture/setup style already in `test_team_requests.py` (look at how its existing approve tests build the request row and drive the client). Import `from datetime import UTC, datetime` at the top if absent.

- [ ] **Step 2: Run to verify they fail**

```bash
cd backend && .venv/bin/python -m pytest tests/test_team_requests.py -q
```

Expected: the duration/boost assertions fail against the current permanent-only code.

- [ ] **Step 3: Accept + store the period on create**

In `backend/app/api/team_requests.py`:

1. Extend the request model:

```python
class CreateBudgetRequest(BaseModel):
    team_id: str
    requested_budget: float
    message: str | None = None
    requested_duration_days: int | None = 30
```

2. In `create_budget_request`, validate and store it. After the existing `if body.requested_budget <= 0:` guard, add:

```python
    if body.requested_duration_days is not None and body.requested_duration_days <= 0:
        raise HTTPException(status_code=400, detail="Requested duration must be positive")
```

and add `requested_duration_days=body.requested_duration_days,` to the `CustomTeamJoinRequest(...)` constructor.

3. `_request_to_dict`: add `"requested_duration_days": r.requested_duration_days,`.

- [ ] **Step 4: Branch the approval on the period**

Add these imports at the top of `team_requests.py`:

```python
from datetime import UTC, datetime, timedelta

from app.services.member_budget_boost import apply_member_budget_boost, resolve_effective_budget
```

Replace the `elif req.request_type == "budget":` block (the single `await litellm.update_team_member(...)` call) with:

```python
    elif req.request_type == "budget":
        original = (
            await resolve_effective_budget(litellm_db, req.team_id, req.requester_id)
            if req.requested_duration_days
            else None
        )
        if req.requested_duration_days and original is not None:
            # Temporary increase → member budget boost (auto-reverts at expiry).
            await apply_member_budget_boost(
                db, litellm, litellm_db,
                team_id=req.team_id, user_id=req.requester_id,
                boost_max_budget=req.requested_budget,
                expires_at=datetime.now(UTC) + timedelta(days=req.requested_duration_days),
                created_by=user.user_id,
            )
        else:
            # Permanent increase (no period, or nothing to revert to).
            await litellm.update_team_member(
                req.team_id,
                req.requester_id,
                max_budget_in_team=req.requested_budget,
            )
```

Note: `apply_member_budget_boost` may raise 409 (member already has an active boost) — that propagates and the request stays PENDING (the status write below only runs on success), which is the intended behavior.

- [ ] **Step 5: Run tests + gates**

```bash
cd backend && .venv/bin/python -m pytest tests/test_team_requests.py -q
cd backend && .venv/bin/python -m pytest tests/ -q 2>&1 | tail -3
cd backend && .venv/bin/ruff check app/ tests/ 2>&1 | tail -2
```

Expected: new tests green (existing team_requests tests still pass — the join path and no-duration budget path are unchanged); suite baseline unchanged; ruff 78.

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/team_requests.py backend/tests/test_team_requests.py
git commit -m "feat(requests): budget request carries a period; approve applies it as a boost

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Frontend — period on the budget request dialog + review display

**Files:**
- Modify: `frontend/src/types/index.ts` (TeamJoinRequest + CreateBudgetRequestBody)
- Modify: `frontend/src/app/(app)/teams/[teamId]/page.tsx` (BudgetRequestButton dialog)
- Modify: `frontend/src/app/(app)/requests/page.tsx` (show the requested period)
- Modify: `frontend/messages/en.json`, `frontend/messages/ko.json`

**Interfaces:**
- Consumes: Task 3's JSON (`requested_duration_days` on the request; accepted on create).
- Produces: final task.

- [ ] **Step 1: Baseline lint**

```bash
cd frontend && npm run lint 2>&1 | tail -5
```

Record counts (baseline 4 errors / 13 warnings).

- [ ] **Step 2: Types**

`frontend/src/types/index.ts`:
- In `TeamJoinRequest` (after `requested_budget: number | null;`): `requested_duration_days: number | null;`
- In `CreateBudgetRequestBody` (after `requested_budget: number;`): `requested_duration_days?: number | null;`

- [ ] **Step 3: Dialog — amount default $20, period default 30, clearable**

In `frontend/src/app/(app)/teams/[teamId]/page.tsx`, the `BudgetRequestButton` component:

1. Default the amount to "20" and add a duration state:

```tsx
  const [amount, setAmount] = useState("20");
  const [durationDays, setDurationDays] = useState("30");
```

2. Add a period field after the amount `<div>` (before the reason field):

```tsx
          <div>
            <label className="text-sm font-medium">{t("budgetDurationLabel")}</label>
            <Input
              type="number"
              min="1"
              placeholder={t("budgetDurationPlaceholder")}
              value={durationDays}
              onChange={(e) => setDurationDays(e.target.value)}
            />
            <p className="text-xs text-muted-foreground mt-1">{t("budgetDurationHint")}</p>
          </div>
```

3. Submit — send `requested_duration_days` (empty → null = permanent):

```tsx
                {
                  team_id: teamId,
                  requested_budget: Number(amount),
                  message: message || undefined,
                  requested_duration_days: durationDays.trim() ? Number(durationDays) : null,
                },
```

4. Reset both on success: in `onSuccess`, after `setAmount("")`, add `setDurationDays("30");` (and change `setAmount("")` to `setAmount("20")` so the default persists for the next open).

- [ ] **Step 4: Show the requested period in the request review**

In `frontend/src/app/(app)/requests/page.tsx`, in the budget detail block (around line 272-276 where `budgetAmount` is shown), add a period line right after the amount `<div>`:

```tsx
                {(detailRequest.request_type ?? "join") === "budget" && (
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">{t("budgetDuration")}</span>
                    <span className="font-medium">
                      {detailRequest.requested_duration_days
                        ? t("budgetDurationDays", { days: detailRequest.requested_duration_days })
                        : t("budgetDurationPermanent")}
                    </span>
                  </div>
                )}
```

(Place it inside the existing `request_type === "budget"` conditional region, as a sibling of the amount row.)

- [ ] **Step 5: i18n keys**

Confirm the namespaces: the dialog uses `useTranslations` in `teams/[teamId]/page.tsx` (grep the component's `t` namespace — it is `teamDetail`); the requests page uses its own namespace (grep `useTranslations(` in `requests/page.tsx`). Add each key under the namespace its consumer uses.

For the dialog keys (teamDetail namespace) — `en.json`:

```json
"budgetDurationLabel": "Period (days)",
"budgetDurationPlaceholder": "30",
"budgetDurationHint": "Default 30 days (≈1 month). Leave empty for a permanent increase.",
```

`ko.json`:

```json
"budgetDurationLabel": "기간 (일)",
"budgetDurationPlaceholder": "30",
"budgetDurationHint": "기본 30일 (≈1개월). 비우면 영구 증액입니다.",
```

For the requests-page keys (its namespace) — `en.json`:

```json
"budgetDuration": "Period",
"budgetDurationDays": "{days} days",
"budgetDurationPermanent": "Permanent",
```

`ko.json`:

```json
"budgetDuration": "기간",
"budgetDurationDays": "{days}일",
"budgetDurationPermanent": "영구",
```

(Place the dialog keys next to the existing `budgetAmountLabel`/`budgetRequest*` keys, and the requests-page keys next to that page's `budgetAmount` key. If the dialog and the requests page share the same namespace, define each key once.)

- [ ] **Step 6: Gates**

```bash
cd frontend && npm run lint 2>&1 | tail -5
cd frontend && npm run build 2>&1 | tail -10
python3 -c "
import json
for lang in ('en','ko'):
    d = json.load(open(f'frontend/messages/{lang}.json'))
    flat = {k for v in d.values() if isinstance(v, dict) for k in v}
    for key in ('budgetDurationLabel','budgetDurationHint','budgetDuration','budgetDurationPermanent'):
        assert key in flat, f'{lang}:{key}'
print('i18n OK')
"
```

Expected: lint equals baseline (0 new); build succeeds; `i18n OK`.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/types/index.ts "frontend/src/app/(app)/teams/[teamId]/page.tsx" "frontend/src/app/(app)/requests/page.tsx" frontend/messages/en.json frontend/messages/ko.json
git commit -m "feat(frontend): budget request period ($20 / 30d default); show it in review

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```
