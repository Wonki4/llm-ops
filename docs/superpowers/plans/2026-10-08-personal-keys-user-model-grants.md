# Personal keys + per-user model / access-group grants — Implementation plan

Spec: `docs/superpowers/specs/2026-10-07-personal-keys-user-model-grants-design.md`
Branch: `feat/personal-keys` (from `main`)

Conventions: backend `uv run pytest <files>` + `ruff check/format`; the stale
`test_keys.py` / `test_me.py` need Postgres and stay in the baseline — new
tests live in new files and override both `get_db` and `get_litellm_db`.
Frontend `npx tsc --noEmit -p .` + `npx eslint <files>`; Playwright on
`next dev -p 3100` with a dummy `litellm_session` cookie and mocked
`/api/proxy/**`.

## Task 1: Grant model + settings + LiteLLM client

- Create `backend/app/services/personal_access.py` (pure): constants
  `NO_DEFAULT_MODELS = "no-default-models"`, `ALL_PROXY_MODELS =
  "all-proxy-models"`, `PERSONAL_KEY_TAG = "personal-beta"`;
  `grant_state(models) -> "none" | "all" | "custom"` (`[]`/None and the
  no-default sentinel → none); `has_grant(models)`; `models_for(state,
  selection)`; `validate_selection(selection, *, model_names, access_groups)`
  → ValueError listing unknown names; `key_models_allowed(key_models, grant,
  *, access_groups)` → the key list must be ⊆ grant expanded (groups expand
  to members; `all` allows anything).
- `app/api/portal_settings.py`: `personal_keys_beta_enabled` (bool, stored
  `"true"`/`"false"`, default false) in GET and PUT.
- `app/clients/litellm.py`: `generate_key(team_id: str | None, …, tags: list[str]
  | None = None)` omits `team_id` when None; `update_user(user_id, **fields)`
  → `POST /user/update`; `access_groups_from_model_info(rows)` helper (or in
  the service).
- Tests: `tests/test_personal_access.py` (state table, validation, key
  scope with groups), settings round trip in `tests/test_portal_settings_beta.py`.
- Commit: `feat(keys): personal-access grant model, beta setting, LiteLLM user update client`.

## Task 2: Personal key minting

- `app/api/keys.py`: `CreateKeyRequest.team_id: str | None = None`;
  `_generate_sk_jwt(key_id, team_id | None, user_id)` → `prjId: null,
  keyType: "USR"` for personal keys; personal path = beta setting on (403
  `personal_keys_beta_disabled`), LiteLLM user row has a grant (403
  `personal_access_required`), `body.models` within the grant (400),
  TPM/RPM from portal defaults, `generate_key(team_id=None,
  tags=[PERSONAL_KEY_TAG], metadata={…, "key_type": "personal"})`;
  `list_my_keys` rows gain `personal: bool`.
- Tests: `tests/test_personal_keys.py` — JWT claims, payload shape, 403/403/400,
  group inside the grant accepted, reveal re-mints a personal key, list flag.
- Commit: `feat(keys): personal (team-less) keys behind the beta setting`.

## Task 3: `/api/me` + admin personal-access API

- `app/api/me.py`: add `models`, `personal_access` (state), `tpm_limit`,
  `rpm_limit`, `budget_duration`, `personal_keys_beta_enabled`,
  `personal_keys_enabled` (grant ∧ beta).
- `app/api/admin_users.py`: detail → `user.models/personal_access/
  budget_duration`, keys rows → `personal`; list rows → `personal_access`;
  `PATCH /api/admin/users/{user_id}/personal-access` body
  `{access: "none"|"all"|"custom", models?: [...], max_budget?, budget_duration?,
  tpm_limit?, rpm_limit?, delete_personal_keys?: bool}` → validates names
  against `/model/info` (+ access groups), `update_user` (creates the row via
  `/user/new` first when missing), optional delete of the user's personal
  keys, returns the refreshed detail profile.
- Tests: `tests/test_personal_access_api.py`.
- Commit: `feat(users): per-user personal-access grants, budget and limits (admin), /api/me exposes the scope`.

## Task 4: Frontend — types, hooks, i18n, new-key page, keys list

- `types`: `ApiKey.personal`, `User.{models, personal_access, tpm_limit,
  rpm_limit, budget_duration, personal_keys_beta_enabled, personal_keys_enabled}`,
  `CreateKeyRequest.team_id: string | null`, `PortalSettings.personal_keys_beta_enabled`,
  `AdminUserDetailProfile.{models, personal_access, budget_duration}`,
  `AdminUserKey.personal`, admin list row `personal_access`,
  `PersonalAccessBody`.
- `lib/personal-access.ts`: `grantState`, `expandPersonalScope(models, index,
  byName)` (reuses `expandModelGrants`, `all` → every visible model).
- `keys/new/page.tsx`: "키 종류" segmented control (팀 키 / 개인 키 Beta),
  shown when `me.personal_keys_beta_enabled`; `?type=personal` preselects;
  personal: team select hidden, scope box (expanded badges), model picker
  (checklist limited to the scope, optional), TPM/RPM from portal defaults,
  disabled with a hint when `personal_keys_enabled` is false; POST
  `team_id: null`.
- `keys/page.tsx`: "개인" badge in the team column, filter option "개인 키",
  header "내 전체 한도" when user limits are set.
- Commit: `feat(keys): personal key creation and listing in the portal`.

## Task 5: Frontend — model dashboard + model detail

- `models/dashboard/page.tsx`: synthetic "개인 (Beta)" entry on top of the
  team list when `me.personal_access !== "none"`; selected → the same table
  from `expandPersonalScope`; header shows budget/limits; action link "개인
  키 만들기" (beta on). Hidden for users without a grant.
- `models/[...modelName]/page.tsx`: "내 개인 키로 사용 가능" chip.
- Commit: `feat(models): personal scope in the dashboard and model detail`.

## Task 6: Frontend — admin

- `admin/users/[userId]/page.tsx`: "개인 키 권한 (Beta)" card + `personal-access-dialog.tsx`
  (none/all/custom radio, checklist of models and access groups, budget,
  duration, TPM/RPM with the all-keys hint, delete-personal-keys checkbox on
  revoke).
- `admin/users/page.tsx`: "개인 키" column chip.
- `admin/settings/page.tsx`: "개인 키 (Beta)" switch in the API-key card.
- Commit: `feat(admin): personal-access editor, users list column, beta switch`.

## Task 7: Gates, live check, docs, PR

- `tsc`/eslint; Playwright smoke (new-key toggle + POST body, dashboard
  entry, admin dialog PATCH body).
- Live on the local stack: grant a group to a user, mint a personal key via
  the portal, call a member → 200, outside → 403; user `rpm_limit: 1` →
  second call limited; revoke → 403.
- Spec → implemented; README paragraph; push; PR to `main`.
