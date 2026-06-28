# llm-d standalone router Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the portal's existing GIE-standalone "llm-d" stack into the real llm-d standalone router (Envoy proxy + llm-d EPP image + llm-d scorer plugins), reusing the existing `custom_llmd_stack` / ArgoCD / `helm_values` plumbing.

**Architecture:** We already deploy the `gateway-api-inference-extension` (GIE) `standalone` chart via an ArgoCD `Application` whose Helm `valuesObject` comes from `custom_llmd_stack.helm_values`. llm-d's EPP is "GIE EPP extended." So the change is a **values delta** — point the EPP image at llm-d's, enable the Envoy sidecar proxy, and supply llm-d's scheduler plugin config — not a new deployment path. Standalone mode (Envoy + EPP in one pod) means no Gateway API provider and no `llm-d-infra`.

**Tech Stack:** FastAPI + SQLAlchemy (backend), pydantic-settings, pytest/pytest-asyncio, ArgoCD + Helm (GIE `standalone` chart `v1.5.0`), Next.js + next-intl (frontend), minikube `portal-test` (M1, no GPU).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-06-28-llmd-standalone-router-design.md` — every task implicitly inherits it.
- **Branch:** `feat/llmd-standalone-router`, stacked on `feat/llmd-serving-management` (PR #174). Recommend merging #174 to `main` before/around implementation; this branch then rebases onto `main` (patch-id auto-drops the stacked commits, as with #172/#173).
- **No DB migration** — `helm_values` JSONB already holds arbitrary values; only the default template changes.
- **No frontend schema change** — copy + one display field only.
- Chart stays GIE `standalone` `v1.5.0` (`settings.llmd_chart_*` unchanged).
- Air-gap: all images/charts must be overridable to an internal registry (no hardcoded external pulls). EPP image is configured via settings, never literal in business logic.
- Backend tests: run in `backend/.venv` (Python ≥3.11, `pip install -e ".[dev]"`). Verified command form: `cd backend && .venv/bin/python -m pytest tests/test_llmd.py -q`.
- Container note: `litellm_backend` runtime image has NO pytest; use the venv for tests. Live deploy uses the running stack + minikube `portal-test`.

---

### Task 1: Gating spike — confirm the GIE `standalone` chart accepts our overrides

**Files:**
- Create: `docs/superpowers/plans/2026-06-28-llmd-spike-notes.md` (records the confirmed value keys; consumed by Tasks 3–4)

**Interfaces:**
- Produces: the exact Helm value keys for (a) overriding the EPP container image, (b) enabling the Envoy sidecar proxy, (c) supplying the scheduler/plugins config. Tasks 3–4 read these from the notes file.

This task writes NO application code. It is a go/no-go gate: if any of the three overrides is unsupported by the chart, STOP and switch to Approach B (deploy llm-d-router's own Kustomize/images as a separate ArgoCD source) — see the spec — before continuing.

- [ ] **Step 1: Pull and inspect the chart's values schema**

```bash
# Requires helm 3.14+. Render the chart we already deploy.
helm show values oci://registry.k8s.io/gateway-api-inference-extension/charts/standalone --version v1.5.0 > /tmp/standalone-values.yaml
helm show readme  oci://registry.k8s.io/gateway-api-inference-extension/charts/standalone --version v1.5.0 > /tmp/standalone-readme.md 2>/dev/null || true
```

Read `/tmp/standalone-values.yaml`. Locate the keys for: the EPP container image (registry/repository/tag), the sidecar proxy enable/type, and the plugins / scheduler-config (inline block or a mounted config file).

- [ ] **Step 2: Render with our candidate overrides**

```bash
cat > /tmp/router-values.yaml <<'YAML'
inferenceExtension:
  replicas: 1
  image:
    registry: registry.k8s.io
    repository: llm-d/llm-d-router-endpoint-picker   # llm-d EPP (adjust path to spike findings)
    tag: v0.8.1
  endpointsServer:
    createInferencePool: false
    endpointSelector: llm-ops/model-name=opt-125m
    targetPorts: 8000
    modelServerType: vllm
YAML
helm template router oci://registry.k8s.io/gateway-api-inference-extension/charts/standalone \
  --version v1.5.0 -f /tmp/router-values.yaml > /tmp/router-rendered.yaml 2>&1
echo "exit=$?"; grep -nE "image:|envoy|proxy|plugins|pluginsConfig|config" /tmp/router-rendered.yaml | head -40
```

Expected: a non-error render whose Deployment uses the overridden image, and which shows where the proxy + plugins config attach.

- [ ] **Step 3: Record findings and the go/no-go decision**

Write `docs/superpowers/plans/2026-06-28-llmd-spike-notes.md` with the CONFIRMED keys, e.g.:

```markdown
# llm-d standalone router — spike findings (2026-06-28)
- EPP image override key: inferenceExtension.image.{registry,repository,tag}  [confirmed | DIFFERENT: <actual>]
- Envoy sidecar proxy: <actual key + value, e.g. inferenceExtension.proxy.enabled: true>  [confirmed | UNSUPPORTED]
- Scheduler/plugins config: <actual key, inline vs file>  [confirmed | UNSUPPORTED]
- Decision: PROCEED with Approach A  |  FALL BACK to Approach B (reason: ...)
```

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/plans/2026-06-28-llmd-spike-notes.md
git commit -m "docs(llmd): spike findings — GIE standalone chart override keys + A/B decision"
```

> If the decision is FALL BACK to B, stop here and re-enter brainstorming for the B design. Tasks 2–7 below assume PROCEED with A and use the key names recorded in the notes file (the candidates below are the expected names).

---

### Task 2: Repair stale `test_llmd.py` to the #174 `helm_values` reality (baseline green)

PR #174 refactored `CustomLlmdStack` to a single `helm_values` JSONB and moved schema generation into `default_llmd_values()`, but did not update `tests/test_llmd.py`. Three tests fail today. Get them green against current code BEFORE changing behavior.

**Files:**
- Modify: `backend/tests/test_llmd.py:15-24` (`test_model_has_expected_columns`)
- Modify: `backend/tests/test_llmd.py:34-42` (`_stack` helper)
- Modify: `backend/tests/test_llmd.py:56-80` (`test_build_values_*`)

**Interfaces:**
- Consumes: `CustomLlmdStack` (columns: `id, name, target_model_name, argocd_connection_id, cluster_id, namespace, argo_app_name, helm_values, values_snapshot, created_by, updated_by, created_at, updated_at`); `build_llmd_values(stack, *, image_registry)` returns `deep_merge({"inferenceExtension": {"image": {"registry": image_registry}}}, stack.helm_values or {})`; `default_llmd_values(target_model_name, *, image_registry)`.

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
- Produces: `settings.llmd_epp_image: str` (default `"llm-d/llm-d-router-endpoint-picker"`) and `settings.llmd_epp_image_tag: str` (default `"v0.8.1"`). Consumed by Task 4. Use the exact repository/tag confirmed in the Task 1 spike notes.

- [ ] **Step 1: Write the failing test**

Extend `test_llmd_settings_target_standalone_chart` in `backend/tests/test_llmd.py`:

```python
def test_llmd_settings_target_standalone_chart():
    assert settings.argo_project == "llm-d"
    assert settings.llmd_chart_name == "standalone"
    assert settings.llmd_chart_version == "v1.5.0"
    assert "gateway-api-inference-extension" in settings.llmd_chart_repo
    # Real llm-d EPP image (overridable for air-gap).
    assert settings.llmd_epp_image == "llm-d/llm-d-router-endpoint-picker"
    assert settings.llmd_epp_image_tag == "v0.8.1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_llmd.py::test_llmd_settings_target_standalone_chart -q`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'llmd_epp_image'`.

- [ ] **Step 3: Add the settings**

In `backend/app/config.py`, after line 71 (`llmd_image_registry: str = "registry.k8s.io"`), add:

```python
    # llm-d router EPP image — GIE EPP extended with llm-d's KV/prefix/load-aware
    # scorers. Air-gap: mirror and override registry + repo + tag.
    llmd_epp_image: str = "llm-d/llm-d-router-endpoint-picker"
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

### Task 4: Real-router `default_llmd_values()` — EPP image + Envoy sidecar + scorer plugins

**Files:**
- Modify: `backend/app/services/llmd_manifests.py:40-61` (`default_llmd_values`)
- Modify: `backend/app/api/llmd.py:338-340, 374` (pass the new EPP image args)
- Modify: `backend/tests/test_llmd.py` (`test_default_values_*`)

**Interfaces:**
- Consumes: `settings.llmd_epp_image`, `settings.llmd_epp_image_tag` (Task 3); `LABEL_MODEL` (`"llm-ops/model-name"`); the confirmed proxy/plugins keys from Task 1 notes.
- Produces: `default_llmd_values(target_model_name, *, image_registry, epp_image, epp_image_tag) -> dict`. Consumed by `api/llmd.py` create + default-values endpoints.

> The proxy/plugins keys below are the EXPECTED chart keys. If Task 1's spike notes recorded different names, substitute them here and in the test — the notes file is authoritative for chart-facing names.

- [ ] **Step 1: Write the failing test**

Replace `test_default_values_standalone_schema_and_default_selector` in `backend/tests/test_llmd.py`:

```python
def test_default_values_is_real_router_template():
    v = default_llmd_values(
        "opt-125m", image_registry="reg.local",
        epp_image="llm-d/llm-d-router-endpoint-picker", epp_image_tag="v0.8.1",
    )
    ie = v["inferenceExtension"]
    # llm-d EPP image (not vanilla GIE)
    assert ie["image"]["registry"] == "reg.local"
    assert ie["image"]["repository"] == "llm-d/llm-d-router-endpoint-picker"
    assert ie["image"]["tag"] == "v0.8.1"
    # Envoy sidecar proxy on (standalone router)
    assert ie["proxy"]["enabled"] is True
    assert ie["proxy"]["type"] == "envoy"
    # llm-d scorer plugins present
    assert "plugins" in ie
    names = {p["type"] for p in ie["plugins"]["scorers"]}
    assert {"prefix-cache", "kv-cache", "load-aware"} <= names
    # Backend selection preserved
    es = ie["endpointsServer"]
    assert es["endpointSelector"] == "llm-ops/model-name=opt-125m"
    assert es["targetPorts"] == 8000
    assert es["modelServerType"] == "vllm"


def test_default_values_blank_model_yields_empty_selector():
    v = default_llmd_values(
        "", image_registry="reg.local",
        epp_image="llm-d/llm-d-router-endpoint-picker", epp_image_tag="v0.8.1",
    )
    assert v["inferenceExtension"]["endpointsServer"]["endpointSelector"] == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_llmd.py::test_default_values_is_real_router_template -q`
Expected: FAIL — `TypeError: default_llmd_values() got an unexpected keyword argument 'epp_image'`.

- [ ] **Step 3: Rewrite `default_llmd_values`**

Replace `backend/app/services/llmd_manifests.py:40-61` with:

```python
def default_llmd_values(
    target_model_name: str, *, image_registry: str, epp_image: str, epp_image_tag: str
) -> dict:
    """The starter ``values.yaml`` for a new stack: the llm-d **standalone router**.

    Deploys an Envoy proxy + the llm-d EPP (GIE EPP extended with llm-d's
    KV/prefix/load-aware scorers) co-located in one pod, in front of
    already-running model servers selected by ``endpointSelector`` on
    ``targetPorts``. No Gateway API provider required. The user edits this freely.
    """
    return {
        "inferenceExtension": {
            "replicas": 1,
            "image": {"registry": image_registry, "repository": epp_image, "tag": epp_image_tag},
            "endpointsServer": {
                "createInferencePool": False,
                "endpointSelector": f"{LABEL_MODEL}={target_model_name}" if target_model_name else "",
                "targetPorts": 8000,
                "modelServerType": "vllm",
            },
            # Standalone mode: self-managed Envoy proxy alongside the EPP.
            "proxy": {"enabled": True, "type": "envoy"},
            # llm-d scheduler scorers (the routing intelligence over vanilla GIE).
            "plugins": {
                "scorers": [
                    {"type": "prefix-cache"},
                    {"type": "kv-cache"},
                    {"type": "load-aware"},
                ]
            },
        },
    }
```

- [ ] **Step 4: Update the two call sites in `api/llmd.py`**

`backend/app/api/llmd.py:338-340` (create) — replace the `default_llmd_values(...)` call:

```python
    helm_values = _parse_values_yaml(body.values_yaml) or default_llmd_values(
        body.target_model_name,
        image_registry=settings.llmd_image_registry,
        epp_image=settings.llmd_epp_image,
        epp_image_tag=settings.llmd_epp_image_tag,
    )
```

`backend/app/api/llmd.py:374` (default-values endpoint) — replace:

```python
    values = default_llmd_values(
        body.target_model_name,
        image_registry=settings.llmd_image_registry,
        epp_image=settings.llmd_epp_image,
        epp_image_tag=settings.llmd_epp_image_tag,
    )
```

- [ ] **Step 5: Run the full llmd suite**

Run: `cd backend && .venv/bin/python -m pytest tests/test_llmd.py -q`
Expected: all passed (was 10; now 11 with the added blank-model test).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/llmd_manifests.py backend/app/api/llmd.py backend/tests/test_llmd.py
git commit -m "feat(llmd): real standalone-router default values (envoy + llm-d EPP + scorers)"
```

---

### Task 5: Default the EPP image under user `helm_values` in `build_llmd_values()`

So a stored stack whose `helm_values` omits the image still gets the llm-d EPP image (air-gap registry already handled). User values always win.

**Files:**
- Modify: `backend/app/services/llmd_manifests.py:64-70` (`build_llmd_values`)
- Modify: `backend/app/api/llmd.py:85-87` (`_values_for` — pass EPP image)
- Modify: `backend/tests/test_llmd.py` (extend build-values tests)

**Interfaces:**
- Produces: `build_llmd_values(stack, *, image_registry, epp_image, epp_image_tag) -> dict` — base is `{"inferenceExtension": {"image": {"registry": image_registry, "repository": epp_image, "tag": epp_image_tag}}}` deep-merged UNDER `stack.helm_values`.

- [ ] **Step 1: Write the failing test**

Update the build-values tests in `backend/tests/test_llmd.py`:

```python
def test_build_values_merges_epp_image_base_under_helm_values():
    v = build_llmd_values(
        _stack(), image_registry="reg.local",
        epp_image="llm-d/llm-d-router-endpoint-picker", epp_image_tag="v0.8.1",
    )
    assert v["inferenceExtension"]["image"] == {
        "registry": "reg.local", "repository": "llm-d/llm-d-router-endpoint-picker", "tag": "v0.8.1",
    }


def test_build_values_user_helm_values_win_over_base():
    v = build_llmd_values(
        _stack(helm_values={"inferenceExtension": {"image": {"tag": "custom"}}, "tracing": {"enabled": True}}),
        image_registry="reg.local",
        epp_image="llm-d/llm-d-router-endpoint-picker", epp_image_tag="v0.8.1",
    )
    img = v["inferenceExtension"]["image"]
    assert img["registry"] == "reg.local"
    assert img["repository"] == "llm-d/llm-d-router-endpoint-picker"
    assert img["tag"] == "custom"           # user wins
    assert v["tracing"] == {"enabled": True}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_llmd.py::test_build_values_merges_epp_image_base_under_helm_values -q`
Expected: FAIL — `TypeError: build_llmd_values() got an unexpected keyword argument 'epp_image'`.

- [ ] **Step 3: Update `build_llmd_values`**

Replace `backend/app/services/llmd_manifests.py:64-70`:

```python
def build_llmd_values(
    stack: CustomLlmdStack, *, image_registry: str, epp_image: str, epp_image_tag: str
) -> dict:
    """The values actually sent to ArgoCD: the user's ``helm_values`` with a thin
    base merged underneath, so the (air-gapped) image defaults apply even if the
    user's values.yaml omits them. The user's values always win.
    """
    base = {
        "inferenceExtension": {
            "image": {"registry": image_registry, "repository": epp_image, "tag": epp_image_tag}
        }
    }
    return deep_merge(base, stack.helm_values or {})
```

- [ ] **Step 4: Update `_values_for` in `api/llmd.py`**

`backend/app/api/llmd.py:85-87` — replace the `build_llmd_values(...)` call:

```python
    return build_llmd_values(
        stack,
        image_registry=settings.llmd_image_registry,
        epp_image=settings.llmd_epp_image,
        epp_image_tag=settings.llmd_epp_image_tag,
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

The values.yaml editor already renders whatever `default_llmd_values` returns, so no editor change. Update labels to say "router," and show the EPP image (already returned in the serialized stack? No — add it).

**Files:**
- Modify: `backend/app/api/llmd.py:188-206` (`_serialize` — add `epp_image`)
- Modify: `frontend/src/types/index.ts` (LlmdStack type — add `epp_image`)
- Modify: `frontend/src/app/(app)/admin/llmd/[id]/page.tsx:200` (show EPP image)
- Modify: `frontend/messages/en.json`, `frontend/messages/ko.json` (copy)

**Interfaces:**
- Consumes: `settings.llmd_epp_image`, `settings.llmd_epp_image_tag`.
- Produces: serialized stack field `epp_image: str` (e.g. `"registry.k8s.io/llm-d/llm-d-router-endpoint-picker:v0.8.1"`).

- [ ] **Step 1: Add `epp_image` to `_serialize`**

In `backend/app/api/llmd.py`, inside `_serialize` (after the `chart_version` line ~199), add:

```python
        "epp_image": f"{settings.llmd_image_registry}/{settings.llmd_epp_image}:{settings.llmd_epp_image_tag}",
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
    assert out["epp_image"].endswith("/llm-d/llm-d-router-endpoint-picker:v0.8.1")
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

- [ ] **Step 6: Typecheck + build the frontend**

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
docker exec litellm_backend python -c "from app.config import settings; print(settings.llmd_epp_image, settings.llmd_epp_image_tag)"
```
Expected: `llm-d/llm-d-router-endpoint-picker v0.8.1`.

- [ ] **Step 2: Confirm an existing serving deployment is Ready with the model label**

```bash
kubectl --context portal-test get pods -A -l llm-ops/model-name --show-labels | head
```
Expected: at least one Running pod (e.g. `cpu-demo`) labelled `llm-ops/model-name=<model>` on port 8000.

- [ ] **Step 3: Create a router stack via the portal**

In the portal → admin/llmd → new: target the existing model, pick the ArgoCD connection, leave values.yaml as the prefilled default (now the real-router template). Save. Confirm the create returns 201 (no ArgoCD 4xx).

- [ ] **Step 4: Confirm the router pod comes up**

```bash
kubectl --context portal-test -n <stack-namespace> get pods,svc
kubectl --context portal-test -n <stack-namespace> describe pod <router-pod> | grep -iE "image:|envoy|epp|error|pull"
```
Expected: router pod `Running` (CPU). If `ImagePullBackOff`, the EPP image isn't reachable from the cluster — note it (air-gap mirror needed) and either load the image into minikube (`minikube -p portal-test image load ...`) or record as a known local-env gap.

- [ ] **Step 5: Route an inference request through the router**

```bash
kubectl --context portal-test -n <stack-namespace> port-forward svc/<router-svc> 8080:80 &
curl -s localhost:8080/v1/models | head
curl -s localhost:8080/v1/chat/completions -H 'content-type: application/json' \
  -d '{"model":"<model>","messages":[{"role":"user","content":"ping"}]}' | head
```
Expected: the request reaches the backend vLLM (mock returns a completion). A 200 with a body proves Envoy→EPP→backend routing works.

- [ ] **Step 6: Confirm the portal renders the router resources**

In admin/llmd/[id]: applied values + deployed resources list the router Deployment/Service; the EPP image field shows the configured image; `live_error` is empty.

- [ ] **Step 7: Push the branch and open the PR**

```bash
git push -u origin feat/llmd-standalone-router
gh pr create --base main --title "feat(llmd): real llm-d standalone router (Envoy + llm-d EPP + scorers)" --body "<summary + Task 7 verification notes + honest GPU limitation>"
```
If #174 is not yet merged, base on `feat/llmd-serving-management` instead, or merge #174 first and rebase (patch-id auto-drops stacked commits).

---

## Self-review

- **Spec coverage:** gating spike (§risk → Task 1); EPP image settings (§components config → Task 3); real-router default template incl. Envoy sidecar + scorers (§components default_llmd_values → Task 4); base-merge EPP image (§components build_llmd_values → Task 5); frontend copy + EPP image, no schema change (§components frontend → Task 6); no DB migration (honored — no task adds one); local M1 live verify incl. honest KV-scoring limitation (§testing/data-flow → Task 7); air-gap overridability (§air-gap → settings in Task 3, registry merge in Task 5). Plus a found regression: stale #174 tests (Task 2).
- **Placeholder scan:** chart-facing proxy/plugins key names are explicitly sourced from Task 1's spike notes (a real task-to-task dependency, not a TBD); all code steps show full code; all test steps show assertions; all run steps show commands + expected output.
- **Type consistency:** `default_llmd_values(target_model_name, *, image_registry, epp_image, epp_image_tag)` and `build_llmd_values(stack, *, image_registry, epp_image, epp_image_tag)` signatures are used identically in Tasks 4/5 and at the `api/llmd.py` call sites; `epp_image` serialized field name matches the frontend `epp_image` type and `eppImage` copy key.
