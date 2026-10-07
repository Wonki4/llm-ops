# Per-user model / access-group grants (team member scope) — Design

Date: 2026-10-07
Status: draft, awaiting review
Related: LiteLLM v1.102 `proxy/auth/auth_checks.py` (`common_checks` 2.1/2.2, `_check_team_member_model_access`, `_check_model_access_helper`), `management_endpoints/team_endpoints.py` (`/team/member_add`, `/team/member_update`), `backend/app/clients/litellm.py::update_team_member`, `backend/app/api/teams.py::change_member_budget`, `backend/app/api/admin_users.py`, `frontend/src/lib/access-groups.ts`

## Goal

Let an admin grant **one user** a model scope — concrete model names and/or
**model access group** names — without touching the whole team's allowlist.
The grant narrows what that user's portal keys may call inside a team, is
shown wherever the portal already shows "which models can this person use",
and uses the mechanism LiteLLM actually enforces for the keys the portal
issues.

## Why not `LiteLLM_UserTable.models` (the first idea)

The first proposal was to write the grant into the user row (`/user/update`
`models: ["beta-models"]`). That is a dead end for this portal:

- every portal key is a **team key** (`backend/app/api/keys.py`:
  `CreateKeyRequest.team_id: str`, the `sk-<jwt>` carries `prjId = team_id`);
- LiteLLM evaluates the user row's `models` **only for personal keys**:
  `common_checks` step 2.1 — `if _model and team_object is None and
  user_object is not None: can_user_call_model(...)`.

So a user-level grant would never be checked on a portal key. What LiteLLM
does check for a team key that carries a `user_id` is step 2.2,
`_check_team_member_model_access`: the member's own `allowed_models` on the
team membership's budget row. That is the lever this design uses.

## Background — verified facts (LiteLLM v1.102.0, local stack, 2026-10-07)

- Auth order for a team key: team blocked? → team `models` (or the key's
  unified `access_group_ids`) → **member scope** (`LiteLLM_TeamMembership.budget_id`
  → `LiteLLM_BudgetTable.allowed_models text[]`, enforced only when non-empty;
  empty = inherit the team) → project checks → budgets. The member scope can
  only **narrow** the team allowlist: a model the team does not grant is
  rejected at the team step first.
- `allowed_models` entries go through `_can_object_call_model` →
  `_check_model_access_helper`, which expands **model access groups**
  (`llm_router.get_model_access_groups(model_name=…, team_id=…)`), wildcard
  patterns and team aliases. A group name in the member scope therefore grants
  the group's member models exactly as it does on a team or key.
- Write path: `POST /team/member_update {team_id, user_id, allowed_models: [...]}`
  (admin-only; team admins are allowed). LiteLLM clone-on-writes a dedicated
  budget row for the member when the membership still shares the team's
  default budget — the same path the portal already uses for per-member
  budgets (`backend/app/clients/litellm.py::update_team_member`,
  `backend/app/api/teams.py::change_member_budget`). `/team/member_add` also
  accepts `allowed_models`; `/team/update` has `default_team_member_models`
  (applied to members added later).
- Read path: no dedicated endpoint; the portal already reads the LiteLLM DB
  directly (`LiteLLM_TeamMembership m LEFT JOIN LiteLLM_BudgetTable b ON
  m.budget_id = b.budget_id`, see `member_budget_boost.resolve_effective_budget`).
- Portal today: team detail → members tab edits per-member budget/TPM/RPM
  (`useChangeMemberBudget`); admin user detail lists keys + teams (no model
  controls); `frontend/src/lib/access-groups.ts` (`buildAccessGroupIndex`,
  `expandModelGrants`) already renders a `models` list with group expansion
  and "via group" hints for teams and keys.
- `LiteLLM_BudgetTable.allowed_models` exists in the local DB (default `{}`),
  so no LiteLLM migration is involved.

## Scope

In:

- A per-member model scope on a team membership: a list of model names and
  access-group names, stored in LiteLLM (`allowed_models`), edited through
  `/team/member_update`.
- Editors: team detail → members tab (team admin or super user), and admin
  user detail → teams tab (super user). Same dialog component.
- Display: members tab column + badge expansion (group → models, "via group"),
  admin user detail teams tab, the member's own "내 팀" view, and the
  new-key dialog's model picker (limited to the member's scope when one is
  set).
- Team setting `default_team_member_models` ("새 멤버 기본 허용 모델") so a
  team can default new members to a narrower scope.

Out (see Non-goals): user-row `models`, LiteLLM's unified access groups
(`LiteLLM_AccessGroupTable` / `access_group_ids`), organisation-level
allowlists, per-key model lists (already supported at key creation).

## Decisions

1. **Storage = LiteLLM, not the portal DB.** The grant lives in
   `LiteLLM_BudgetTable.allowed_models` on the member's budget row, written
   only through `/team/member_update`. No `custom_*` table, no migration, no
   sync job; what the portal shows is what the proxy enforces.
2. **Semantics follow LiteLLM exactly.** Empty list = inherit the team's
   models (no restriction); non-empty = the member may call only those
   models/groups, and only within the team's allowlist. The portal validates
   the subset client- and server-side (each entry must be a team model, a team
   access group, or `all-proxy-models` when the team grants it) and surfaces
   LiteLLM's 400 otherwise.
3. **Access groups are first-class entries.** The picker lists the team's
   concrete models and the team's access groups (from the same
   `useModels` + `buildAccessGroupIndex` data the team page uses); a group is
   stored by name and rendered expanded with `expandModelGrants`.
4. **Who may edit:** the same rule as the member budget — team admin of that
   team or super user (`require_team_admin`). LiteLLM rejects
   `allowed_models` on self-join, which the portal never does anyway.
5. **Per team, not global.** A user in three teams gets three scopes. The
   admin user detail page edits them one team at a time; there is no
   "all teams" grant because LiteLLM has no object for it.
6. **Keys follow the scope at request time.** A key's own `models` list is
   not rewritten when the scope changes; LiteLLM applies the member scope on
   every request on top of the key/team lists. The new-key dialog narrows its
   model choices to the scope so people do not create keys that will 403.
7. **Default for new members** is a team setting (`default_team_member_models`)
   edited next to the team's default member budget; applied by LiteLLM on
   `/team/member_add` when the request does not pass `allowed_models`.

## Architecture

### Backend

- `clients/litellm.py::update_team_member(..., allowed_models: list[str] | None = None)`
  — forwarded as-is (`None` = leave unchanged, `[]` = clear the scope).
  `update_team(team_id, default_team_member_models=...)` for the team default.
- `api/teams.py`
  - `PUT /api/teams/{team_id}/members/{member_id}/models`
    body `{"allowed_models": ["gpt-4o", "beta-models"]}` → validates each
    entry against the team's effective models/groups (team `models` expanded
    with the router's access groups, `all-proxy-models` honoured), calls
    `update_team_member(allowed_models=...)`, returns the stored list.
    Requires `require_team_admin`.
  - `list_team_members` adds `allowed_models: string[]` per row (LEFT JOIN the
    membership's budget row; `[]` when inheriting).
  - `GET /api/teams/{team_id}` (team detail) adds `default_member_models`;
    `PUT …/settings` accepts it.
  - The "my teams" / team detail endpoints a member sees add
    `my_allowed_models` so the member's own view can show the narrowed scope.
- `api/admin_users.py::get_user_detail` — `teams[]` rows gain
  `allowed_models` (same join). Editing from the admin page reuses the teams
  endpoint above (super user passes `require_team_admin`).
- `api/keys.py::create_key` — reject `models` entries outside the caller's
  member scope with 400 (defensive; LiteLLM would 403 at call time anyway).

### Frontend

- `types`: `TeamMember.allowed_models: string[]`, `AdminUserTeam.allowed_models`,
  `TeamDetail.default_member_models`, `my_allowed_models`.
- `hooks/use-api.ts`: `useChangeMemberModels(teamId)` → PUT above;
  `useUpdateTeamSettings` gains `default_member_models`.
- `components/member-model-scope-dialog.tsx`: title "허용 모델", a checklist
  of the team's models and access groups (groups shown with their member
  count and expanded on hover), "팀 전체 모델 상속" (clears to `[]`), save.
  Validation mirrors the backend (entries must be in the team's set).
- Team detail → members tab: new column "허용 모델" rendering `expandModelGrants`
  badges (group badge + "via group" hint) or "팀 상속"; row action opens the
  dialog. Team settings: "새 멤버 기본 허용 모델" picker.
- Admin user detail → teams tab: same column + action per team row.
- "내 팀" (member view): shows the member's scope under the team's model list
  when narrowed. New-key dialog: model picker limited to the scope when set.
- i18n (`teams.*`, `adminUsers.*`, `keys.*`): `memberModels`, `memberModelsInherit`,
  `memberModelsDialogTitle`, `memberModelsHint`, `defaultMemberModels`,
  `memberModelsSaved`, `memberModelsInvalid`, `keyModelOutsideScope`.

### Error handling

- LiteLLM 400 on a non-subset list → the dialog shows the message and keeps
  the selection; the portal pre-validates so this is rare.
- Membership without a budget row: LiteLLM creates one on
  `/team/member_update` (clone-on-write), same as the budget editor; nothing
  for the portal to do.
- A model removed from the team later: the member scope may now reference a
  model the team no longer grants; the team check rejects it and the members
  tab shows that entry greyed as "팀에 없음".

## Testing

- Backend: `test_teams.py` additions — PUT validates subset (400 for a model
  outside the team, group names accepted), calls `update_team_member` with
  `allowed_models`, `list_team_members` carries the scope; `test_admin_users.py`
  teams rows carry it; `test_keys.py` key create outside the scope → 400.
- Frontend: `tsc`/eslint; Playwright on the dev server with mocked proxy —
  members tab shows "팀 상속" / expanded group badges, dialog save sends the
  list, admin user detail renders the same.
- Live check on the local stack: set `["beta-models"]` on a member of a team
  whose models include the group; a key of that member calls a group member
  → 200, a team model outside the group → 403 `team_model_access_denied`;
  clear the scope → both 200.

## Non-goals

- User-row `models` grants (not enforced for team keys; the portal issues no
  personal keys).
- LiteLLM's unified access groups (`access_group_ids` on keys/teams,
  `LiteLLM_AccessGroupTable`): a separate feature; it has no user-level
  binding either.
- Organisation allowlists, per-key model editing after creation.

## Open questions for review

1. Should team admins be able to narrow members (as LiteLLM allows), or only
   super users? Draft: team admins, matching the member budget editor.
2. New-key dialog: hide models outside the member's scope, or show them
   disabled with the reason? Draft: hide, with a one-line note.
3. Should the portal expose `default_team_member_models` now or later?
   Draft: now — it is one field on the existing team settings form.
