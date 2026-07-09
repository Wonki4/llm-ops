# Team Member Budget Boost — Design

**Date:** 2026-07-09
**Status:** Approved (user confirmed; history view added at user request; revert
policy: unconditional snapshot restore)

## Problem

A team member's budget can only be changed permanently. Admins who want to
raise a member's limit for a sprint/deadline must remember to lower it again
by hand. There is also no record of who raised what, when, or whether it was
put back.

## Decision

A **temporary budget boost** per team member: the admin sets a new limit and
an end time; the portal snapshots the member's current effective budget,
applies the new limit through the existing LiteLLM path, and a worker job
restores the snapshot when the period ends. Every boost row is kept forever
and doubles as the **history**.

### Data model (portal DB, migration 038)

Table `custom_member_budget_boost`:

- `id UUID PK`
- `team_id VARCHAR(128) NOT NULL`, `user_id VARCHAR(128) NOT NULL`
- `original_max_budget FLOAT NOT NULL` — effective budget snapshotted at
  boost time (always a number; see "unlimited members" below)
- `boost_max_budget FLOAT NOT NULL`
- `expires_at TIMESTAMPTZ NOT NULL`
- `status VARCHAR(16) NOT NULL DEFAULT 'active'` — `active | reverted | cancelled`
- `reverted_at TIMESTAMPTZ NULL`
- `created_by VARCHAR(128) NULL`, `created_at`, `updated_at`
- Partial unique index: one `active` row per `(team_id, user_id)`.

No LiteLLM schema changes.

### API (teams router; all three endpoints `require_team_admin`)

- `POST /api/teams/{team_id}/members/{member_id}/budget-boost`
  `{max_budget: float, expires_at: datetime}` →
  - 400 if `max_budget <= 0`, `expires_at` not in the future, or the member's
    current **effective** budget is unset/unlimited (boosting an unlimited
    member is meaningless, and reverting to NULL is not expressible via
    `update_team_member`, which omits None fields).
  - 409 if an active boost already exists for the member.
  - Effective budget = the membership's dedicated budget row
    (`LiteLLM_TeamMembership.budget_id → LiteLLM_BudgetTable.max_budget`),
    falling back to the team's default member budget
    (`metadata.team_member_budget_id` row) — same resolution the team-detail
    query uses.
  - Applies via `litellm.update_team_member(max_budget_in_team=body.max_budget)`
    (clone-on-write, proxy cache stays in sync), then inserts the boost row.
- `DELETE /api/teams/{team_id}/members/{member_id}/budget-boost` — early
  cancel: 404 if no active boost; restores `original_max_budget` via the same
  client call; sets `status='cancelled'`, `reverted_at=now`. The restore call
  runs first — if LiteLLM fails, respond 502 and leave the row `active` (the
  worker keeps it safe at expiry).
- `GET /api/teams/{team_id}/budget-boosts?limit=50` (max 200) — all statuses,
  newest first: `{id, user_id, original_max_budget, boost_max_budget,
  expires_at, status, reverted_at, created_by, created_at}`. This feeds both
  the per-member active badge (client-side join) and the history card.

### Auto-revert worker

New job `backend/app/jobs/expire_budget_boosts.py`, added to `worker.py`'s
gather as `budget_boost_loop(interval_seconds=300)` (same loop pattern as
membership expiry). Each tick:

1. Portal DB: select `active` boosts with `expires_at <= now`.
2. For each: if the membership no longer exists in
   `LiteLLM_TeamMembership` (member removed / team deleted), mark
   `reverted` without an API call — nothing to restore.
3. Otherwise `LiteLLMClient().update_team_member(..., max_budget_in_team=
   original_max_budget)` (jobs construct the client directly — existing
   pattern in `auto_deprecate` / `apply_cost_schedule`). On success mark
   `reverted` + `reverted_at=now`; on failure log and leave `active` so the
   next tick retries.

Revert is **unconditional**: manual budget edits made during the boost window
are overwritten at expiry (user-chosen policy; the UI badge warns a boost is
active).

### Frontend (team detail page, admin-only members tab)

- Per-member **boost action** opening a small dialog: new limit +
  end datetime (`datetime-local`). Members whose budget is unlimited get the
  action disabled with a hint.
- Active boost **badge** next to the member's budget: `부스트 ~<date>` with
  original→boost amounts and a cancel button.
- **History card** below the member list — new component file
  `frontend/src/components/team-boost-history.tsx` (keeps the 1,870-line page
  from growing): recent boosts table — member, original→boost, expires_at,
  status, requested-by, created_at. An `active` row whose `expires_at` is
  past renders as "원복 대기" (revert pending, worker within ~5 min).
- Hooks: `useTeamBudgetBoosts(teamId)`, `useCreateBudgetBoost`,
  `useCancelBudgetBoost`. i18n en/ko.

## Non-goals

- TPM/RPM boosts (budget only), scheduled future starts, overlapping boosts
  per member, notifications, boosting unlimited-budget members.
- No revert-skip/conflict detection — snapshot restore is unconditional.

## Edge notes

- After a boost, the member has a dedicated budget row even if they
  previously shared the team default; the restored value is numerically
  identical, so effective behavior is unchanged.
- Rows in the history are never deleted; cancel and revert are terminal
  states distinguished by `status`.

## Verification

- Backend: new tests — POST validation (400 x3, 409), snapshot resolution
  (dedicated row + team-default fallback), cancel path, sweep job (revert
  success, LiteLLM failure leaves active, membership-gone marks reverted).
  pytest 0 NEW failures (baseline 21), ruff 0 NEW (baseline 78).
- Migration applies cleanly on the local docker DB (alembic → 038).
- Frontend: lint 0 NEW (baseline 4 errors/13 warnings), build passes.

## v1 implementation notes (post final review)

Final whole-branch review: Ready to merge — 10/10 checks PASS (end-to-end
create→raise→worker-revert traced; the applied TOCTOU fix reserves the boost
row before the LiteLLM call so a race yields 409 and a LiteLLM 502 rolls the
row back with no orphan; unconditional snapshot restore; tz-aware throughout).
Ship-as-is minors:

- Frontend boost button disables on the members-list `total_max_budget`, which
  is the dedicated-row budget only (no team-default fallback), while the
  backend `resolve_effective_budget` does fall back. A member on the team
  default with a NULL membership budget_id could see the button disabled even
  though the backend would boost them. Fails SAFE (over-restricts, never
  over-permits). Follow-up: gate the button on a resolved-effective-budget
  signal if it bites in practice.
- Classic dual-write window: LiteLLM raise succeeds but the commit fails →
  orphaned raise with no boost row (extremely rare, commit-after-external-call).
- History "By" shows raw user_id and omits a created_at column (cosmetic).
