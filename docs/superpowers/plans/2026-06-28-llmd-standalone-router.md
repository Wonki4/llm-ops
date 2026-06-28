# llm-d standalone router Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the portal's existing GIE-standalone "llm-d" stack into the real llm-d standalone router by swapping the EPP image to llm-d's, reusing the existing `custom_llmd_stack` / ArgoCD / `helm_values` plumbing.

**Architecture:** We already deploy the `gateway-api-inference-extension` (GIE) `standalone` chart via an ArgoCD `Application` whose Helm `valuesObject` comes from `custom_llmd_stack.helm_values`. The Task 1 spike confirmed this chart ALREADY co-locates an Envoy sidecar with the EPP and ships cache-aware scorers (queue / kv-cache / prefix-cache) by default. llm-d's EPP is "GIE EPP extended," so the real delta is a single values change — point the EPP image at llm-d's. Standalone mode (Envoy + EPP in one pod) means no Gateway API provider and no `llm-d-infra`.

**Tech Stack:** FastAPI + SQLAlchemy (backend), pydantic-settings, pytest/pytest-asyncio, ArgoCD + Helm (GIE `standalone` chart `v1.5.0`), Next.js + next-intl (frontend), minikube `portal-test` (M1, no GPU).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-06-28-llmd-standalone-router-design.md` — every task implicitly inherits it.
- **Branch:** `feat/llmd-standalone-router`, stacked on `feat/llmd-serving-management` (PR #174). Recommend merging #174 to `main` before/around implementation; this branch then rebases onto `main` (patch-id auto-drops the stacked commits, as with #172/#173).
- **No DB migration** — `helm_values` JSONB already holds arbitrary values; only the default template changes.
- **No frontend schema change** — copy + one display field only.
- Chart stays GIE `standalone` `v1.5.0` (`settings.llmd_chart_*` unchanged).
- **Task 1 spike result (DONE, PROCEED-A + decision A1):** real chart keys are `inferenceExtension.image.{registry,repository,tag}` for the EPP image. The chart enables the Envoy sidecar by DEFAULT (`inferenceExtension.sidecar.enabled: true`) and ships cache-aware scorers (queue-scorer, kv-cache-utilization-scorer, prefix-cache-scorer) in its default templated `EndpointPickerConfig`. There is **NO** `proxy.*` and **NO** free-form `plugins` values key. **Decision A1:** override ONLY the EPP image (to llm-d's `ghcr.io/llm-d/llm-d-router-endpoint-picker:v0.8.1`); rely on the chart defaults for the sidecar and scorers; keep the single-helm-chart plumbing (no Kustomize/multi-source). Custom llm-d scorer configs (A2) are out of scope.
- llm-d EPP image lives on `ghcr.io` (NOT the GIE `registry.k8s.io`), so it gets its own registry/repository/tag settings, distinct from `llmd_image_registry`.
- Air-gap: all images/charts must be overridable to an internal registry (no hardcoded external pulls). EPP image is configured via settings, never literal in business logic.
- Backend tests: run in `backend/.venv` (Python ≥3.11, `pip install -e ".[dev]"`). Verified command form: `cd backend && .venv/bin/python -m pytest tests/test_llmd.py -q`.
- Container note: `litellm_backend` runtime image has NO pytest; use the venv for tests. Live deploy uses the running stack + minikube `portal-test`.

---

### Task 1: Gating spike — confirm the GIE `standalone` chart accepts our overrides  (COMPLETE — commit 81c597e, decision PROCEED-A / A1)

**Files:**
- Create: `docs/superpowers/plans/2026-06-28-llmd-spike-notes.md` (records the confirmed value keys; consumed by Tasks 3–4)

**Interfaces:**
- Produces: the exact Helm value keys for (a) overriding the EPP container image, (b) the Envoy sidecar proxy, (c) the scheduler/plugins config. Tasks 3–6 read these from the notes file.

This task writes NO application code. It is a go/no-go gate: if any of the three overrides is unsupported by the chart, STOP and switch to Approach B (deploy llm-d-router's own Kustomize/images as a separate ArgoCD source) — see the spec — before continuing.

**Outcome (recorded in spike notes):** PROCEED with Approach A. (a) EPP image override CONFIRMED via `inferenceExtension.image.{registry,repository,tag}`. (b) Envoy sidecar already ENABLED by default. (c) Default plugin config already cache-aware; no free-form plugins values key (custom scorers would need a ConfigMap swap — deferred as A2). User chose **A1**: image swap only.

> Task 1 is complete. Its spike notes (`docs/superpowers/plans/2026-06-28-llmd-spike-notes.md`) are authoritative for chart-facing key names used in Tasks 3–6.

---

### Task 2: Repair stale `test_llmd.py` to the #174 `helm_values` reality (baseline green)

PR #174 refactored `CustomLlmdStack` to a single `helm_values` JSONB and moved schema generation into `default_llmd_values()`, but did not update `tests/test_llmd.py`. Three tests fail today. Get them green against current code BEFORE changing behavior.

**Files:**
- Modify: `backend/tests/test_llmd.py:15-24` (`test_model_has_expected_columns`)
- Modify: `backend/tests/test_llmd.py:34-42` (`_stack` helper)
- Modify: `backend/tests/test_llmd.py:56-80` (`test_build_values_*`)

**Interfaces:**
- Consumes: `CustomLlmdStack` (columns: `id, name, target_model_name, argocd_connection_id, cluster_id, namespace, argo_app_name, helm_values, values_snapshot, created_by, updated_by, created_at, updated_at`); `build_llmd_values(stack, *, image_registry)` returns `deep_merge({"inferenceExtension": {"image": {"registry": image_registry}}}, stack.helm_values or {})`; `default_llmd_values(target_model_name, *, image_registry)`.

> Note: Tasks 3–5 will change the `build_llmd_values` / `default_llmd_values` signatures again. Task 2 only restores green against the CURRENT (#174) code — do not anticipate the later signature here.

- [ ] **Step 1: Run the suite to confirm the 3 known failures**

Run: `cd backend && .venv/bin/python -m pytest tests/test_llmd.py -q`
Expected: `3 failed, 7 passed` — failures are `test_model_has_expected_columns`, `test_build_values_standalone_schema_and_default_selector`, `test_build_values_custom_selector_and_override` (AttributeError: SimpleNamespace has no attribute 'helm_values').

- [ ] **Step 2: Rewrite the three tests to the current model**

Replace `test_model_has_expected_columns` (lines 15-24):

```python
def test_model_has_expected_columns():
    cols = set(CustomLlmdStack.__table__.columns.keys())
    assert {
        "id", "name", "target_model_name", "argocd_connection_id", "cluster_id",
        "namespace", "argo_app_name", "helm_values", "values_snapshot",
        "created_by", "updated_by", "created_at", "updated_at",
    } <= cols
    # Structured per-field columns were collapsed into helm_values in #174.
    assert not (
        {"replicas", "model_server_type", "target_port", "endpoint_selector", "values_override"} & cols
    )
```

Replace the `_stack` helper (lines 34-42):

```python
def _stack(**kw):
    base = dict(
        id=uuid.uuid4(), name="my-stack", target_model_name="opt-125m",
        namespace="llmd-my-stack", argo_app_name="llmd-my-stack", helm_values={},
    )
    base.update(kw)
    return types.SimpleNamespace(**base)
```

Replace both `test_build_values_*` tests (lines 56-80):

```python
def test_build_values_merges_image_registry_base_under_helm_values():
    v = build_llmd_values(_stack(), image_registry="reg.local")
    assert v["inferenceExtension"]["image"]["registry"] == "reg.local"


def test_build_values_user_helm_values_win_over_base():
    v = build_llmd_values(
        _stack(helm_values={"inferenceExtension": {"image": {"registry": "user.reg", "tag": "v9"}},
                            "tracing": {"enabled": True}}),
        image_registry="reg.local",
    )
    # User registry overrides the base; unrelated keys pass through.
    assert v["inferenceExtension"]["image"] == {"registry": "user.reg", "tag": "v9"}
    assert v["tracing"] == {"enabled": True}


def test_default_values_standalone_schema_and_default_selector():
    v = default_llmd_values("opt-125m", image_registry="reg.local")
    es = v["inferenceExtension"]["endpointsServer"]
    assert es["endpointSelector"] == "llm-ops/model-name=opt-125m"
    assert es["targetPorts"] == 8000
    assert es["modelServerType"] == "vllm"
```

Add `default_llmd_values` to the import at the top of the file (line 6-12 block):

```python
from app.services.llmd_manifests import (
    MANAGED_BY,
    argo_app_name_for,
    build_argo_application,
    build_llmd_values,
    deep_merge,
    default_llmd_values,
)
```

- [ ] **Step 3: Run the suite — all green**

Run: `cd backend && .venv/bin/python -m pytest tests/test_llmd.py -q`
Expected: `10 passed`.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_llmd.py
git commit -m "test(llmd): repair stale tests after #174 helm_values refactor"
```

---

### Task 3: Add llm-d EPP image settings to config

**Files:**
- Modify: `backend/app/config.py:68-72` (llm-d settings block)
- Modify: `backend/tests/test_llmd.py` (extend `test_llmd_settings_target_standalone_chart`)

**Interfaces:**
- Produces: `settings.llmd_epp_image_registry: str` (default `"ghcr.io"`), `settings.llmd_epp_image_repository: str` (default `"llm-d/llm-d-router-endpoint-picker"`), `settings.llmd_epp_image_tag: str` (default `"v0.8.1"`). Consumed by Tasks 4–6. Per the Task 1 spike, the llm-d EPP lives on `ghcr.io` (NOT the GIE `registry.k8s.io`), so it needs its own registry setting separate from `llmd_image_registry`.

- [ ] **Step 1: Write the failing test**

Extend `test_llmd_settings_target_standalone_chart` in `backend/tests/test_llmd.py`:

```python
def test_llmd_settings_target_standalone_chart():
    assert settings.argo_project == "llm-d"
    assert settings.llmd_chart_name == "standalone"
    assert settings.llmd_chart_version == "v1.5.0"
    assert "gateway-api-inference-extension" in settings.llmd_chart_repo
    # Real llm-d EPP image, on ghcr.io (overridable for air-gap).
    assert settings.llmd_epp_image_registry == "ghcr.io"
    assert settings.llmd_epp_image_repository == "llm-d/llm-d-router-endpoint-picker"
    assert settings.llmd_epp_image_tag == "v0.8.1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_llmd.py::test_llmd_settings_target_standalone_chart -q`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'llmd_epp_image_registry'`.

- [ ] **Step 3: Add the settings**

In `backend/app/config.py`, after line 71 (`llmd_image_registry: str = "registry.k8s.io"`), add:

```python
    # llm-d router EPP image — GIE EPP extended with llm-d's routing intelligence.
    # Lives on ghcr.io (NOT the GIE registry.k8s.io). Air-gap: mirror + override all three.
    llmd_epp_image_registry: str = "ghcr.io"
    llmd_epp_image_repository: str = "llm-d/llm-d-router-endpoint-picker"
    llmd_epp_image_tag: str = "v0.8.1"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_llmd.py::test_llmd_settings_target_standalone_chart -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/config.py backend/tests/test_llmd.py
git commit -m "feat(llmd): add configurable llm-d EPP image settings"
```

---

### Task 4: Swap EPP image to llm-d's in `default_llmd_values()` (sidecar + scorers stay chart defaults)

Per Task 1 (A1): the chart already enables the Envoy sidecar and ships cache-aware scorers by default. We override ONLY the EPP image. Do NOT add `proxy.*` or `plugins.*` — those values keys do not exist in this chart.

**Files:**
- Modify: `backend/app/services/llmd_manifests.py:40-61` (`default_llmd_values`)
- Modify: `backend/app/api/llmd.py:338-340, 374` (pass the new EPP image args)
- Modify: `backend/tests/test_llmd.py` (replace `test_default_values_standalone_schema_and_default_selector` from Task 2)

**Interfaces:**
- Consumes: `settings.llmd_epp_image_registry/repository/tag` (Task 3); `LABEL_MODEL` (`"llm-ops/model-name"`).
- Produces: `default_llmd_values(target_model_name, *, epp_registry, epp_repository, epp_tag) -> dict`. Consumed by `api/llmd.py` create + default-values endpoints.

- [ ] **Step 1: Write the failing test**

Replace `test_default_values_standalone_schema_and_default_selector` (added in Task 2) in `backend/tests/test_llmd.py` with:

```python
def test_default_values_is_real_router_template():
    v = default_llmd_values(
        "opt-125m", epp_registry="reg.local",
        epp_repository="llm-d/llm-d-router-endpoint-picker", epp_tag="v0.8.1",
    )
    ie = v["inferenceExtension"]
    # llm-d EPP image (not vanilla GIE)
    assert ie["image"] == {
        "registry": "reg.local",
        "repository": "llm-d/llm-d-router-endpoint-picker",
        "tag": "v0.8.1",
    }
    # Target existing model servers; don't create an InferencePool
    es = ie["endpointsServer"]
    assert es["createInferencePool"] is False
    assert es["endpointSelector"] == "llm-ops/model-name=opt-125m"
    assert es["targetPorts"] == 8000
    assert es["modelServerType"] == "vllm"
    # A1: sidecar + scorers come from the chart defaults — these values keys do
    # NOT exist in the GIE standalone chart, so we must not emit them.
    assert "proxy" not in ie
    assert "plugins" not in ie


def test_default_values_blank_model_yields_empty_selector():
    v = default_llmd_values(
        "", epp_registry="reg.local",
        epp_repository="llm-d/llm-d-router-endpoint-picker", epp_tag="v0.8.1",
    )
    assert v["inferenceExtension"]["endpointsServer"]["endpointSelector"] == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_llmd.py::test_default_values_is_real_router_template -q`
Expected: FAIL — `TypeError: default_llmd_values() got an unexpected keyword argument 'epp_registry'`.

- [ ] **Step 3: Rewrite `default_llmd_values`**

Replace `backend/app/services/llmd_manifests.py:40-61` with:

```python
def default_llmd_values(
    target_model_name: str, *, epp_registry: str, epp_repository: str, epp_tag: str
) -> dict:
    """The starter ``values.yaml`` for a new stack: the llm-d **standalone router**.

    The GIE ``standalone`` chart already co-locates an Envoy sidecar with the EPP
    and ships cache-aware scorers (queue / kv-cache / prefix-cache) in its default
    EndpointPickerConfig. To get the *llm-d* router we only swap the EPP image to
    llm-d's (GIE EPP extended with llm-d's routing intelligence); the sidecar and
    scorers come from chart defaults. The router fronts already-running model
    servers selected by ``endpointSelector`` on ``targetPorts`` (no InferencePool,
    no Gateway API provider). The user edits this freely.
    """
    return {
        "inferenceExtension": {
            "replicas": 1,
            "image": {"registry": epp_registry, "repository": epp_repository, "tag": epp_tag},
            "endpointsServer": {
                "createInferencePool": False,
                "endpointSelector": f"{LABEL_MODEL}={target_model_name}" if target_model_name else "",
                "targetPorts": 8000,
                "modelServerType": "vllm",
            },
        },
    }
```

- [ ] **Step 4: Update the two call sites in `api/llmd.py`**

`backend/app/api/llmd.py:338-340` (create) — replace the `default_llmd_values(...)` call:

```python
    helm_values = _parse_values_yaml(body.values_yaml) or default_llmd_values(
        body.target_model_name,
        epp_registry=settings.llmd_epp_image_registry,
        epp_repository=settings.llmd_epp_image_repository,
        epp_tag=settings.llmd_epp_image_tag,
    )
```

`backend/app/api/llmd.py:374` (default-values endpoint) — replace:

```python
    values = default_llmd_values(
        body.target_model_name,
        epp_registry=settings.llmd_epp_image_registry,
        epp_repository=settings.llmd_epp_image_repository,
        epp_tag=settings.llmd_epp_image_tag,
    )
```

- [ ] **Step 5: Run the full llmd suite**

Run: `cd backend && .venv/bin/python -m pytest tests/test_llmd.py -q`
Expected: all passed (was 10; the replaced default-values test plus the new blank-model test → 11 passed).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/llmd_manifests.py backend/app/api/llmd.py backend/tests/test_llmd.py
git commit -m "feat(llmd): swap EPP image to llm-d's (sidecar + scorers from chart defaults)"
```

---

### Task 5: Default the EPP image under user `helm_values` in `build_llmd_values()`

So a stored stack whose `helm_values` omits the image still gets the llm-d EPP image. User values always win.

**Files:**
- Modify: `backend/app/services/llmd_manifests.py:64-70` (`build_llmd_values`)
- Modify: `backend/app/api/llmd.py:85-87` (`_values_for` — pass EPP image)
- Modify: `backend/tests/test_llmd.py` (replace the build-values tests from Task 2)

**Interfaces:**
- Produces: `build_llmd_values(stack, *, epp_registry, epp_repository, epp_tag) -> dict` — base is `{"inferenceExtension": {"image": {"registry": epp_registry, "repository": epp_repository, "tag": epp_tag}}}` deep-merged UNDER `stack.helm_values`.

- [ ] **Step 1: Write the failing test**

Replace `test_build_values_merges_image_registry_base_under_helm_values` and `test_build_values_user_helm_values_win_over_base` (from Task 2) in `backend/tests/test_llmd.py` with:

```python
def test_build_values_merges_epp_image_base_under_helm_values():
    v = build_llmd_values(
        _stack(), epp_registry="reg.local",
        epp_repository="llm-d/llm-d-router-endpoint-picker", epp_tag="v0.8.1",
    )
    assert v["inferenceExtension"]["image"] == {
        "registry": "reg.local", "repository": "llm-d/llm-d-router-endpoint-picker", "tag": "v0.8.1",
    }


def test_build_values_user_helm_values_win_over_base():
    v = build_llmd_values(
        _stack(helm_values={"inferenceExtension": {"image": {"tag": "custom"}}, "tracing": {"enabled": True}}),
        epp_registry="reg.local",
        epp_repository="llm-d/llm-d-router-endpoint-picker", epp_tag="v0.8.1",
    )
    img = v["inferenceExtension"]["image"]
    assert img["registry"] == "reg.local"
    assert img["repository"] == "llm-d/llm-d-router-endpoint-picker"
    assert img["tag"] == "custom"           # user wins
    assert v["tracing"] == {"enabled": True}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_llmd.py::test_build_values_merges_epp_image_base_under_helm_values -q`
Expected: FAIL — `TypeError: build_llmd_values() got an unexpected keyword argument 'epp_registry'`.

- [ ] **Step 3: Update `build_llmd_values`**

Replace `backend/app/services/llmd_manifests.py:64-70`:

```python
def build_llmd_values(
    stack: CustomLlmdStack, *, epp_registry: str, epp_repository: str, epp_tag: str
) -> dict:
    """The values actually sent to ArgoCD: the user's ``helm_values`` with a thin
    base merged underneath, so the llm-d EPP image defaults apply even if the
    user's values.yaml omits them. The user's values always win.
    """
    base = {
        "inferenceExtension": {
            "image": {"registry": epp_registry, "repository": epp_repository, "tag": epp_tag}
        }
    }
    return deep_merge(base, stack.helm_values or {})
```

- [ ] **Step 4: Update `_values_for` in `api/llmd.py`**

`backend/app/api/llmd.py:85-87` — replace the `build_llmd_values(...)` call:

```python
    return build_llmd_values(
        stack,
        epp_registry=settings.llmd_epp_image_registry,
        epp_repository=settings.llmd_epp_image_repository,
        epp_tag=settings.llmd_epp_image_tag,
    )
```

- [ ] **Step 5: Run the full llmd suite**

Run: `cd backend && .venv/bin/python -m pytest tests/test_llmd.py -q`
Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/llmd_manifests.py backend/app/api/llmd.py backend/tests/test_llmd.py
git commit -m "feat(llmd): default llm-d EPP image under user helm_values"
```

---

### Task 6: Frontend — copy + EPP image on the detail page

The values.yaml editor already renders whatever `default_llmd_values` returns, so no editor change. Update copy to say "router," and show the EPP image (add it to the serialized stack).

**Files:**
- Modify: `backend/app/api/llmd.py:188-206` (`_serialize` — add `epp_image`)
- Modify: `frontend/src/types/index.ts` (LlmdStack type — add `epp_image`)
- Modify: `frontend/src/app/(app)/admin/llmd/[id]/page.tsx:200` (show EPP image)
- Modify: `frontend/messages/en.json`, `frontend/messages/ko.json` (copy)

**Interfaces:**
- Consumes: `settings.llmd_epp_image_registry/repository/tag`.
- Produces: serialized stack field `epp_image: str` (e.g. `"ghcr.io/llm-d/llm-d-router-endpoint-picker:v0.8.1"`).

- [ ] **Step 1: Add `epp_image` to `_serialize`**

In `backend/app/api/llmd.py`, inside `_serialize` (after the `chart_version` line ~199), add:

```python
        "epp_image": f"{settings.llmd_epp_image_registry}/{settings.llmd_epp_image_repository}:{settings.llmd_epp_image_tag}",
```

- [ ] **Step 2: Add a render-level test for `_serialize`**

Add to `backend/tests/test_llmd.py`:

```python
def test_serialize_includes_epp_image():
    from app.api.llmd import _serialize

    stack = _stack(
        argocd_connection_id=None, cluster_id=None, helm_values={"a": 1},
        created_at=None, updated_at=None,
    )
    out = _serialize(stack, {"sync_status": "Synced", "health_status": "Healthy", "status_message": None})
    assert out["epp_image"] == "ghcr.io/llm-d/llm-d-router-endpoint-picker:v0.8.1"
```

Run: `cd backend && .venv/bin/python -m pytest tests/test_llmd.py::test_serialize_includes_epp_image -q`
Expected: PASS (after Step 1). If `_serialize` touches attributes the `_stack` helper lacks, extend `_stack` defaults to include them (`name, target_model_name, namespace, argo_app_name, helm_values, created_by` are already present).

- [ ] **Step 3: Add `epp_image` to the frontend type**

In `frontend/src/types/index.ts`, find the `LlmdStack` interface (near `chart_version: string;`) and add:

```typescript
  epp_image: string;
```

- [ ] **Step 4: Show the EPP image on the detail page**

In `frontend/src/app/(app)/admin/llmd/[id]/page.tsx`, after line 200 (`<Field label={t("chart")} ...>`), add:

```tsx
            <Field label={t("eppImage")} mono>{stack.epp_image}</Field>
```

- [ ] **Step 5: Add copy keys**

In `frontend/messages/en.json` under the `adminLlmd` namespace, add `"eppImage": "Router image (EPP)"`. In `frontend/messages/ko.json`, add `"eppImage": "라우터 이미지 (EPP)"`. Update the `adminLlmd` page description copy in both to mention "Envoy + EPP standalone router" (find the existing `pageDescription`/`listHint` and reword to note it's the standalone llm-d router, not just an EPP).

- [ ] **Step 6: Typecheck the frontend**

Run: `docker exec litellm_frontend sh -lc 'cd /app && npx tsc --noEmit'` (or `npm run lint` if tsc unavailable)
Expected: no errors referencing `epp_image` or `eppImage`.

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/llmd.py backend/tests/test_llmd.py frontend/src/types/index.ts \
        "frontend/src/app/(app)/admin/llmd/[id]/page.tsx" frontend/messages/en.json frontend/messages/ko.json
git commit -m "feat(llmd): surface router EPP image + router-oriented copy"
```

---

### Task 7: Live verification on minikube `portal-test` (M1, no GPU)

No code. Prove the router stands up and routes to an existing serving deployment. Honest limitation: KV-aware scoring efficacy is NOT measurable without GPU/real vLLM — this verifies "router up + requests route."

**Files:** none (manual). Capture findings in the PR description.

- [ ] **Step 1: Rebuild backend image so the new settings/values ship**

```bash
docker compose up -d --build backend backend-worker
docker exec litellm_backend python -c "from app.config import settings; print(settings.llmd_epp_image_registry, settings.llmd_epp_image_repository, settings.llmd_epp_image_tag)"
```
Expected: `ghcr.io llm-d/llm-d-router-endpoint-picker v0.8.1`.

- [ ] **Step 2: Confirm an existing serving deployment is Ready with the model label**

```bash
kubectl --context portal-test get pods -A -l llm-ops/model-name --show-labels | head
```
Expected: at least one Running pod (e.g. `cpu-demo`) labelled `llm-ops/model-name=<model>` on port 8000.

- [ ] **Step 3: Create a router stack via the portal**

In the portal → admin/llmd → new: target the existing model, pick the ArgoCD connection, leave values.yaml as the prefilled default (now the real-router template). Save. Confirm the create returns 201 (no ArgoCD 4xx).

- [ ] **Step 4: Confirm the router pod comes up — and that llm-d's EPP accepts the chart's config**

```bash
kubectl --context portal-test -n <stack-namespace> get pods,svc
kubectl --context portal-test -n <stack-namespace> logs <router-pod> -c epp | tail -40
kubectl --context portal-test -n <stack-namespace> describe pod <router-pod> | grep -iE "image:|error|pull|crash|back-off"
```
Expected: router pod `Running` (CPU), epp container NOT crashlooping.
- **Key risk check (A1):** the EPP container is launched by the chart with `--config-file /config/default-plugins.yaml` (GIE's `EndpointPickerConfig`). Confirm llm-d's EPP image accepts those flags/config — i.e. the epp container reaches Ready and its logs show it loaded the plugin config without a fatal "unknown flag" / "unknown plugin type" error.
- **If the epp container crashloops on the config/flags:** llm-d's EPP is not config-compatible with this chart under A1. FALLBACK: revert the EPP image to vanilla GIE (`registry.k8s.io/gateway-api-inference-extension/epp:v1.5.0` via the stack's `helm_values`/settings) — still a cache-aware standalone router — and record that adopting llm-d's EPP binary needs A2 (custom config). Note this in the PR and stop for a decision.
- **If `ImagePullBackOff`:** the ghcr.io image isn't reachable from the cluster — `minikube -p portal-test image load ghcr.io/llm-d/llm-d-router-endpoint-picker:v0.8.1` (pull locally first) or record as a known local-env/air-gap gap.

- [ ] **Step 5: Route an inference request through the router**

```bash
kubectl --context portal-test -n <stack-namespace> port-forward svc/<router-svc> 8080:80 &
curl -s localhost:8080/v1/models | head
curl -s localhost:8080/v1/chat/completions -H 'content-type: application/json' \
  -d '{"model":"<model>","messages":[{"role":"user","content":"ping"}]}' | head
```
Expected: the request reaches the backend vLLM (mock returns a completion). A 200 with a body proves Envoy→EPP→backend routing works.

- [ ] **Step 6: Confirm the portal renders the router resources**

In admin/llmd/[id]: applied values + deployed resources list the router Deployment/Service; the EPP image field shows `ghcr.io/llm-d/llm-d-router-endpoint-picker:v0.8.1`; `live_error` is empty.

- [ ] **Step 7: Push the branch and open the PR**

```bash
git push -u origin feat/llmd-standalone-router
gh pr create --base main --title "feat(llmd): real llm-d standalone router (swap EPP image to llm-d's)" --body "<summary + Task 7 verification notes + honest GPU limitation + any A1 fallback outcome>"
```
If #174 is not yet merged, base on `feat/llmd-serving-management` instead, or merge #174 first and rebase (patch-id auto-drops stacked commits).

---

## Self-review

- **Spec coverage:** gating spike (§risk → Task 1, DONE); EPP image settings incl. its own ghcr.io registry (§components config → Task 3); EPP-image swap in the default template, sidecar+scorers from chart defaults per A1 (§components default_llmd_values → Task 4); base-merge EPP image under user values (§components build_llmd_values → Task 5); frontend copy + EPP image, no schema change (§components frontend → Task 6); no DB migration (honored — no task adds one); local M1 live verify incl. the A1 config-compat risk check + fallback and the honest KV-scoring limitation (§testing/data-flow → Task 7); air-gap overridability (§air-gap → settings in Task 3, registry merge in Task 5). Plus a found regression: stale #174 tests (Task 2).
- **A1 alignment:** Task 4 explicitly asserts NO `proxy`/`plugins` keys are emitted (the chart has no such values keys and already provides sidecar + cache-aware scorers). No Kustomize/multi-source is introduced anywhere.
- **Placeholder scan:** chart-facing keys are the spike-confirmed real ones (`inferenceExtension.image.{registry,repository,tag}`, `sidecar.*` default, `pluginsConfigFile`); all code steps show full code; all test steps show assertions; all run steps show commands + expected output.
- **Type consistency:** `default_llmd_values(target_model_name, *, epp_registry, epp_repository, epp_tag)` and `build_llmd_values(stack, *, epp_registry, epp_repository, epp_tag)` signatures match between Tasks 4/5, the `api/llmd.py` call sites, and the tests; the serialized `epp_image` field name matches the frontend `epp_image` type and the `eppImage` copy key; settings names `llmd_epp_image_registry/repository/tag` are identical across Tasks 3–6.
