# Budget-Request Limits (Team Settings) — Design

**Date:** 2026-08-25
**Status:** Approved (design)

## Goal

Let a team admin bound what a regular user may put in a **budget increase
request** for that team: a **maximum amount** and an **allowlist of periods**
(from a fixed preset set). Requests outside the bounds are rejected; the request
form only offers the allowed choices. Unset = today's behavior (no bounds), so
the feature is opt-in per team.

## Background — verified facts

- **Budget requests are unbounded today.** `create_budget_request`
  (`backend/app/api/team_requests.py`) accepts `requested_budget` and
  `requested_duration_days`; the only validation is `> 0`.
- **Team settings already live in `custom_portal_settings`** as `team:{id}:<key>`
  rows (`membership_duration`, `default_tpm_limit`, `default_rpm_limit`,
  `description`). `update_team_settings` (teams.py ~1200) upserts/deletes them in
  a `portal_setting_keys` loop; empty value → row deleted.
- **`get_team_detail` already surfaces team settings to the client** — it merges
  `_get_team_default_limits(...)` and `_get_membership_duration(...)` into the
  payload (teams.py ~367-371). We add the budget-request policy the same way, so
  both the settings tab (to prefill) and the request form (to constrain) read it
  from one payload.
- **Both UIs are in one file.** `frontend/src/app/(app)/teams/[teamId]/page.tsx`
  contains the `TeamSettingsTab` (admin config) and the budget-request form
  (`useCreateBudgetRequest`).

## Decisions

1. **Max amount + period allowlist** (not min/max, not free-form periods).
2. **Fixed period presets:** `7`, `30`, `90`, `365` (1 year) days, and
   `permanent`. The admin checks which to allow.
3. **Storage: reuse `custom_portal_settings`** (the established team-settings
   store), not new DB columns.
4. **Opt-in / backward compatible:** an unset amount = no cap; an unset/empty
   allowlist = any period (current behavior).
5. **Defense in depth:** the request form constrains the UI (UX) and
   `create_budget_request` enforces server-side (security).
6. **Scope: the request-creation step only.** No change to the approval step or
   to how an approved budget is applied.

## Architecture

### Storage — two per-team settings

| Key | Value | Meaning |
|---|---|---|
| `team:{id}:budget_request_max_amount` | number as string, e.g. `"20"` | max requestable amount; absent = no cap |
| `team:{id}:budget_request_allowed_days` | CSV of preset tokens, e.g. `"7,30,90,365,permanent"` | allowed periods; absent/empty = no restriction |

`permanent` in the CSV maps to a request with `requested_duration_days = null`;
integer tokens map to that many days. The canonical preset set is
`{7, 30, 90, 365, "permanent"}`; only these tokens are ever stored or accepted.

### Backend

- **`_get_budget_request_policy(db, team_id) -> dict`** (new, mirrors
  `_get_team_default_limits`): reads the two keys and returns
  `{"budget_request_max_amount": float | None,
    "budget_request_allowed_days": list[str] | None}`
  (`allowed_days` as the parsed token list, e.g. `["7","30","permanent"]`, or
  `None`/`[]` when unrestricted). Merged into the `get_team_detail` payload.
- **`update_team_settings`**: add `budget_request_max_amount` and
  `budget_request_allowed_days` to `UpdateTeamSettingsRequest` and to the
  `portal_setting_keys` loop. The API accepts `allowed_days` as a list of preset
  tokens; it is validated against the preset set (reject unknown tokens with
  400) and stored as CSV. Empty list / null → delete the row (no restriction).
  A non-positive `max_amount` → 400; null/empty → delete the row.
- **`create_budget_request` enforcement** (after the existing `> 0` checks):
  load the policy for `body.team_id`, then
  - if `max_amount` is set and `requested_budget > max_amount` → 400
    ("Requested amount exceeds the team limit of $X").
  - if `allowed_days` is set (non-empty): compute the request's period token
    (`"permanent"` when `requested_duration_days is None`, else
    `str(requested_duration_days)`); if it is not in `allowed_days` → 400
    ("Requested period is not allowed for this team").
  - if a key is unset → no corresponding check.

### Frontend (`teams/[teamId]/page.tsx` + `types` + i18n)

- **Team detail type** (`types/index.ts`): add
  `budget_request_max_amount: number | null` and
  `budget_request_allowed_days: string[] | null` to the team-detail shape, and
  the two fields to the settings-update body type.
- **`TeamSettingsTab`** — a new "예산요청 한도 / Budget request limits" section:
  a max-amount `Input` (blank = no cap) and **five preset checkboxes**
  (7d / 30d / 90d / 1y / permanent). Saved through the existing
  `update_team_settings` mutation. Prefilled from the team-detail policy.
- **Budget-request form** (the `useCreateBudgetRequest` dialog): read the policy
  from team detail and
  - show the amount cap as helper text and validate `amount ≤ max` before
    submit (submit disabled / error otherwise);
  - render the **period as the allowed presets only** (radio/select), defaulting
    to the first allowed value, instead of the current free default of 30. When
    the team has no allowlist, keep the current period control unchanged.
- **en/ko i18n** for the new labels/errors (equal key counts).

## Error handling

- Server rejects out-of-bounds requests with 400 and a clear message; the form
  surfaces it. The server is the source of truth even if the UI is bypassed.
- Unknown preset tokens sent to `update_team_settings` → 400 (the settings UI
  only ever sends valid tokens).
- Teams with no policy behave exactly as today.

## Testing

- **Backend (pytest, mock_db):** amount over cap → 400; period not in allowlist
  → 400; permanent allowed vs disallowed; no policy → passes; `update_team_settings`
  stores CSV and rejects unknown tokens; `get_team_detail` surfaces the policy.
- **Frontend:** `tsc --noEmit` + `lint` (0 new). Manual: admin sets max + presets
  → save → open the request form → amount over cap is blocked, period select
  shows only allowed presets; a team with no policy is unchanged; en/ko render.

## Non-goals

- No minimum amount or minimum period (caps/allowlist only).
- Presets are the fixed set `{7, 30, 90, 365, permanent}` — no free-form day
  entry.
- No change to the approval step or to boost application.
- No retroactive effect on already-submitted or approved requests.
