# Budget-Request Limits Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a team admin bound the amount (max) and period (preset allowlist) a user may put in a budget increase request; enforce it server-side and constrain the request form.

**Architecture:** Two new per-team `custom_portal_settings` rows drive it. A policy helper is surfaced via `get_team_detail`; `update_team_settings` stores it; `create_budget_request` enforces it. Frontend adds a settings-tab config section and constrains the request dialog. Opt-in / backward compatible.

**Tech Stack:** FastAPI + SQLAlchemy async (backend); Next.js app-router + react-query + next-intl (frontend).

## Global Constraints

- **Preset period tokens are the fixed set `["7", "30", "90", "365", "permanent"]`** — only these are ever stored or accepted; `permanent` ⇔ a request with `requested_duration_days = null`.
- **Storage:** reuse `custom_portal_settings` keys `team:{id}:budget_request_max_amount` (number-as-string) and `team:{id}:budget_request_allowed_days` (CSV of preset tokens). Absent = no restriction.
- **Opt-in / backward compatible:** unset amount = no cap; unset/empty allowlist = any period. Teams with no policy behave exactly as today.
- **Defense in depth:** the form constrains the UI; `create_budget_request` is the server-side source of truth.
- Backend tests use the `mock_db` (AsyncMock) pattern. Gate: **0 new failures vs the `origin/main` baseline** + `ruff` clean on changed files.
- Frontend gate: `npx tsc --noEmit` exit 0 and `npm run lint` **0 new** (baseline 4 errors / 13 warnings), from `frontend/`.
- **en/ko i18n parity** (equal key counts). Work on branch `feat/budget-request-limits`. Never stage the `litellm` submodule.

---

## File Structure

- **Modify** `backend/app/api/teams.py` — preset constant, `_get_budget_request_policy` helper, `get_team_detail` payload, `UpdateTeamSettingsRequest` + `update_team_settings` storage.
- **Modify** `backend/app/api/team_requests.py` — enforce the policy in `create_budget_request`.
- **Create** `backend/tests/test_budget_request_limits.py` — policy storage/exposure + enforcement tests.
- **Modify** `frontend/src/types/index.ts` — team-detail + settings-body fields.
- **Modify** `frontend/src/app/(app)/teams/[teamId]/page.tsx` — settings-tab config + request-form constraint + parent prop threading.
- **Modify** `frontend/messages/en.json` + `ko.json` — new labels/errors.

---

## Task 1: Backend — policy storage + exposure + settings write

**Files:**
- Modify: `backend/app/api/teams.py`
- Test: covered in Task 2's test file (this task's behavior is asserted there via `update_team_settings` + `get_team_detail`); a focused unit assertion is included here in Step 5.

**Interfaces — Produces:**
- `BUDGET_REQUEST_DAY_PRESETS: list[str] = ["7", "30", "90", "365", "permanent"]`
- `async def _get_budget_request_policy(db, team_id) -> dict` → `{"budget_request_max_amount": float | None, "budget_request_allowed_days": list[str] | None}`

- [ ] **Step 1: Add the preset constant + policy helper**

In `backend/app/api/teams.py`, near the other module-level helpers (e.g. just above `_get_team_default_limits`), add:

```python
BUDGET_REQUEST_DAY_PRESETS = ["7", "30", "90", "365", "permanent"]


async def _get_budget_request_policy(db: AsyncSession, team_id: str) -> dict:
    """Team's budget-request bounds from portal settings. None/[] = unrestricted."""
    amt_key = f"team:{team_id}:budget_request_max_amount"
    days_key = f"team:{team_id}:budget_request_allowed_days"
    result = await db.execute(
        text("SELECT key, value FROM custom_portal_settings WHERE key IN (:amt, :days)"),
        {"amt": amt_key, "days": days_key},
    )
    rows = {r["key"]: r["value"] for r in result.mappings()}
    amt_raw = rows.get(amt_key)
    days_raw = rows.get(days_key)
    return {
        "budget_request_max_amount": float(amt_raw) if amt_raw else None,
        "budget_request_allowed_days": (
            [d for d in days_raw.split(",") if d] if days_raw else None
        ),
    }
```

- [ ] **Step 2: Surface the policy in `get_team_detail`**

In the `get_team_detail` return dict (teams.py ~367-372, where `**(await _get_team_default_limits(db, team_id))` and `"membership_duration": ...` are), add:

```python
        **(await _get_budget_request_policy(db, team_id)),
```

- [ ] **Step 3: Extend the settings request model**

Add to `UpdateTeamSettingsRequest`:

```python
    budget_request_max_amount: float | None = None
    budget_request_allowed_days: list[str] | None = None
```

- [ ] **Step 4: Store the two settings in `update_team_settings`**

After the existing `portal_setting_keys` loop (the `for field, key in portal_setting_keys.items():` block), add explicit handling (one value is a list, so it is not part of the scalar loop):

```python
    # Budget-request bounds (opt-in). max_amount stored as a string; allowed_days
    # as a CSV of preset tokens validated against the fixed set. Empty -> delete.
    if "budget_request_max_amount" in updates:
        amt_key = f"team:{team_id}:budget_request_max_amount"
        amt = updates["budget_request_max_amount"]
        if amt is None or amt == "":
            await db.execute(text("DELETE FROM custom_portal_settings WHERE key = :key"), {"key": amt_key})
        else:
            if float(amt) <= 0:
                raise HTTPException(status_code=400, detail="budget_request_max_amount must be positive")
            await db.execute(
                text(
                    "INSERT INTO custom_portal_settings (key, value, updated_by) "
                    "VALUES (:key, :value, :updated_by) "
                    "ON CONFLICT (key) DO UPDATE SET value = :value, updated_by = :updated_by"
                ),
                {"key": amt_key, "value": str(amt), "updated_by": user.user_id},
            )
    if "budget_request_allowed_days" in updates:
        days_key = f"team:{team_id}:budget_request_allowed_days"
        days = updates["budget_request_allowed_days"] or []
        bad = [d for d in days if d not in BUDGET_REQUEST_DAY_PRESETS]
        if bad:
            raise HTTPException(status_code=400, detail=f"Invalid period presets: {bad}")
        if days:
            await db.execute(
                text(
                    "INSERT INTO custom_portal_settings (key, value, updated_by) "
                    "VALUES (:key, :value, :updated_by) "
                    "ON CONFLICT (key) DO UPDATE SET value = :value, updated_by = :updated_by"
                ),
                {"key": days_key, "value": ",".join(days), "updated_by": user.user_id},
            )
        else:
            await db.execute(text("DELETE FROM custom_portal_settings WHERE key = :key"), {"key": days_key})
```

Note: `HTTPException` and `text` are already imported in this module.

- [ ] **Step 5: Smoke-check the helper + ruff**

Run (from `backend/`):
```bash
python -c "from app.api.teams import _get_budget_request_policy, BUDGET_REQUEST_DAY_PRESETS; print(BUDGET_REQUEST_DAY_PRESETS)"
ruff check app/api/teams.py
```
Expected: prints `['7', '30', '90', '365', 'permanent']`; ruff clean.

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/teams.py
git commit -m "feat(teams): store + expose budget-request policy (max amount, period presets)"
```

---

## Task 2: Backend — enforce the policy in `create_budget_request` + tests

**Files:**
- Modify: `backend/app/api/team_requests.py`
- Create: `backend/tests/test_budget_request_limits.py`

**Interfaces — Consumes (Task 1):** `_get_budget_request_policy` and `BUDGET_REQUEST_DAY_PRESETS` from `app.api.teams`.

- [ ] **Step 1: Import the policy helper**

In `backend/app/api/team_requests.py`, add near the other `app.api`/`app.services` imports:

```python
from app.api.teams import _get_budget_request_policy
```

(No circular import: `teams.py` does not import `team_requests`.)

- [ ] **Step 2: Enforce in `create_budget_request`**

Immediately after the existing positivity checks (`if body.requested_budget <= 0:` / `if body.requested_duration_days ... <= 0:`) and before the duplicate-pending check, add:

```python
    policy = await _get_budget_request_policy(db, body.team_id)
    max_amount = policy["budget_request_max_amount"]
    if max_amount is not None and body.requested_budget > max_amount:
        raise HTTPException(
            status_code=400,
            detail=f"Requested amount exceeds the team limit of ${max_amount:g}",
        )
    allowed_days = policy["budget_request_allowed_days"]
    if allowed_days:
        token = "permanent" if body.requested_duration_days is None else str(body.requested_duration_days)
        if token not in allowed_days:
            raise HTTPException(
                status_code=400,
                detail="Requested period is not allowed for this team",
            )
```

- [ ] **Step 3: Write the tests**

Create `backend/tests/test_budget_request_limits.py` (mock_db pattern; `create_budget_request` uses the portal `db` for the policy read + duplicate check, and `litellm_db` for the alias):

```python
"""Budget-request limits — policy enforcement in create_budget_request (mock_db).

These assert the security-critical rejections. Enforcement runs BEFORE the
duplicate-pending check and the team-alias read, so each test only needs the
policy SELECT (the first portal-db execute) to return the team's settings; the
request is rejected with 400 before any further DB call.
"""

from unittest.mock import AsyncMock, MagicMock


def _policy_db(team_id, max_amount=None, allowed_days=None):
    """A portal-db `execute` whose first call returns the policy SELECT rows."""
    rows = []
    if max_amount is not None:
        rows.append({"key": f"team:{team_id}:budget_request_max_amount", "value": str(max_amount)})
    if allowed_days is not None:
        rows.append({"key": f"team:{team_id}:budget_request_allowed_days", "value": ",".join(allowed_days)})
    res = MagicMock()
    res.mappings.return_value = rows  # _get_budget_request_policy iterates .mappings()
    return AsyncMock(return_value=res)


async def test_amount_over_cap_rejected(client_for_user, super_user, mock_db):
    mock_db.execute = _policy_db("team-1", max_amount=20)
    async with client_for_user(super_user) as client:
        resp = await client.post("/api/team-requests/budget", json={
            "team_id": "team-1", "requested_budget": 50, "requested_duration_days": 30,
        })
    assert resp.status_code == 400
    assert "limit" in resp.json()["detail"].lower()


async def test_period_not_in_allowlist_rejected(client_for_user, super_user, mock_db):
    mock_db.execute = _policy_db("team-1", allowed_days=["7", "30"])
    async with client_for_user(super_user) as client:
        resp = await client.post("/api/team-requests/budget", json={
            "team_id": "team-1", "requested_budget": 10, "requested_duration_days": 90,
        })
    assert resp.status_code == 400


async def test_permanent_disallowed_when_not_in_list(client_for_user, super_user, mock_db):
    mock_db.execute = _policy_db("team-1", allowed_days=["30"])
    async with client_for_user(super_user) as client:
        resp = await client.post("/api/team-requests/budget", json={
            "team_id": "team-1", "requested_budget": 10, "requested_duration_days": None,
        })
    assert resp.status_code == 400


async def test_amount_within_cap_and_allowed_period_passes_policy(client_for_user, super_user, mock_db):
    # A request inside the cap + allowed period must clear enforcement. With
    # no pending duplicate, execution proceeds past the policy check; we assert
    # it is NOT a 400 policy rejection (it may 201, or fail later on the
    # unmocked litellm-alias read — either way the policy gate passed).
    mock_db.execute = _policy_db("team-1", max_amount=20, allowed_days=["30"])
    async with client_for_user(super_user) as client:
        resp = await client.post("/api/team-requests/budget", json={
            "team_id": "team-1", "requested_budget": 20, "requested_duration_days": 30,
        })
    assert resp.status_code != 400
```

The last test asserts the policy gate *passes* (status != 400) without depending
on how the conftest mocks the downstream `litellm_db` alias read. If a full 201
success is cleanly mockable in this conftest (mirror `test_team_requests.py`'s
`create_budget_request` setup), tighten it to `== 201` and add a `no-policy →
201` case; otherwise leave it and note the full success path is covered by the
manual E2E.

- [ ] **Step 4: Run tests + ruff**

Run (from `backend/`):
```bash
python -m pytest tests/test_budget_request_limits.py -q
ruff check app/api/team_requests.py tests/test_budget_request_limits.py
```
Expected: the rejection tests pass; ruff clean. Compare full-suite failures to the `origin/main` baseline — **0 new**.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/team_requests.py backend/tests/test_budget_request_limits.py
git commit -m "feat(requests): enforce team budget-request limits (amount cap + period allowlist)"
```

---

## Task 3: Frontend — types + team-settings config UI + i18n

**Files:**
- Modify: `frontend/src/types/index.ts`
- Modify: `frontend/src/app/(app)/teams/[teamId]/page.tsx`
- Modify: `frontend/messages/en.json`, `ko.json`

**Interfaces — Produces:** the settings-tab UI writes `budget_request_max_amount` + `budget_request_allowed_days` via `useUpdateTeamSettings`; the team-detail type carries the policy for Task 4.

- [ ] **Step 1: Extend the types**

In `frontend/src/types/index.ts`:
- Add to `interface TeamDetail`:
  ```ts
  budget_request_max_amount: number | null;
  budget_request_allowed_days: string[] | null;
  ```
- Add the same two optional fields to the update-settings body type used by `useUpdateTeamSettings` (find the type whose fields include `membership_duration` / `default_tpm_limit`), e.g.:
  ```ts
  budget_request_max_amount?: number | null;
  budget_request_allowed_days?: string[] | null;
  ```

- [ ] **Step 2: Add i18n keys (en + ko)**

Add to the `teamDetail` block in both files (equal sets). en values:
```json
    "settingsBudgetRequestCard": "Budget request limits",
    "settingsBudgetRequestMaxLabel": "Max requestable amount ($)",
    "settingsBudgetRequestMaxHint": "Blank = no limit.",
    "settingsBudgetRequestPeriodsLabel": "Allowed periods",
    "settingsBudgetRequestPeriodsHint": "None checked = any period allowed.",
    "budgetPreset7": "7 days",
    "budgetPreset30": "30 days",
    "budgetPreset90": "90 days",
    "budgetPreset365": "1 year",
    "budgetPresetPermanent": "Permanent"
```
Add natural Korean values for each in `ko.json` (e.g. `settingsBudgetRequestCard` "예산요청 한도", `budgetPreset365` "1년", `budgetPresetPermanent` "영구").

- [ ] **Step 3: Thread the policy into `TeamSettingsTab` (props + parent)**

Add two props to `TeamSettingsTab`'s signature and prop type:
```ts
  budgetRequestMaxAmount,
  budgetRequestAllowedDays,
```
```ts
  budgetRequestMaxAmount: number | null;
  budgetRequestAllowedDays: string[] | null;
```
At the parent render site (`<TeamSettingsTab teamId={teamId} ... />`, ~line 1969) pass:
```tsx
              budgetRequestMaxAmount={data.budget_request_max_amount}
              budgetRequestAllowedDays={data.budget_request_allowed_days}
```
(`data` is the team-detail query result used for the other props.)

- [ ] **Step 4: Settings state + UI + save**

In `TeamSettingsTab`, add the preset constant (module scope, top of file):
```ts
const BUDGET_PRESETS = ["7", "30", "90", "365", "permanent"] as const;
```
Add state:
```ts
  const [maxAmount, setMaxAmount] = useState(budgetRequestMaxAmount != null ? String(budgetRequestMaxAmount) : "");
  const [allowedDays, setAllowedDays] = useState<string[]>(budgetRequestAllowedDays ?? []);
  const togglePreset = (p: string) =>
    setAllowedDays((prev) => (prev.includes(p) ? prev.filter((x) => x !== p) : [...prev, p]));
```
Add to the `handleSave` body object:
```ts
          budget_request_max_amount: maxAmount.trim() === "" ? null : Number(maxAmount),
          budget_request_allowed_days: allowedDays.length ? allowedDays : null,
```
Add a new `<Card>` (after the default-budget card) — a max-amount input + five checkboxes:
```tsx
      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("settingsBudgetRequestCard")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <label className="text-sm font-medium">{t("settingsBudgetRequestMaxLabel")}</label>
            <input
              type="number" min="0" step="0.01" value={maxAmount}
              onChange={(e) => setMaxAmount(e.target.value)}
              className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
            />
            <p className="text-xs text-muted-foreground">{t("settingsBudgetRequestMaxHint")}</p>
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium">{t("settingsBudgetRequestPeriodsLabel")}</label>
            <div className="flex flex-wrap gap-3">
              {BUDGET_PRESETS.map((p) => (
                <label key={p} className="flex items-center gap-1.5 text-sm">
                  <input type="checkbox" className="size-4" checked={allowedDays.includes(p)} onChange={() => togglePreset(p)} />
                  {t(`budgetPreset${p === "permanent" ? "Permanent" : p}`)}
                </label>
              ))}
            </div>
            <p className="text-xs text-muted-foreground">{t("settingsBudgetRequestPeriodsHint")}</p>
          </div>
        </CardContent>
      </Card>
```

- [ ] **Step 5: Type-check + lint + i18n parity + commit**

Run (from `frontend/`):
```bash
npx tsc --noEmit
npm run lint
node -e "const en=require('./messages/en.json'),ko=require('./messages/ko.json');const a=Object.keys(en.teamDetail),b=Object.keys(ko.teamDetail);console.log('teamDetail',a.length,b.length,'mismatch',a.filter(k=>!b.includes(k)).concat(b.filter(k=>!a.includes(k))))"
```
Expected: tsc 0; lint 0 new; `mismatch []` and equal counts. Then:
```bash
git add frontend/src/types/index.ts "frontend/src/app/(app)/teams/[teamId]/page.tsx" frontend/messages/en.json frontend/messages/ko.json
git commit -m "feat(teams): budget-request limits config in team settings tab"
```

---

## Task 4: Frontend — constrain the budget-request form

**Files:**
- Modify: `frontend/src/app/(app)/teams/[teamId]/page.tsx`
- Modify: `frontend/messages/en.json`, `ko.json`

**Interfaces — Consumes (Task 3):** `TeamDetail.budget_request_max_amount` / `budget_request_allowed_days`.

- [ ] **Step 1: i18n keys (en + ko)**

Add to `teamDetail` in both files:
```json
    "budgetMaxHint": "Max ${max} for this team",
    "budgetAmountOverLimit": "Amount exceeds the team limit",
    "budgetPeriodLabel": "Period"
```
(ko: e.g. `budgetMaxHint` "이 팀 최대 ${max}", `budgetAmountOverLimit` "금액이 팀 한도를 초과합니다", `budgetPeriodLabel` "기간".) Reuse the `budgetPreset*` keys from Task 3.

- [ ] **Step 2: Pass policy into `BudgetRequestDialog`**

Add props to `BudgetRequestDialog` (`{ teamId, currentBudget }`):
```ts
function BudgetRequestDialog({ teamId, currentBudget, maxAmount, allowedDays }: {
  teamId: string; currentBudget: number | null;
  maxAmount: number | null; allowedDays: string[] | null;
}) {
```
At the render site (~line 430) pass `maxAmount={team.budget_request_max_amount}` `allowedDays={team.budget_request_allowed_days}` (use whatever the team-detail object is named at that scope).

- [ ] **Step 3: Constrain amount + period**

- **Amount:** add `max={maxAmount ?? undefined}` to the amount `<Input>`, a helper line when a cap exists, and block submit when over the cap:
  ```tsx
  {maxAmount != null && (
    <p className="text-xs text-muted-foreground mt-1">{t("budgetMaxHint", { max: maxAmount })}</p>
  )}
  ```
  In the submit button `disabled`, add `|| (maxAmount != null && Number(amount) > maxAmount)`; optionally show `budgetAmountOverLimit` inline.
- **Period:** when `allowedDays` is a non-empty list, replace the free duration `<Input>` with a `<select>` over the allowed presets (value `"permanent"` → submit `null`), defaulting to the first allowed value; otherwise keep the current free `<Input>`. Initialize `durationDays` accordingly:
  ```tsx
  {allowedDays && allowedDays.length > 0 ? (
    <select
      className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm"
      value={durationDays}
      onChange={(e) => setDurationDays(e.target.value)}
    >
      {allowedDays.map((p) => (
        <option key={p} value={p === "permanent" ? "" : p}>
          {t(`budgetPreset${p === "permanent" ? "Permanent" : p}`)}
        </option>
      ))}
    </select>
  ) : (
    /* existing free number Input unchanged */
  )}
  ```
  Set the initial `durationDays` state so that, when an allowlist exists, it equals the first allowed token (`""` for permanent) instead of the hardcoded `"30"`. The existing submit already maps `durationDays.trim() ? Number(durationDays) : null`, so a `""` option submits `null` (permanent) correctly.

- [ ] **Step 4: Type-check + lint + i18n parity + commit**

Run (from `frontend/`):
```bash
npx tsc --noEmit
npm run lint
node -e "const en=require('./messages/en.json'),ko=require('./messages/ko.json');const a=Object.keys(en.teamDetail),b=Object.keys(ko.teamDetail);console.log(a.length===b.length && a.every(k=>b.includes(k)) ? 'teamDetail parity OK' : 'MISMATCH')"
```
Expected: tsc 0; lint 0 new; parity OK. Then:
```bash
git add "frontend/src/app/(app)/teams/[teamId]/page.tsx" frontend/messages/en.json frontend/messages/ko.json
git commit -m "feat(requests): constrain budget-request form to team limits"
```

---

## Verification (whole feature)

**Backend** (`cd backend`):
```bash
python -m pytest tests/test_budget_request_limits.py -q
ruff check app/api/teams.py app/api/team_requests.py tests/test_budget_request_limits.py
```
Gate: rejection tests pass; **0 new failures vs the `origin/main` baseline**.

**Frontend** (`cd frontend`): `npx tsc --noEmit` (0) + `npm run lint` (0 new) + teamDetail i18n parity.

**Manual (dev server):** as a team admin set a max amount + a couple of period presets in Settings → save. Open the budget-request dialog as a member: the amount over the cap is blocked, the period control shows only the allowed presets (default = first). Remove all presets + clear the amount → the form is unrestricted again (unchanged behavior). Bypassing the UI (raw POST over the cap / disallowed period) → 400. en/ko both render.
