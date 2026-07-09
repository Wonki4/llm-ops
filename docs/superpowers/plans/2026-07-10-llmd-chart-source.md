# Per-Stack llm-d Chart Source + EPP Image Override Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let each llm-d stack override the Helm chart source (repo/name/version) and the EPP image (registry/repository/tag) so an air-gapped install can point them at an internal mirror; NULL falls back to the global settings default.

**Architecture:** Six nullable columns on `custom_llmd_stack` (migration 039). `_application_for`/`_values_for` resolve `stack.X or settings.Y`. Create/Update accept the six fields; `_serialize` returns effective values; a new `chart-defaults` endpoint prefills the form. Spec: `docs/superpowers/specs/2026-07-10-llmd-chart-source-design.md`.

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic; Next.js + next-intl + TanStack Query.

## Global Constraints

- NULL/empty per-stack value = use the corresponding `settings.*` default (today's behavior). Existing stacks and non-air-gapped installs unchanged.
- Override resolution is `stack.X or settings.Y` at every render (create/update/status), never persisted as the default value — so changing the env default still moves un-overridden stacks.
- Migration `039_llmd_stack_chart_source`, `down_revision="038_member_budget_boost"` (verify against the actual `revision` in `038_member_budget_boost.py`).
- Backend gates: `cd backend && .venv/bin/python -m pytest tests/ -q` 0 NEW failures (baseline 21); `.venv/bin/ruff check app/ tests/` 0 NEW (baseline 78). Use `datetime.UTC` not `timezone.utc` if touching datetimes. Imports top-of-file.
- Frontend gates: `cd frontend && npm run lint` 0 NEW (baseline 4 errors/13 warnings); `npm run build` passes.
- Branch `feat/llmd-chart-source` (already checked out). Commits end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Migration 039 + model columns

**Files:**
- Create: `backend/migrations/versions/039_llmd_stack_chart_source.py`
- Modify: `backend/app/db/models/custom_llmd_stack.py`

**Interfaces:**
- Produces: `CustomLlmdStack.chart_repo/chart_name/chart_version/epp_registry/epp_repository/epp_tag: Mapped[str | None]`, consumed by Tasks 2–3.

- [ ] **Step 1: Write the migration**

Create `backend/migrations/versions/039_llmd_stack_chart_source.py`:

```python
"""Per-stack llm-d chart source + EPP image override.

Nullable overrides on custom_llmd_stack; NULL falls back to the global
settings default. Lets an air-gapped install point the chart repo and EPP
image at an internal mirror per stack.

Revision ID: 039_llmd_stack_chart_source
Revises: 038_member_budget_boost
"""

import sqlalchemy as sa
from alembic import op

revision = "039_llmd_stack_chart_source"
down_revision = "038_member_budget_boost"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("chart_repo", 512),
    ("chart_name", 256),
    ("chart_version", 128),
    ("epp_registry", 256),
    ("epp_repository", 256),
    ("epp_tag", 128),
)


def upgrade() -> None:
    for name, length in _COLUMNS:
        op.add_column("custom_llmd_stack", sa.Column(name, sa.String(length), nullable=True))


def downgrade() -> None:
    for name, _ in reversed(_COLUMNS):
        op.drop_column("custom_llmd_stack", name)
```

Before committing, open `backend/migrations/versions/038_member_budget_boost.py` and confirm its `revision = "038_member_budget_boost"` matches this file's `down_revision`; fix if different.

- [ ] **Step 2: Add the model columns**

In `backend/app/db/models/custom_llmd_stack.py`, insert after the `helm_values`/`values_snapshot` columns (before `created_by`):

```python
    # Air-gap overrides: chart source + EPP image. NULL = use the global
    # settings default (resolved at render time in the API layer).
    chart_repo: Mapped[str | None] = mapped_column(String(512), nullable=True)
    chart_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    chart_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    epp_registry: Mapped[str | None] = mapped_column(String(256), nullable=True)
    epp_repository: Mapped[str | None] = mapped_column(String(256), nullable=True)
    epp_tag: Mapped[str | None] = mapped_column(String(128), nullable=True)
```

(`String` and `Mapped`/`mapped_column` are already imported in this file.)

- [ ] **Step 3: Apply the migration + verify**

```bash
cd backend && APP_DATABASE_URL="postgresql+asyncpg://llmproxy:dbpassword9090@localhost:5432/litellm_portal" .venv/bin/alembic upgrade head
cd backend && APP_DATABASE_URL="postgresql+asyncpg://llmproxy:dbpassword9090@localhost:5432/litellm_portal" .venv/bin/alembic current
```

Expected: runs `038… -> 039_llmd_stack_chart_source`; `current` prints `039_llmd_stack_chart_source (head)`.

- [ ] **Step 4: Backend gates**

```bash
cd backend && .venv/bin/python -m pytest tests/ -q 2>&1 | tail -3
cd backend && .venv/bin/ruff check app/ tests/ 2>&1 | tail -2
```

Expected: 21 pre-existing failures (0 new), ruff 78 (0 new).

- [ ] **Step 5: Commit**

```bash
git add backend/migrations/versions/039_llmd_stack_chart_source.py backend/app/db/models/custom_llmd_stack.py
git commit -m "feat(db): per-stack llm-d chart source + EPP image columns

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Resolve overrides in rendering + create/update/serialize + chart-defaults endpoint

**Files:**
- Modify: `backend/app/api/llmd.py`
- Test: `backend/tests/test_llmd.py`, `backend/tests/test_llmd_manifests.py`

**Interfaces:**
- Consumes: Task 1's columns; `build_argo_application(...)` / `build_llmd_values(...)` (existing kwargs, unchanged signatures); `settings.llmd_chart_*` / `settings.llmd_epp_image_*`.
- Produces: `_serialize` returns effective chart/epp values + `chart_overrides`; `GET /api/admin/llmd-stacks/chart-defaults` returns the six settings defaults. Task 3's frontend relies on these JSON key names.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_llmd.py`, add a small helper stack and tests. Use a `types.SimpleNamespace` stack so no DB is needed for the resolver tests:

```python
import types

from app.api.llmd import _application_for, _serialize, _values_for


def _stack(**kw):
    base = dict(
        id=uuid.uuid4(), name="s", target_model_name="qwen", cluster_id=None,
        namespace="team-a", argo_app_name="llmd-s", helm_values={}, values_snapshot={},
        chart_repo=None, chart_name=None, chart_version=None,
        epp_registry=None, epp_repository=None, epp_tag=None,
        created_by=None, created_at=None, updated_at=None,
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_application_uses_stack_chart_override_when_set():
    stack = _stack(chart_repo="oci://mirror.internal/charts", chart_name="llmd", chart_version="1.2.3")
    app = _application_for(stack, "argocd", "https://kubernetes.default.svc")
    src = app["spec"]["source"]
    assert src["repoURL"] == "oci://mirror.internal/charts"
    assert src["chart"] == "llmd"
    assert src["targetRevision"] == "1.2.3"


def test_application_falls_back_to_settings_when_override_null():
    from app.config import settings

    app = _application_for(_stack(), "argocd", "https://kubernetes.default.svc")
    src = app["spec"]["source"]
    assert src["repoURL"] == settings.llmd_chart_repo
    assert src["chart"] == settings.llmd_chart_name
    assert src["targetRevision"] == settings.llmd_chart_version


def test_values_use_stack_epp_override_when_set():
    stack = _stack(epp_registry="mirror.internal", epp_repository="llm-d/epp", epp_tag="v9")
    img = _values_for(stack)["inferenceExtension"]["image"]
    assert img == {"registry": "mirror.internal", "repository": "llm-d/epp", "tag": "v9"}


def test_values_fall_back_to_settings_epp_when_null():
    from app.config import settings

    img = _values_for(_stack())["inferenceExtension"]["image"]
    assert img["registry"] == settings.llmd_epp_image_registry
    assert img["repository"] == settings.llmd_epp_image_repository
    assert img["tag"] == settings.llmd_epp_image_tag


def test_serialize_reports_effective_and_overrides():
    from app.config import settings

    over = _serialize(_stack(chart_repo="oci://mirror/x"), {"sync_status": "Synced"})
    assert over["chart_repo"] == "oci://mirror/x"                 # effective = override
    assert over["chart_overrides"]["chart_repo"] == "oci://mirror/x"
    assert over["chart_overrides"]["chart_name"] is None
    base = _serialize(_stack(), {"sync_status": "Synced"})
    assert base["chart_repo"] == settings.llmd_chart_repo         # effective = default
    assert base["chart_overrides"]["chart_repo"] is None
    assert base["epp_image"] == (
        f"{settings.llmd_epp_image_registry}/{settings.llmd_epp_image_repository}:{settings.llmd_epp_image_tag}"
    )


async def test_chart_defaults_endpoint(client_for_user, super_user, mock_db):
    from app.config import settings

    async with client_for_user(super_user) as client:
        resp = await client.get("/api/admin/llmd-stacks/chart-defaults")
    assert resp.status_code == 200
    body = resp.json()
    assert body["chart_repo"] == settings.llmd_chart_repo
    assert body["epp_registry"] == settings.llmd_epp_image_registry
    assert body["epp_tag"] == settings.llmd_epp_image_tag
```

(`uuid` is already imported in test_llmd.py; add `import types` if absent.)

- [ ] **Step 2: Run to verify they fail**

```bash
cd backend && .venv/bin/python -m pytest tests/test_llmd.py -q
```

Expected: the override tests fail (resolver still uses settings only), `chart_overrides`/`chart-defaults` missing.

- [ ] **Step 3: Implement the resolver + endpoint in `backend/app/api/llmd.py`**

1. Add a small helper near `_values_for` / `_application_for`:

```python
def _chart_source(stack: CustomLlmdStack) -> tuple[str, str, str]:
    return (
        stack.chart_repo or settings.llmd_chart_repo,
        stack.chart_name or settings.llmd_chart_name,
        stack.chart_version or settings.llmd_chart_version,
    )


def _epp_image(stack: CustomLlmdStack) -> tuple[str, str, str]:
    return (
        stack.epp_registry or settings.llmd_epp_image_registry,
        stack.epp_repository or settings.llmd_epp_image_repository,
        stack.epp_tag or settings.llmd_epp_image_tag,
    )
```

2. `_values_for`:

```python
def _values_for(stack: CustomLlmdStack) -> dict:
    registry, repository, tag = _epp_image(stack)
    return build_llmd_values(stack, epp_registry=registry, epp_repository=repository, epp_tag=tag)
```

3. `_application_for`:

```python
def _application_for(stack: CustomLlmdStack, argocd_namespace: str, destination_server: str) -> dict:
    chart_repo, chart_name, chart_version = _chart_source(stack)
    return build_argo_application(
        stack,
        chart_repo=chart_repo,
        chart_name=chart_name,
        chart_version=chart_version,
        values=stack.values_snapshot,
        project=settings.argo_project,
        argocd_namespace=argocd_namespace,
        destination_server=destination_server,
    )
```

4. `_serialize` — replace the four chart/epp lines with effective values + overrides:

```python
        "chart_repo": _chart_source(stack)[0],
        "chart_name": _chart_source(stack)[1],
        "chart_version": _chart_source(stack)[2],
        "epp_image": "{}/{}:{}".format(*_epp_image(stack)),
        "chart_overrides": {
            "chart_repo": stack.chart_repo,
            "chart_name": stack.chart_name,
            "chart_version": stack.chart_version,
            "epp_registry": stack.epp_registry,
            "epp_repository": stack.epp_repository,
            "epp_tag": stack.epp_tag,
        },
```

5. Add the `chart-defaults` GET route (place it with the other routes, and BEFORE any `/{stack_id}`-style route so it isn't shadowed — put it right after the `router = APIRouter(...)` block's first routes, e.g. just before `create_stack`):

```python
@router.get("/chart-defaults")
async def chart_defaults(user: CustomUser = Depends(require_super_user)) -> dict:
    """The global chart-source + EPP-image defaults, for prefilling the form."""
    return {
        "chart_repo": settings.llmd_chart_repo,
        "chart_name": settings.llmd_chart_name,
        "chart_version": settings.llmd_chart_version,
        "epp_registry": settings.llmd_epp_image_registry,
        "epp_repository": settings.llmd_epp_image_repository,
        "epp_tag": settings.llmd_epp_image_tag,
    }
```

Note: `/chart-defaults` does not collide with `/{stack_id}` GET routes because there is no bare `GET /{stack_id}` in this router (only `/{stack_id}/applied`), but define it before any dynamic route regardless.

- [ ] **Step 4: Accept + store the six fields on create/update**

1. Request models:

```python
class CreateLlmdStackRequest(BaseModel):
    name: str
    target_model_name: str
    cluster_id: str | None = None
    namespace: str = "default"
    values_yaml: str = ""
    chart_repo: str | None = None
    chart_name: str | None = None
    chart_version: str | None = None
    epp_registry: str | None = None
    epp_repository: str | None = None
    epp_tag: str | None = None


class UpdateLlmdStackRequest(BaseModel):
    namespace: str | None = None
    values_yaml: str | None = None
    chart_repo: str | None = None
    chart_name: str | None = None
    chart_version: str | None = None
    epp_registry: str | None = None
    epp_repository: str | None = None
    epp_tag: str | None = None
```

2. In `create_stack`, add the six fields to the `CustomLlmdStack(...)` constructor (empty → NULL), placed after `argo_app_name=app_name,`:

```python
        chart_repo=(body.chart_repo or "").strip() or None,
        chart_name=(body.chart_name or "").strip() or None,
        chart_version=(body.chart_version or "").strip() or None,
        epp_registry=(body.epp_registry or "").strip() or None,
        epp_repository=(body.epp_repository or "").strip() or None,
        epp_tag=(body.epp_tag or "").strip() or None,
```

Important: these must be set on the `stack` object **before** the `stack.values_snapshot = _values_for(stack)` line so the snapshot uses the overridden EPP image. The constructor runs before that line already — good.

3. In `update_stack`, after the `values_yaml` block and before `stack.values_snapshot = _values_for(stack)`:

```python
    for field in ("chart_repo", "chart_name", "chart_version", "epp_registry", "epp_repository", "epp_tag"):
        val = getattr(body, field)
        if val is not None:
            setattr(stack, field, val.strip() or None)
```

(This re-renders `values_snapshot` from the new EPP image and re-applies the Application — the existing lines below already do that.)

- [ ] **Step 5: Fix any existing test that asserted the old serialize keys**

The existing `test_llmd_manifests.py` may build applications directly; it should be unaffected (it calls `build_argo_application` with explicit kwargs). If any existing `test_llmd.py` test asserted `_serialize(...)["chart_repo"] == settings...` it still holds (effective == default when no override). Run and confirm; update only if a key was removed (none are — `chart_repo`/`chart_name`/`chart_version`/`epp_image` are all still present).

- [ ] **Step 6: Run tests + gates**

```bash
cd backend && .venv/bin/python -m pytest tests/test_llmd.py tests/test_llmd_manifests.py tests/test_llmd_k8s.py -q
cd backend && .venv/bin/python -m pytest tests/ -q 2>&1 | tail -3
cd backend && .venv/bin/ruff check app/ tests/ 2>&1 | tail -2
```

Expected: targeted green (new tests + existing); suite baseline unchanged; ruff 78.

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/llmd.py backend/tests/test_llmd.py
git commit -m "feat(llmd): per-stack chart source + EPP override resolved with settings fallback

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Frontend — chart-source / EPP section on the stack form

**Files:**
- Modify: `frontend/src/types/index.ts` (LlmdStackSummary: add `chart_name`, `chart_version` if absent, `chart_overrides`)
- Modify: `frontend/src/hooks/use-api.ts` (CreateLlmdStackBody/UpdateLlmdStackBody + `useLlmdChartDefaults`)
- Modify: `frontend/src/app/(app)/admin/llmd/new/page.tsx` (the section + form state + submit)
- Modify: `frontend/messages/en.json`, `frontend/messages/ko.json` (`llmd` namespace)

**Interfaces:**
- Consumes: Task 2's `chart-defaults` endpoint + `chart_overrides` in the stack summary.
- Produces: final task, nothing downstream.

- [ ] **Step 1: Baseline lint**

```bash
cd frontend && npm run lint 2>&1 | tail -5
```

Record counts (baseline 4 errors / 13 warnings).

- [ ] **Step 2: Types + hook + body**

`frontend/src/types/index.ts` — extend `LlmdStackSummary` (it has `chart_repo`/`chart_name`/`chart_version`/`epp_image` already per the file; add the overrides object):

```ts
  chart_overrides: {
    chart_repo: string | null;
    chart_name: string | null;
    chart_version: string | null;
    epp_registry: string | null;
    epp_repository: string | null;
    epp_tag: string | null;
  };
```

`frontend/src/hooks/use-api.ts` — extend both bodies with the six optional fields and add the defaults hook:

```ts
export interface CreateLlmdStackBody {
  name: string;
  target_model_name: string;
  cluster_id: string | null;
  namespace?: string;
  values_yaml?: string;
  chart_repo?: string | null;
  chart_name?: string | null;
  chart_version?: string | null;
  epp_registry?: string | null;
  epp_repository?: string | null;
  epp_tag?: string | null;
}

export interface UpdateLlmdStackBody {
  namespace?: string;
  values_yaml?: string;
  chart_repo?: string | null;
  chart_name?: string | null;
  chart_version?: string | null;
  epp_registry?: string | null;
  epp_repository?: string | null;
  epp_tag?: string | null;
}

export interface LlmdChartDefaults {
  chart_repo: string;
  chart_name: string;
  chart_version: string;
  epp_registry: string;
  epp_repository: string;
  epp_tag: string;
}

export function useLlmdChartDefaults() {
  return useQuery({
    queryKey: ["llmd-stacks", "chart-defaults"],
    queryFn: () => apiFetch<LlmdChartDefaults>("/api/admin/llmd-stacks/chart-defaults"),
  });
}
```

- [ ] **Step 3: Form state + prefill + submit (new-stack page)**

In `frontend/src/app/(app)/admin/llmd/new/page.tsx`:

1. Extend `FormState` and `EMPTY` with the six string fields (all `""`):

```ts
  chart_repo: string; chart_name: string; chart_version: string;
  epp_registry: string; epp_repository: string; epp_tag: string;
```

```ts
  chart_repo: "", chart_name: "", chart_version: "",
  epp_registry: "", epp_repository: "", epp_tag: "",
```

2. Fetch defaults and prefill once (place with the other hooks):

```ts
  const { data: chartDefaults } = useLlmdChartDefaults();
  useEffect(() => {
    if (!chartDefaults) return;
    setForm((f) => ({
      ...f,
      chart_repo: f.chart_repo || chartDefaults.chart_repo,
      chart_name: f.chart_name || chartDefaults.chart_name,
      chart_version: f.chart_version || chartDefaults.chart_version,
      epp_registry: f.epp_registry || chartDefaults.epp_registry,
      epp_repository: f.epp_repository || chartDefaults.epp_repository,
      epp_tag: f.epp_tag || chartDefaults.epp_tag,
    }));
  }, [chartDefaults]);
```

(Import `useLlmdChartDefaults` from `@/hooks/use-api`.)

3. Submit — send a field only when the admin **changed** it from the fetched default; otherwise send `null` so the stack stays un-overridden and keeps tracking the env default (per the spec's compatibility property). Add a helper and use it:

```ts
      const overrideOrNull = (val: string, def: string | undefined) =>
        val && val !== def ? val : null;
```

and in the `body`:

```ts
      chart_repo: overrideOrNull(form.chart_repo, chartDefaults?.chart_repo),
      chart_name: overrideOrNull(form.chart_name, chartDefaults?.chart_name),
      chart_version: overrideOrNull(form.chart_version, chartDefaults?.chart_version),
      epp_registry: overrideOrNull(form.epp_registry, chartDefaults?.epp_registry),
      epp_repository: overrideOrNull(form.epp_repository, chartDefaults?.epp_repository),
      epp_tag: overrideOrNull(form.epp_tag, chartDefaults?.epp_tag),
```

(So the common no-change case submits all-null → NULL columns → env-tracking; only an air-gap admin who overwrites a field pins that field.)

- [ ] **Step 4: Render the collapsible section**

Add a `<details>` block inside the form Card (after the values.yaml field, before the submit button). Use plain HTML `<details>`/`<summary>` (no new dependency):

```tsx
            <details className="rounded-md border p-3">
              <summary className="cursor-pointer text-sm font-medium">{t("chartSourceTitle")}</summary>
              <p className="text-xs text-muted-foreground mt-1">{t("chartSourceHint")}</p>
              <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
                <div>
                  <Label htmlFor="llmd-chart-repo">{t("chartRepo")}</Label>
                  <Input id="llmd-chart-repo" value={form.chart_repo}
                    onChange={(e) => setForm({ ...form, chart_repo: e.target.value })} />
                </div>
                <div>
                  <Label htmlFor="llmd-chart-name">{t("chartName")}</Label>
                  <Input id="llmd-chart-name" value={form.chart_name}
                    onChange={(e) => setForm({ ...form, chart_name: e.target.value })} />
                </div>
                <div>
                  <Label htmlFor="llmd-chart-version">{t("chartVersion")}</Label>
                  <Input id="llmd-chart-version" value={form.chart_version}
                    onChange={(e) => setForm({ ...form, chart_version: e.target.value })} />
                </div>
                <div>
                  <Label htmlFor="llmd-epp-registry">{t("eppRegistry")}</Label>
                  <Input id="llmd-epp-registry" value={form.epp_registry}
                    onChange={(e) => setForm({ ...form, epp_registry: e.target.value })} />
                </div>
                <div>
                  <Label htmlFor="llmd-epp-repository">{t("eppRepository")}</Label>
                  <Input id="llmd-epp-repository" value={form.epp_repository}
                    onChange={(e) => setForm({ ...form, epp_repository: e.target.value })} />
                </div>
                <div>
                  <Label htmlFor="llmd-epp-tag">{t("eppTag")}</Label>
                  <Input id="llmd-epp-tag" value={form.epp_tag}
                    onChange={(e) => setForm({ ...form, epp_tag: e.target.value })} />
                </div>
              </div>
            </details>
```

- [ ] **Step 5: i18n keys (both locales, `llmd` namespace)**

Confirm the form uses `useTranslations("llmd")` (grep the page); add keys under that namespace. `en.json`:

```json
"chartSourceTitle": "Chart source / EPP image (air-gap)",
"chartSourceHint": "Defaults point at public registries. In an air-gapped cluster, override these with your internal mirror URLs.",
"chartRepo": "Chart repo URL",
"chartName": "Chart name",
"chartVersion": "Chart version",
"eppRegistry": "EPP image registry",
"eppRepository": "EPP image repository",
"eppTag": "EPP image tag",
```

`ko.json`:

```json
"chartSourceTitle": "차트 소스 / EPP 이미지 (에어갭)",
"chartSourceHint": "기본값은 공개 레지스트리를 가리킵니다. 에어갭 클러스터에서는 내부 미러 URL로 바꿔 입력하세요.",
"chartRepo": "차트 repo URL",
"chartName": "차트 이름",
"chartVersion": "차트 버전",
"eppRegistry": "EPP 이미지 registry",
"eppRepository": "EPP 이미지 repository",
"eppTag": "EPP 이미지 tag",
```

If the page's `t` namespace is not exactly `llmd`, place the keys under whatever `useTranslations("...")` it uses (grep first).

- [ ] **Step 6: Gates**

```bash
cd frontend && npm run lint 2>&1 | tail -5
cd frontend && npm run build 2>&1 | tail -10
python3 -c "
import json
for lang in ('en','ko'):
    d = json.load(open(f'frontend/messages/{lang}.json'))
    # namespace confirmed in Step 5; assert keys exist somewhere under it
    found = any('chartSourceTitle' in v for v in d.values() if isinstance(v, dict))
    assert found, lang
print('i18n OK')
"
```

Expected: lint equals baseline (0 new); build succeeds; `i18n OK`.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/hooks/use-api.ts "frontend/src/app/(app)/admin/llmd/new/page.tsx" frontend/messages/en.json frontend/messages/ko.json
git commit -m "feat(frontend): per-stack chart source + EPP image fields on the llm-d form

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```
