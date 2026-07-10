# Time-Limited Budget-Increase Requests — Design

**Date:** 2026-07-10
**Status:** Approved — user said "작업 시작" (start). Q1 answered
(temporary-default, permanent possible); Q2 assumed = approve-as-requested
(no adjust-at-approval UI), noted below.

## Problem

A team member can request a budget increase (`request_type="budget"` on
`custom_team_join_requests`). On approval the admin's action is
`update_team_member(max_budget_in_team=requested)` — a **permanent** change
with no period and no revert. The member-budget-boost feature (#199) already
provides a time-limited raise that auto-reverts at expiry (via
`custom_member_budget_boost` + the `expire_budget_boosts` worker), but the
request flow can't use it: the requester can't ask for a *temporary* increase.

## Decision

Let the requester set a **period** on a budget-increase request; on approval,
apply it as a **member budget boost** (auto-reverts at expiry). Defaults:
amount **$20**, period **30 days (≈1 month)**. An empty period = a permanent
increase (today's behavior).

**Q1 (answered):** temporary-by-default, permanent still possible (empty
period).
**Q2 (assumed, pending review):** the admin **approves as requested** — the
member's requested amount + period apply as-is; there is no adjust-at-approval
UI. An admin who disagrees rejects, and the member re-requests. (If the user
wants adjust-at-approval, the approve endpoint + review dialog gain optional
amount/duration overrides — a small extension.)

### Data model (migration 040_budget_request_duration)

Add one nullable column to `custom_team_join_requests`:

- `requested_duration_days INTEGER NULL` — the requested period in days. NULL =
  permanent. Only meaningful for `request_type="budget"`.

Migration `040_budget_request_duration` revises `039_llmd_stack_chart_source`
(origin/main's current head, after #199–#201 merged). No ordering hazard
remains.

### Shared boost service (DRY refactor)

The boost-creation logic currently lives inline in
`teams.py::create_budget_boost` (validate → `_active_boost_exists` 409 →
snapshot `resolve_effective_budget` → reserve row via `db.flush()`
(IntegrityError→409) → `litellm.update_team_member` (Exception→502) → row
`active`). Extract it verbatim into
`backend/app/services/member_budget_boost.py`:

```
async def apply_member_budget_boost(
    db, litellm, litellm_db, *, team_id, user_id,
    boost_max_budget: float, expires_at: datetime, created_by: str | None,
) -> CustomMemberBudgetBoost   # raises HTTPException(400/409/502)
```

It preserves the TOCTOU-safe ordering (reserve the boost row before the
LiteLLM call so a race yields 409 and a LiteLLM failure rolls the row back).
`teams.create_budget_boost` is refactored to call it (behavior-identical, its
tests unchanged). The new approval path calls the same function.

### Request creation (`POST /api/team-requests/budget`)

- Request model gains `requested_duration_days: int | None = 30` (default 1
  month). Validate `> 0` when provided (400 otherwise). Store on the row.
- Amount default ($20) is a frontend default; the backend still requires
  `requested_budget > 0`.

### Approval (`POST /api/team-requests/{id}/approve`, budget type)

Replace the single permanent `update_team_member` call with:

- If `req.requested_duration_days` is set AND the member's effective budget
  (`resolve_effective_budget`) is not None:
  `expires_at = now + timedelta(days=req.requested_duration_days)`, then
  `apply_member_budget_boost(..., boost_max_budget=req.requested_budget,
  expires_at=expires_at, created_by=user.user_id)`. The existing worker
  reverts at expiry.
  - A 409 from `apply_member_budget_boost` (member already has an active
    boost) surfaces to the admin — the request stays pending.
- Else (no duration, OR the member has no current budget to revert to):
  permanent `update_team_member(max_budget_in_team=req.requested_budget)` —
  today's behavior.

Set the request `APPROVED` after the apply succeeds (same
flush/get_db-commit semantics as today).

### Serialize

`_request_to_dict` includes `requested_duration_days` so the admin review
list shows whether the request is temporary and for how long.

### Frontend (team detail page)

- **Budget request dialog** (member-facing, `BudgetRequestButton`): amount
  input defaulting to **20**; a period input defaulting to **30** (days) with
  a "permanent" affordance (clear/uncheck → send `null`); helper text
  "기본 30일 (≈1개월), 비우면 영구". Submit sends `requested_duration_days`.
- **Admin request list**: show the requested period (e.g. "30일" or "영구")
  next to the amount so the approver knows it's temporary before approving.
- Hooks/types gain `requested_duration_days`. en/ko i18n.

## Non-goals

- No adjust-at-approval UI (Q2 assumption — approve as requested). Revisit if
  the user wants it.
- No change to the boost worker / revert policy (unconditional snapshot
  restore, from #199).
- No period on join requests (budget requests only).

## Edge notes

- Member with no current budget (effective None) + a period: a temporary boost
  can't revert to "unlimited", so the approval falls back to a permanent set
  (documented; matches the boost feature's own "set a budget first" limit
  without blocking the approval).
- Approving a temporary request when the member already has an active boost →
  409 (one active boost per member, from #199's partial unique index); the
  request stays pending for a later retry.

## Verification

- Backend: new tests — request stores `requested_duration_days`; approval with
  a period calls `apply_member_budget_boost` with `expires_at ≈ now+Nd`;
  approval without a period (or no effective budget) does the permanent
  `update_team_member`; approval when an active boost exists → 409; the
  extracted `apply_member_budget_boost` preserves the reserve-before-apply
  ordering (existing boost tests still green). pytest 0 NEW failures
  (baseline 21), ruff 0 NEW (baseline 78). Migration applies (alembic → 040).
- Frontend: lint 0 NEW (baseline 4 errors/13 warnings), build passes; the
  dialog defaults to $20 / 30 days and round-trips permanent (null).
