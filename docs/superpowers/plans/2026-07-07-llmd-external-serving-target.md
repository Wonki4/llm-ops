# External Servings as llm-d Target Models — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the llm-d stack form pick externally-discovered vLLM/SGLang servings as the target model, choosing which of the serving's labels the EPP router selects on.

**Architecture:** Frontend-heavy. The `endpointSelector` is only a values.yaml prefill, so this adds a grouped target picker (portal + external), a label picker for external servings, and namespace/cluster auto-fill — plus one backend parameter (`endpoint_selector`) on the `/default-values` template endpoint so the prefilled values.yaml targets the external pods. No DB schema change.

**Tech Stack:** FastAPI + pydantic (backend); Next.js + react-query + next-intl (frontend); pytest `asyncio_mode = "auto"`.

**Spec:** `docs/superpowers/specs/2026-07-07-llmd-external-serving-target-design.md`

## Global Constraints

- Branch: all commits on `feat/llmd-external-target` (already checked out, based on current origin/main which includes the merged llm-d CRD form with `cluster_id`).
- Backend tests: `cd /Users/wongibaek/Documents/litellm-ops/backend && .venv/bin/pytest` (asyncio_mode auto). ~21 PRE-EXISTING failures (teams/keys/me/catalog/e2e) are unrelated; gate is **no NEW failures**.
- Ruff line-length 120 py311. `ruff check app/ tests/` has ~78 PRE-EXISTING errors repo-wide; gate is **0 new** — check your changed files are clean: `.venv/bin/ruff check <files>`.
- Frontend gates: `cd frontend && npx tsc --noEmit` (exit 0); `npm run lint` (no NEW errors beyond the 4 pre-existing: models/dashboard, models/history, settings, login). i18n keys go in BOTH `messages/en.json` and `messages/ko.json`, valid JSON.
- The llm-d router selector label constant is `LABEL_MODEL = "llm-ops/model-name"`. Portal targets keep the default `endpointSelector = "llm-ops/model-name=<target_model_name>"`; external targets use an explicit `key=value`.
- `endpointSelector` lives at `values["inferenceExtension"]["endpointsServer"]["endpointSelector"]`.
- No new stack column / migration. Submit body shape is unchanged (`name`, `target_model_name`, `cluster_id`, `namespace`, `values_yaml`).

## File Map

| File | Action | Responsibility |
|---|---|---|
| `backend/app/services/llmd_manifests.py` | Modify | `default_llmd_values` accepts `endpoint_selector` |
| `backend/app/api/llmd.py` | Modify | `DefaultValuesRequest.endpoint_selector` + pass-through |
| `backend/tests/test_llmd_manifests.py` | Modify | Cover explicit-selector + fallback |
| `frontend/src/hooks/use-api.ts` | Modify | `useLlmdDefaultValues` takes `{target_model_name, endpoint_selector?}` |
| `frontend/src/app/(app)/admin/llmd/new/page.tsx` | Modify | Grouped picker, external label select, auto-fill, wiring |
| `frontend/messages/en.json`, `frontend/messages/ko.json` | Modify | New llm-d strings |

---

### Task 1: Backend — explicit endpoint_selector on the template

**Files:**
- Modify: `backend/app/services/llmd_manifests.py` (`default_llmd_values`)
- Modify: `backend/app/api/llmd.py` (`DefaultValuesRequest`, `default_values` handler)
- Test: `backend/tests/test_llmd_manifests.py`

**Interfaces:**
- Produces (Task 2 consumes): `POST /api/admin/llmd-stacks/default-values` accepts optional `endpoint_selector: str | None`; when truthy it becomes the template's `endpointSelector` verbatim, else the `llm-ops/model-name=<target_model_name>` default.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_llmd_manifests.py`:

```python
def test_default_values_uses_explicit_endpoint_selector():
    v = default_llmd_values(
        "qwen", epp_registry="r", epp_repository="repo", epp_tag="t",
        endpoint_selector="app=my-vllm",
    )
    assert v["inferenceExtension"]["endpointsServer"]["endpointSelector"] == "app=my-vllm"


def test_default_values_falls_back_to_model_label():
    v = default_llmd_values("qwen", epp_registry="r", epp_repository="repo", epp_tag="t")
    assert v["inferenceExtension"]["endpointsServer"]["endpointSelector"] == "llm-ops/model-name=qwen"
```

(`default_llmd_values` is already imported at the top of this file from the coverage-restore work; verify the import line `from app.services.llmd_manifests import (... default_llmd_values ...)` is present — if not, add it.)

- [ ] **Step 2: Run to verify fail**

Run: `cd /Users/wongibaek/Documents/litellm-ops/backend && .venv/bin/pytest tests/test_llmd_manifests.py -k endpoint_selector -v`
Expected: FAIL — `default_llmd_values()` got an unexpected keyword argument `endpoint_selector`.

- [ ] **Step 3: Implement**

In `backend/app/services/llmd_manifests.py`, change the `default_llmd_values` signature and the `endpointSelector` line. The current function starts:

```python
def default_llmd_values(
    target_model_name: str, *, epp_registry: str, epp_repository: str, epp_tag: str
) -> dict:
```

Change the signature to add the kwarg:

```python
def default_llmd_values(
    target_model_name: str,
    *,
    epp_registry: str,
    epp_repository: str,
    epp_tag: str,
    endpoint_selector: str | None = None,
) -> dict:
```

Inside the function, just before the `return {`, compute the selector, and use it in the dict. The current line is:

```python
                "endpointSelector": f"{LABEL_MODEL}={target_model_name}" if target_model_name else "",
```

Replace it with a variable set above the return:

```python
    selector = endpoint_selector or (f"{LABEL_MODEL}={target_model_name}" if target_model_name else "")
```

and in the returned dict:

```python
                "endpointSelector": selector,
```

In `backend/app/api/llmd.py`, `DefaultValuesRequest` (currently `target_model_name: str = ""`) becomes:

```python
class DefaultValuesRequest(BaseModel):
    target_model_name: str = ""
    endpoint_selector: str | None = None
```

and the `default_values` handler call to `default_llmd_values(body.target_model_name, epp_registry=..., ...)` gains the kwarg:

```python
    values = default_llmd_values(
        body.target_model_name,
        epp_registry=settings.llmd_epp_image_registry,
        epp_repository=settings.llmd_epp_image_repository,
        epp_tag=settings.llmd_epp_image_tag,
        endpoint_selector=body.endpoint_selector,
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `cd /Users/wongibaek/Documents/litellm-ops/backend && .venv/bin/pytest tests/test_llmd_manifests.py -v && .venv/bin/ruff check app/services/llmd_manifests.py app/api/llmd.py tests/test_llmd_manifests.py`
Expected: all pass; ruff clean on these files.

- [ ] **Step 5: Commit**

```bash
cd /Users/wongibaek/Documents/litellm-ops
git add backend/app/services/llmd_manifests.py backend/app/api/llmd.py backend/tests/test_llmd_manifests.py
git commit -m "feat(llmd): default-values accepts an explicit endpoint_selector"
```

---

### Task 2: Frontend — grouped target picker + external label select

**Files:**
- Modify: `frontend/src/hooks/use-api.ts` (`useLlmdDefaultValues`)
- Modify: `frontend/src/app/(app)/admin/llmd/new/page.tsx`
- Modify: `frontend/messages/en.json`, `frontend/messages/ko.json`

**Interfaces:**
- Consumes: Task 1's `endpoint_selector` param; `useExternalServings()` (returns `{servings: ExternalServing[], errors}`) and `ExternalServing` (`cluster_id: string|null`, `namespace`, `deployment_name`, `engine`, `labels: Record<string,string>`, `registration: {model_name}|null`) — both already exported from `use-api.ts`.

- [ ] **Step 1: Change the hook to take an object**

In `frontend/src/hooks/use-api.ts`, replace `useLlmdDefaultValues` (currently takes a bare string):

```ts
export interface LlmdDefaultValuesBody {
  target_model_name: string;
  endpoint_selector?: string;
}

export function useLlmdDefaultValues() {
  return useMutation({
    mutationFn: (body: LlmdDefaultValuesBody) =>
      apiFetch<LlmdDefaultValuesResponse>("/api/admin/llmd-stacks/default-values", {
        method: "POST",
        body: JSON.stringify(body),
      }),
  });
}
```

- [ ] **Step 2: Add i18n keys (en + ko)**

In BOTH `frontend/messages/en.json` and `frontend/messages/ko.json`, inside the existing `"llmd"` object, add:

en.json:
```json
"targetGroupPortal": "Portal deployments",
"targetGroupExternal": "External servings",
"endpointLabelLabel": "Router label selector",
"endpointLabelHint": "Which label the EPP router uses to find this serving's pods.",
"endpointLabelNone": "This serving has no labels — set endpointSelector directly in values.yaml below."
```

ko.json:
```json
"targetGroupPortal": "포털 배포",
"targetGroupExternal": "외부 서빙",
"endpointLabelLabel": "라우터 라벨 셀렉터",
"endpointLabelHint": "EPP 라우터가 이 서빙의 파드를 찾는 데 쓸 라벨입니다.",
"endpointLabelNone": "이 서빙에는 라벨이 없습니다 — 아래 values.yaml에서 endpointSelector를 직접 지정하세요."
```

- [ ] **Step 3: Wire the form — state, hooks, handlers**

In `frontend/src/app/(app)/admin/llmd/new/page.tsx`:

Extend `FormState` and `EMPTY`:

```tsx
type FormState = {
  name: string;
  target_model_name: string;
  target_kind: "portal" | "external";
  endpoint_selector: string;
  cluster_id: string;
  namespace: string;
  values_yaml: string;
};

const EMPTY: FormState = {
  name: "",
  target_model_name: "",
  target_kind: "portal",
  endpoint_selector: "",
  cluster_id: "",
  namespace: "default",
  values_yaml: "",
};
```

Add the external-servings hook + a selected-serving state (near the other hooks around line 41-47). Import `useExternalServings` and the `ExternalServing` type from `@/hooks/use-api` (add to the existing import). Then:

```tsx
  const { data: external } = useExternalServings();
  const servings = external?.servings ?? [];
  const [selectedExternal, setSelectedExternal] = useState<ExternalServing | null>(null);
```

Replace the `loadDefaults` `useEffect` and `resetDefaults` so they pass the selector:

```tsx
  const loadDefaults = defaultsMut.mutate;
  useEffect(() => {
    if (valuesTouched) return;
    loadDefaults(
      { target_model_name: form.target_model_name, endpoint_selector: form.endpoint_selector || undefined },
      { onSuccess: (r) => setForm((f) => ({ ...f, values_yaml: r.values_yaml })) },
    );
  }, [form.target_model_name, form.endpoint_selector, valuesTouched, loadDefaults]);

  const resetDefaults = () => {
    defaultsMut.mutate(
      { target_model_name: form.target_model_name, endpoint_selector: form.endpoint_selector || undefined },
      { onSuccess: (r) => { setForm((f) => ({ ...f, values_yaml: r.values_yaml })); setValuesTouched(false); } },
    );
  };
```

Add a target-change handler + the select's controlled value, above the `return (`:

```tsx
  const externalKey = (s: ExternalServing) => `ext::${s.cluster_id ?? ""}::${s.namespace}::${s.deployment_name}`;
  const targetSelectValue =
    form.target_kind === "external" && selectedExternal ? externalKey(selectedExternal) : form.target_model_name;

  const onTargetChange = (value: string) => {
    if (value.startsWith("ext::")) {
      const serving = servings.find((s) => externalKey(s) === value);
      if (!serving) return;
      const labels = Object.entries(serving.labels);
      const preferred =
        labels.find(([k]) => k === "app") ?? labels.find(([k]) => k === "app.kubernetes.io/name") ?? labels[0];
      setSelectedExternal(serving);
      setForm((f) => ({
        ...f,
        target_model_name: serving.registration?.model_name || serving.deployment_name,
        target_kind: "external",
        endpoint_selector: preferred ? `${preferred[0]}=${preferred[1]}` : "",
        namespace: serving.namespace,
        cluster_id: serving.cluster_id ?? "",
      }));
    } else {
      setSelectedExternal(null);
      setForm((f) => ({ ...f, target_model_name: value, target_kind: "portal", endpoint_selector: "" }));
    }
  };
```

- [ ] **Step 4: Wire the form — the JSX (grouped select + label select)**

Replace the target-model `<select>` block (currently lines ~108-121, the `<Label htmlFor="llmd-model">` div) with:

```tsx
              <div className="space-y-2">
                <Label htmlFor="llmd-model">{t("targetModel")}</Label>
                <select
                  id="llmd-model"
                  value={targetSelectValue}
                  onChange={(e) => onTargetChange(e.target.value)}
                  className="w-full h-9 rounded-md border border-input bg-transparent px-3 text-sm"
                >
                  <option value="">{t("targetModelPlaceholder")}</option>
                  <optgroup label={t("targetGroupPortal")}>
                    {(deployments ?? []).map((d) => (
                      <option key={d.id} value={d.model_name}>{d.model_name}</option>
                    ))}
                  </optgroup>
                  <optgroup label={t("targetGroupExternal")}>
                    {servings.map((s) => (
                      <option key={externalKey(s)} value={externalKey(s)}>
                        {s.deployment_name} ({s.engine} · {s.namespace})
                      </option>
                    ))}
                  </optgroup>
                </select>
                <p className="text-xs text-muted-foreground">{t("targetModelHint")}</p>
              </div>
```

Immediately AFTER that `</div>` (before the cluster `<select>` div), add the external label picker:

```tsx
              {form.target_kind === "external" && selectedExternal && (
                <div className="space-y-2">
                  <Label htmlFor="llmd-endpoint-label">{t("endpointLabelLabel")}</Label>
                  {Object.keys(selectedExternal.labels).length === 0 ? (
                    <p className="text-xs text-muted-foreground">{t("endpointLabelNone")}</p>
                  ) : (
                    <select
                      id="llmd-endpoint-label"
                      value={form.endpoint_selector}
                      onChange={(e) => setForm({ ...form, endpoint_selector: e.target.value })}
                      className="w-full h-9 rounded-md border border-input bg-transparent px-3 text-sm"
                    >
                      {Object.entries(selectedExternal.labels).map(([k, v]) => (
                        <option key={`${k}=${v}`} value={`${k}=${v}`}>{k}={v}</option>
                      ))}
                    </select>
                  )}
                  <p className="text-xs text-muted-foreground">{t("endpointLabelHint")}</p>
                </div>
              )}
```

(No change to `handleSubmit` — the submit body already sends `target_model_name`/`cluster_id`/`namespace`/`values_yaml`, and the external selector is baked into `values_yaml` by the default-values fetch.)

- [ ] **Step 5: Gates**

Run:
```bash
cd /Users/wongibaek/Documents/litellm-ops/frontend
python3 -c "import json; json.load(open('messages/en.json')); json.load(open('messages/ko.json')); print('json ok')"
npx tsc --noEmit && echo TSC_OK
npm run lint 2>&1 | tail -2
```
Expected: json ok; TSC_OK; lint at the 4-error pre-existing baseline (no new).

- [ ] **Step 6: Commit**

```bash
cd /Users/wongibaek/Documents/litellm-ops
git add frontend/src/hooks/use-api.ts "frontend/src/app/(app)/admin/llmd/new/page.tsx" frontend/messages/en.json frontend/messages/ko.json
git commit -m "feat(frontend): pick external servings as llm-d target, choose router label"
```

---

### Task 3: Verification + local smoke

**Files:** none new.

- [ ] **Step 1: Full backend suite + ruff**

```bash
cd /Users/wongibaek/Documents/litellm-ops/backend
.venv/bin/pytest -q 2>&1 | tail -1
.venv/bin/ruff check app/ tests/ 2>&1 | tail -1
```
Expected: no NEW failures vs the ~21 pre-existing baseline; ruff error count unchanged (~78 pre-existing, 0 new).

- [ ] **Step 2: Frontend build**

```bash
cd /Users/wongibaek/Documents/litellm-ops/frontend && npm run build 2>&1 | tail -5
```
Expected: build succeeds.

- [ ] **Step 3: Rebuild + smoke the running stack**

```bash
cd /Users/wongibaek/Documents/litellm-ops
docker compose up -d --build backend frontend
```
Wait for backend healthy, then:
```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8002/api/admin/llmd-stacks/default-values
```
Expected: `405` or `401` (POST-only route requires auth/method — proves it resolves; a `500` would be a regression). Then in the browser at http://localhost:3003/admin/llmd/new confirm the target dropdown shows a "Portal deployments" group and an "External servings" group; selecting an external serving reveals the label selector and auto-fills namespace.

- [ ] **Step 4: Wrap up**

Use superpowers:finishing-a-development-branch.

**Note:** the running local stack currently discovers no external servings (no kubeconfig configured), so the "External servings" group will be empty locally — that is expected; the grouping/label UI is verifiable with a configured cluster. The backend selector logic is covered by the Task 1 unit tests.
