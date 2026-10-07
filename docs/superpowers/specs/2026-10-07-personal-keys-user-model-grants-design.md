# Personal keys + per-user model / access-group grants — Design

Date: 2026-10-07
Status: draft, awaiting review (supersedes the team-member-scope draft of the same day: "teams will stay few; add personal keys to the portal instead")
Related: LiteLLM v1.102 `proxy/auth/auth_checks.py` (`common_checks` 2.1 `can_user_call_model`, 4.1 `_user_max_budget_check`, `_check_model_access_helper`), `management_endpoints/internal_user_endpoints.py` (`/user/update`), `backend/app/api/keys.py`, `backend/app/clients/litellm.py`, `backend/app/api/admin_users.py`, `backend/app/api/me.py`, `frontend/src/app/(app)/keys/new/page.tsx`, `frontend/src/lib/access-groups.ts`, MCP plan `docs/superpowers/plans/2026-08-03-budget-usage-mcp-plan.md` (decodes `prjId`)

## Goal

Two things that only work together:

1. A user can mint a **personal key** from the portal — a key with no team.
   Today every portal key is a team key, so a user who is not in a team can
   do nothing, and teams are not going to be created for everyone.
2. An admin can grant **one user** a model scope — concrete model names
   and/or **model access group** names — plus a personal budget and TPM/RPM,
   and LiteLLM enforces that scope on the user's personal keys.

Teams keep working exactly as they do; a user may hold team keys and
personal keys side by side.

## Background — verified facts (LiteLLM v1.102.0, local stack, 2026-10-07)

- **What governs a key with no team** (`common_checks`):
  - 2.1 `if _model and team_object is None and user_object is not None:
    can_user_call_model(model, llm_router, user_object)` → the **user row's
    `models`** list, evaluated by `_can_object_call_model` →
    `_check_model_access_helper`, which expands model access groups
    (`llm_router.get_model_access_groups(model_name=…)`), wildcards and
    aliases. So a group name in `user.models` grants the group's members.
  - `models == []` means **all proxy models** (`all_model_access` when the
    list is empty); `["no-default-models"]` means **nothing** (403
    `key_model_access_denied` before any other check); `all-proxy-models`
    is the explicit "everything" sentinel.
  - 4.1 personal budget: `user_object.max_budget` against the
    `spend:user:<id>` counter; user `tpm_limit` / `rpm_limit` apply as well.
    Key-level `models`, `max_budget`, `tpm/rpm` still apply on top, as for
    any key.
- `/key/generate` with `user_id` and **no `team_id`** creates a personal key
  and upserts the `LiteLLM_UserTable` row when missing (that is how portal
  users get a row today — `clients/litellm.py::create_user` exists but is not
  called anywhere). `/user/update {user_id, models, max_budget, tpm_limit,
  rpm_limit, budget_duration}` edits the row; `litellm_settings.
  default_internal_user_params` / `max_internal_user_budget` only apply to
  `/user/new`, not to rows upserted by key generation.
- Portal key minting (`api/keys.py::create_key`): `CreateKeyRequest.team_id:
  str` (required); the `sk-<jwt>` `sub` is `{keyId, prjId: team_id,
  keyType: "PRJ", regUserId, iat}`; `reveal_key` re-mints the same JWT from
  the LiteLLM row (`team_id`, `metadata.sk_key_id`, `sk_iat`); TPM/RPM come
  from `custom_portal_settings` (`team:<id>:default_*` then
  `default_tpm_limit` / `default_rpm_limit`). `list_my_keys` LEFT JOINs the
  team and drops only keys of strict-hidden teams — a NULL `team_id` already
  passes. The MCP budget plan parses `prjId` as "the key's own team".
- New-key page (`keys/new/page.tsx`) is team-first: team select → the team's
  models with access-group badges (`buildAccessGroupIndex`), TPM/RPM shown
  from team or portal defaults. Admin user detail shows the user row's spend,
  `max_budget`, `tpm_limit`, `rpm_limit` read-only; `/api/me` returns
  `spend` and `max_budget`.
- `frontend/src/lib/access-groups.ts` (`buildAccessGroupIndex`,
  `expandModelGrants`) already renders a `models` list with group expansion
  and "via group" hints (teams, keys, model pages).

## Scope

In:

- Personal keys: a "키 종류" choice on the new-key page (팀 키 / 개인 키);
  backend accepts `team_id: null`; JWT `prjId: null, keyType: "USR"`;
  keys list shows an "개인" badge and a "개인 키" filter.
- Per-user grants (super user): admin user detail gets a "개인 키 권한" card —
  allowed models/access groups, personal max budget + reset period, TPM/RPM —
  written to the LiteLLM user row through `/user/update`.
- Default deny: a user with no explicit grant cannot mint a personal key, and
  the portal never writes the LiteLLM "empty list = everything" state.
- The user's own view: `/api/me` and the new-key page show the personal
  scope (expanded) so people see what a personal key may call; the personal
  key's model picker is limited to that scope.
- Admin users list: a "개인 키" column (granted / not) to find who has access.

Out (see Non-goals): team-member scope (`allowed_models` on memberships),
LiteLLM unified access groups (`access_group_ids`), organisation allowlists,
self-service "request personal access" workflow.

## Decisions

1. **Source of truth is the LiteLLM user row.** `models`, `max_budget`,
   `budget_duration`, `tpm_limit`, `rpm_limit` on `LiteLLM_UserTable`, edited
   only via `/user/update`. No portal table, no migration: what the admin page
   shows is what the proxy enforces.
2. **Grant states are explicit; `[]` is never written.** The editor offers
   three states: *없음* (`["no-default-models"]`), *전체* (`["all-proxy-models"]`),
   *선택* (the chosen model/group names). An empty list is LiteLLM's
   "everything" and is ambiguous with "never configured", so the portal
   treats a row with `models == []` as *no grant* for its own purposes and
   never produces it.
3. **Personal key minting requires a grant.** `POST /api/keys` with
   `team_id: null` refuses (403, "관리자가 개인 키 권한을 부여해야 합니다") unless
   the user row's `models` is non-empty and not only `no-default-models`.
   This is the portal-side guarantee of default deny; keys created outside the
   portal (LiteLLM UI, master key) are out of scope and noted as a residual
   risk (hardening: `default_internal_user_params.models =
   ["no-default-models"]` + a one-off script for existing `[]` rows, optional).
4. **Access groups are first-class grant entries**, stored by name and
   rendered expanded with `expandModelGrants`, exactly as team/key grants are
   today. The picker lists every model the admin can see plus every access
   group found in `model_info.access_groups`.
5. **Personal key limits come from the user row + portal defaults.** TPM/RPM
   on the key = portal `default_tpm_limit` / `default_rpm_limit` (no team
   override exists); the user row's `tpm_limit` / `rpm_limit` (if set by the
   admin) cap all of the user's personal keys together, the way team-member
   limits cap team keys. Key `max_budget` must be ≤ the user's `max_budget`
   when one is set; otherwise the user budget alone applies.
6. **JWT claims for personal keys:** `prjId: null`, `keyType: "USR"`,
   everything else unchanged, so `reveal_key` stays a pure re-mint from the
   row. Consumers that decode `prjId` (the budget MCP) must treat `null` as
   "no team"; nothing else reads the claim.
7. **Who may grant:** super user only (it is a platform-wide entitlement, not
   a team matter). Users cannot self-grant; a request workflow is a possible
   follow-up (the budget-request pattern already exists).
8. **Hidden-team rules do not apply** to personal keys (nothing to hide
   behind); they are listed for their owner and for super users.

## Architecture

### Backend

- `clients/litellm.py`
  - `generate_key(..., team_id: str | None)` — omit `team_id` from the
    payload when `None`.
  - `update_user(user_id, *, models=None, max_budget=..., budget_duration=...,
    tpm_limit=..., rpm_limit=...)` → `POST /user/update` (only the given
    fields). `get_user_info` already exists.
- `api/keys.py`
  - `CreateKeyRequest.team_id: str | None = None`. Personal path: load the
    LiteLLM user row (`SELECT models, max_budget, tpm_limit, rpm_limit`);
    enforce Decision 3; validate `body.models` ⊆ the user's scope (names or
    groups; `all-proxy-models` in the grant allows anything); TPM/RPM from
    the portal defaults; `_generate_sk_jwt(key_id, None, user_id)` → `prjId:
    null, keyType: "USR"`; `generate_key(team_id=None, ...)`.
  - `list_my_keys` / admin key listings: `team_id: null` rows keep flowing;
    add `personal: bool` to the serialised key for the UI.
- `api/me.py` — add `models` (the raw grant), `tpm_limit`, `rpm_limit`,
  `budget_duration`, `personal_keys_enabled` (Decision 3 predicate) so the
  new-key page can decide without a second call.
- `api/admin_users.py`
  - `get_user_detail` → `user.models`, `user.budget_duration`,
    `user.personal_keys_enabled`; keys rows → `personal`.
  - `PATCH /api/admin/users/{user_id}/personal-access` body
    `{"models": [...] | "all" | "none", "max_budget", "budget_duration",
    "tpm_limit", "rpm_limit"}` → validates names/groups against
    `/model/info` (+ `access_groups`), maps `all` → `["all-proxy-models"]`,
    `none` → `["no-default-models"]`, calls `update_user`, returns the row.
    Creates the user row first via `/user/new` when it does not exist yet
    (a user who never minted a key), with the same payload.
  - `list_users` → `personal_keys_enabled` per row (one query on
    `LiteLLM_UserTable.models`).
- `api/models_catalog.py::list_models` already exposes `model_info`; make
  sure `access_groups` survives for non-admins (it does for the team page).

### Frontend

- `types`: `Key.personal`, `MeProfile.models/tpm_limit/rpm_limit/
  budget_duration/personal_keys_enabled`, `AdminUserDetailProfile.models/
  budget_duration/personal_keys_enabled`, `AdminUserRow.personal_keys_enabled`,
  `UpdatePersonalAccessBody`.
- `hooks/use-api.ts`: `useCreateKey` body `team_id: string | null`;
  `useUpdatePersonalAccess(userId)`.
- `keys/new/page.tsx`: a segmented "키 종류" control above the team select
  (팀 키 / 개인 키). Personal: team select hidden; a "개인 키 권한" box shows
  the user's scope expanded (`expandModelGrants`) or, when
  `personal_keys_enabled` is false, an explanation + disabled submit; model
  picker limited to the scope; TPM/RPM shown from portal defaults with
  source "글로벌"; budget field capped by the user's `max_budget`.
- `keys/page.tsx`: "개인" badge in the team column, team filter gains
  "개인 키"; admin key views the same.
- `admin/users/[userId]/page.tsx`: new card "개인 키 권한" — status chip
  (없음 / 전체 / N개 모델·그룹), expanded badges, personal budget / reset /
  TPM / RPM, "편집" → `personal-access-dialog.tsx`: three-state radio
  (없음 / 전체 / 선택) + checklist of models and access groups (groups with
  member count), budget/duration/TPM/RPM inputs.
- `admin/users/page.tsx`: "개인 키" column (chip).
- i18n (`keys.*`, `adminUsers.*`, `me.*`): `keyTypeLabel`, `keyTypeTeam`,
  `keyTypePersonal`, `personalScopeTitle`, `personalScopeNone`,
  `personalScopeAll`, `personalKeysDisabled`, `personalBadge`,
  `filterPersonal`, `personalAccessCard`, `personalAccessEdit`,
  `personalAccessState*`, `personalBudget`, `personalLimits`,
  `personalAccessSaved`, `personalAccessInvalid`.

### Error handling

- LiteLLM 403 `key_model_access_denied` on a personal key → surfaces to the
  caller of the proxy as today; the portal pre-validates the key's `models`
  so portal-minted keys do not start in that state.
- `/user/update` 400 (unknown model) → dialog shows the message; the portal
  validates names first so this is rare.
- A granted model/group later removed from the proxy: the grant keeps the
  name; the card renders it greyed as "현재 없음"; LiteLLM simply never
  matches it.
- Revoking (*없음*) does not delete existing personal keys; they start
  failing with 403 immediately (LiteLLM reads the user row per request, with
  its usual cache TTL). The dialog says so and offers "개인 키도 삭제" as a
  checkbox (calls `/key/delete` for the user's personal keys).

## Testing

- Backend: `test_keys.py` — personal create builds `prjId: null` /
  `keyType: "USR"`, omits `team_id` in `/key/generate`, uses portal default
  TPM/RPM, refuses without a grant (403), rejects `models` outside the scope
  (400), accepts a group name inside the scope; reveal re-mints a personal
  key identically. `test_admin_users.py` — PATCH maps all/none/list, creates
  the row when missing, validates names; detail/list carry the new fields.
  `test_me.py` — new fields.
- Frontend: `tsc`/eslint; Playwright on the dev server with mocked proxy —
  new-key page toggles to personal (team select hidden, scope box, picker
  limited), POST body has `team_id: null`; admin user detail card + dialog
  three states; keys list badge/filter.
- Live check on the local stack: grant `["beta-models"]` (a group that
  exists in `model_info`) to a user, mint a personal key from the portal,
  call a group member → 200, a model outside the group → 403; set *없음* →
  403 on the same key; set a `max_budget` of 0.01 and exceed it →
  `ExceededBudget: User=…`.

## Non-goals

- Team-member model scope (`/team/member_update allowed_models`): the right
  tool when a team has many members with different needs, but teams will
  stay few here; the earlier draft stays in git history (`19f13d7`) if that
  changes.
- LiteLLM unified access groups (`access_group_ids`): key/team-level only,
  separate feature.
- Self-service requests for personal access; organisation-level allowlists;
  editing a key's `models` after creation.

## Open questions for review

1. Should personal-key TPM/RPM have their own portal defaults
   (`default_personal_tpm_limit`) instead of reusing the global key
   defaults? Draft: reuse.
2. Should "전체" (`all-proxy-models`) be offered at all, or only explicit
   lists? Draft: offer it, it is the natural "trusted user" setting.
3. On revoke, delete the user's personal keys by default or only on request?
   Draft: checkbox, off by default.
4. Should the MCP budget tool treat a personal key's `prjId: null` as "query
   any team the user belongs to" (today's free-param rule) or as "user-only
   spend"? Draft: user-only when the key is personal.
