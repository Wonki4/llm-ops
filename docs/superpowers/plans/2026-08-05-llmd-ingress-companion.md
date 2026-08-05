# llm-d Ingress Companion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every llm-d router stack the portal deploys also gets an Ingress (created/updated/deleted by the portal) fronting the router's Envoy entry Service.

**Architecture:** The portal renders a pure Ingress manifest and upserts it to the *target* cluster (via `k8s_for_cluster`, the same client the model-deployment path uses) right after applying the ArgoCD Application, and deletes it with the stack. Ingress config is global (env-overridable), never per-stack; the ingress domain is enforced non-empty so each stack's host `{argo_app_name}.{domain}` is unique.

**Tech Stack:** FastAPI, SQLAlchemy async, `kubernetes_asyncio`, pydantic-settings; pytest.

Spec: `docs/superpowers/specs/2026-08-05-llmd-ingress-companion-design.md`.

**Branch base:** `origin/main` (it already contains the #216 router.* migration `6a3cc00`, the only dependency). Do **not** stack on the open proxy-image branch `feat/llmd-proxy-image-override` — it sits on the already-merged #216 and would strand this work. `origin/main` has **no** `llmd_proxy_image` setting; the placement anchors below are given for `origin/main`.

## Global Constraints

- **Domain is enforced non-empty.** `settings.effective_ingress_domain` returns `settings.llmd_ingress_domain.strip()` or the literal default `"llm-d.local"`. There is no host-less Ingress mode.
- **Host is always** `f"{stack.argo_app_name}.{domain}"`.
- **Backend target** is Service `f"{stack.argo_app_name}-epp"`, port **name** `"http"` (the chart's sidecar-mode entry, 8081). Never a port number.
- **Ingress name** is `f"{stack.argo_app_name}-ingress"`; **namespace** is `stack.namespace`; **label** `app.kubernetes.io/managed-by: litellm-portal` (the module constant `MANAGED_BY`).
- **`ingress_class` empty → omit the `ingressClassName` key entirely** (do not emit `ingressClassName: ""`).
- **Reuse, don't re-implement:** upsert via `K8sClient.create_or_patch(ns, [manifest])`; delete via `K8sClient.delete(ns, {"ingress": name})` (it skips `None` entries and treats HTTP 404 as success). The target-cluster client is `await k8s_for_cluster(db, stack.cluster_id)`.
- **Config prefix** is `APP_` (pydantic `env_prefix`), so the env vars are `APP_LLMD_INGRESS_CLASS`, `APP_LLMD_INGRESS_DOMAIN`, `APP_LLMD_INGRESS_PATH`.
- Baseline: the backend test suite has ~21 pre-existing failures unrelated to this work (conftest doesn't mock `get_litellm_db`). Gate on **0 new failures vs `origin/main`**, comparing the failing-test set — not an absolute count.

---

### Task 1: Config — ingress settings + enforced-domain property

**Files:**
- Modify: `backend/app/config.py` (add three fields after `llmd_epp_image_tag` line 77 / before `argo_project` line 78; add `effective_ingress_domain` property after the `keycloak_issuer` property, ~line 89)
- Test: `backend/tests/test_config.py` (create)

**Interfaces:**
- Produces: `settings.llmd_ingress_class: str`, `settings.llmd_ingress_domain: str`, `settings.llmd_ingress_path: str`, and `Settings.effective_ingress_domain -> str`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_config.py`:

```python
from app.config import Settings


def test_ingress_defaults():
    s = Settings()
    assert s.llmd_ingress_class == ""
    assert s.llmd_ingress_domain == "llm-d.local"
    assert s.llmd_ingress_path == "/"


def test_effective_ingress_domain_falls_back_when_blank():
    # Operator sets APP_LLMD_INGRESS_DOMAIN="" (or whitespace) -> treated as unset.
    assert Settings(llmd_ingress_domain="").effective_ingress_domain == "llm-d.local"
    assert Settings(llmd_ingress_domain="   ").effective_ingress_domain == "llm-d.local"


def test_effective_ingress_domain_uses_override():
    assert Settings(llmd_ingress_domain="ai.corp.internal").effective_ingress_domain == "ai.corp.internal"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_config.py -q`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'llmd_ingress_class'`.

- [ ] **Step 3: Add the config fields**

In `backend/app/config.py`, immediately after `llmd_epp_image_tag: str = "v0.9.0"` (line 77) and before `argo_project: str = "llm-d"` (line 78), add:

```python
    # llm-d router Ingress. The portal always creates one Ingress per stack and
    # manages its lifecycle directly (not via ArgoCD). Air-gap/ops override
    # these globally; there are no per-stack ingress fields.
    llmd_ingress_class: str = ""  # APP_LLMD_INGRESS_CLASS; empty -> cluster's default IngressClass
    # APP_LLMD_INGRESS_DOMAIN; host = "{argo_app_name}.{domain}". Enforced non-empty
    # via effective_ingress_domain so multi-stack hosts never collide.
    llmd_ingress_domain: str = "llm-d.local"
    llmd_ingress_path: str = "/"  # APP_LLMD_INGRESS_PATH; pathType Prefix
```

- [ ] **Step 4: Add the enforced-domain property**

In `backend/app/config.py`, add this property alongside the other `@property` methods (right after the `keycloak_issuer` property, ~line 89):

```python
    @property
    def effective_ingress_domain(self) -> str:
        """The llm-d ingress domain, enforced non-empty (blank -> default)."""
        return self.llmd_ingress_domain.strip() or "llm-d.local"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_config.py -q`
Expected: PASS (3 passed).

- [ ] **Step 6: Lint**

Run: `cd backend && ruff check app/config.py tests/test_config.py`
Expected: no new errors on these files.

- [ ] **Step 7: Commit**

```bash
git add backend/app/config.py backend/tests/test_config.py
git commit -m "feat(llmd): add ingress config with enforced non-empty domain"
```

---

### Task 2: Manifest builder — `build_llmd_ingress` + service name + releaseName

**Files:**
- Modify: `backend/app/services/llmd_manifests.py` (add `llmd_service_name` + `build_llmd_ingress`; add `releaseName` in `build_argo_application`)
- Test: `backend/tests/test_llmd_manifests.py` (add cases)

**Interfaces:**
- Consumes: module constant `MANAGED_BY` (already defined = `"litellm-portal"`); `stack.argo_app_name`, `stack.namespace`.
- Produces:
  - `llmd_service_name(stack) -> str` returning `f"{stack.argo_app_name}-epp"`.
  - `build_llmd_ingress(stack, *, ingress_class: str, ingress_domain: str, ingress_path: str) -> dict`.
  - `build_argo_application(...)` now emits `spec.source.helm.releaseName == stack.argo_app_name`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_llmd_manifests.py` (the `_stack()` helper there already sets `argo_app_name="llmd-my-stack"`, `namespace="llmd-my-stack"`):

```python
from app.services.llmd_manifests import build_llmd_ingress, llmd_service_name


def test_llmd_service_name_is_release_epp():
    assert llmd_service_name(_stack()) == "llmd-my-stack-epp"


def test_build_ingress_shape_and_backend():
    ing = build_llmd_ingress(
        _stack(), ingress_class="nginx", ingress_domain="ai.corp.internal", ingress_path="/"
    )
    assert ing["apiVersion"] == "networking.k8s.io/v1"
    assert ing["kind"] == "Ingress"
    assert ing["metadata"]["name"] == "llmd-my-stack-ingress"
    assert ing["metadata"]["namespace"] == "llmd-my-stack"
    assert ing["metadata"]["labels"]["app.kubernetes.io/managed-by"] == "litellm-portal"
    rule = ing["spec"]["rules"][0]
    assert rule["host"] == "llmd-my-stack.ai.corp.internal"
    path = rule["http"]["paths"][0]
    assert path["path"] == "/"
    assert path["pathType"] == "Prefix"
    assert path["backend"]["service"]["name"] == "llmd-my-stack-epp"
    assert path["backend"]["service"]["port"] == {"name": "http"}
    assert ing["spec"]["ingressClassName"] == "nginx"


def test_build_ingress_omits_class_when_empty():
    ing = build_llmd_ingress(
        _stack(), ingress_class="", ingress_domain="llm-d.local", ingress_path="/"
    )
    assert "ingressClassName" not in ing["spec"]
    assert ing["spec"]["rules"][0]["host"] == "llmd-my-stack.llm-d.local"


def test_build_ingress_respects_path():
    ing = build_llmd_ingress(
        _stack(), ingress_class="nginx", ingress_domain="llm-d.local", ingress_path="/router"
    )
    assert ing["spec"]["rules"][0]["http"]["paths"][0]["path"] == "/router"
```

Also add a `releaseName` assertion to the existing application test. In `test_build_application_is_isolated_to_project_and_namespace` (after the `src = app["spec"]["source"]` line), add:

```python
    assert src["helm"]["releaseName"] == "llmd-my-stack"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_llmd_manifests.py -q`
Expected: FAIL — `ImportError: cannot import name 'build_llmd_ingress'` (and, once import resolves, the `releaseName` KeyError).

- [ ] **Step 3: Add the service-name + ingress builders**

In `backend/app/services/llmd_manifests.py`, add after `selector_to_match_labels` (before `default_llmd_values`, ~line 55):

```python
def llmd_service_name(stack: CustomLlmdStack) -> str:
    """The router's entry Service in sidecar mode: ``<release>-epp``.

    ArgoCD's Helm release name is the Application name (``argo_app_name``), which
    build_argo_application pins explicitly, so this name is deterministic.
    """
    return f"{stack.argo_app_name}-epp"


def build_llmd_ingress(
    stack: CustomLlmdStack,
    *,
    ingress_class: str,
    ingress_domain: str,
    ingress_path: str,
) -> dict:
    """An Ingress fronting the llm-d router's Envoy entry Service.

    Backend: Service ``{argo_app_name}-epp`` port name ``http`` (chart sidecar
    entry, 8081). Host is always ``{argo_app_name}.{ingress_domain}`` so multiple
    stacks never collide. ``ingress_class`` empty omits ``ingressClassName`` (the
    cluster's default IngressClass is used). Managed by the portal, not ArgoCD.
    """
    spec: dict = {
        "rules": [
            {
                "host": f"{stack.argo_app_name}.{ingress_domain}",
                "http": {
                    "paths": [
                        {
                            "path": ingress_path,
                            "pathType": "Prefix",
                            "backend": {
                                "service": {
                                    "name": llmd_service_name(stack),
                                    "port": {"name": "http"},
                                }
                            },
                        }
                    ]
                },
            }
        ],
    }
    if ingress_class:
        spec["ingressClassName"] = ingress_class
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "Ingress",
        "metadata": {
            "name": f"{stack.argo_app_name}-ingress",
            "namespace": stack.namespace,
            "labels": {"app.kubernetes.io/managed-by": MANAGED_BY},
        },
        "spec": spec,
    }
```

- [ ] **Step 4: Pin the Helm release name**

In `backend/app/services/llmd_manifests.py`, inside `build_argo_application`, change the `helm` value in the `source` dict from:

```python
                "helm": {"valuesObject": values},
```
to:
```python
                "helm": {"releaseName": stack.argo_app_name, "valuesObject": values},
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_llmd_manifests.py -q`
Expected: PASS (all — the new ingress tests, the releaseName assertion, and the pre-existing tests).

- [ ] **Step 6: Lint**

Run: `cd backend && ruff check app/services/llmd_manifests.py tests/test_llmd_manifests.py`
Expected: no new errors on these files.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/llmd_manifests.py backend/tests/test_llmd_manifests.py
git commit -m "feat(llmd): build_llmd_ingress + pin helm releaseName for deterministic svc"
```

---

### Task 3: Lifecycle wiring in the API (create/update upsert, delete cleanup, serializer)

**Files:**
- Modify: `backend/app/api/llmd.py` (import; `_ingress_for` helper; `create_stack`, `update_stack`, `delete_stack`; `_serialize`)
- Test: `backend/tests/test_llmd.py` (patch existing create tests; add ingress upsert + delete tests)

**Interfaces:**
- Consumes: `build_llmd_ingress`, `llmd_service_name` (Task 2); `settings.llmd_ingress_class`, `settings.effective_ingress_domain`, `settings.llmd_ingress_path` (Task 1); existing `k8s_for_cluster(db, cluster_id) -> K8sClient` from `app.services.clusters`.
- Produces: `_ingress_for(stack) -> dict`; `_serialize(...)` gains an `"ingress_host"` key.

- [ ] **Step 1: Write/patch the failing tests**

In `backend/tests/test_llmd.py`:

**(a)** Update the import at the top of the affected `patch(...)` blocks — the existing create tests must also stub `k8s_for_cluster`. Rewrite `test_create_stack_applies_application` to:

```python
async def test_create_stack_applies_application(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_none_result())
    fake_k8s = MagicMock()
    fake_k8s.apply_application = AsyncMock()
    fake_k8s.get_application = AsyncMock(return_value=None)
    fake_target = MagicMock()
    fake_target.create_or_patch = AsyncMock()
    with patch(
        "app.api.llmd.argocd_placement_for",
        AsyncMock(return_value=(fake_k8s, "argocd", "https://kubernetes.default.svc")),
    ), patch("app.api.llmd.k8s_for_cluster", AsyncMock(return_value=fake_target)):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/admin/llmd-stacks", json={
                "name": "demo", "target_model_name": "qwen", "cluster_id": None,
                "namespace": "team-a", "values_yaml": "",
            })
    assert resp.status_code == 201
    fake_k8s.apply_application.assert_awaited_once()
    ns, manifest = fake_k8s.apply_application.await_args.args
    assert ns == "argocd"
    assert manifest["metadata"]["namespace"] == "argocd"
    assert manifest["metadata"]["name"] == "llmd-demo"
    # The Ingress is upserted to the target cluster in the stack's namespace.
    fake_target.create_or_patch.assert_awaited_once()
    ing_ns, manifests = fake_target.create_or_patch.await_args.args
    assert ing_ns == "team-a"
    ing = manifests[0]
    assert ing["kind"] == "Ingress"
    assert ing["metadata"]["name"] == "llmd-demo-ingress"
    assert ing["spec"]["rules"][0]["host"] == "llmd-demo.llm-d.local"
    assert ing["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"]["name"] == "llmd-demo-epp"
```

**(b)** In `test_create_stack_destination_server_from_placement`, add the same `k8s_for_cluster` patch so it doesn't hit real K8s. Change its `with patch(...)` to:

```python
    fake_target = MagicMock()
    fake_target.create_or_patch = AsyncMock()
    with patch(
        "app.api.llmd.argocd_placement_for",
        AsyncMock(return_value=(fake_k8s, "argo-central", "https://gpu-cluster:6443")),
    ), patch("app.api.llmd.k8s_for_cluster", AsyncMock(return_value=fake_target)):
```

**(c)** `test_create_stack_argocd_rbac_denied_502` raises inside `apply_application` before the ingress step, so add the patch defensively too. Change its `with patch(...)` to:

```python
    with patch(
        "app.api.llmd.argocd_placement_for",
        AsyncMock(return_value=(fake_k8s, "argocd", "https://kubernetes.default.svc")),
    ), patch("app.api.llmd.k8s_for_cluster", AsyncMock(return_value=MagicMock())):
```

**(d)** Add a delete test. Append to `backend/tests/test_llmd.py`:

```python
def _result_with(stack):
    r = MagicMock()
    r.scalar_one_or_none.return_value = stack
    return r


async def test_delete_stack_removes_ingress(client_for_user, super_user, mock_db):
    stack = _stack(name="demo", namespace="team-a", argo_app_name="llmd-demo", cluster_id=None)
    mock_db.execute = AsyncMock(return_value=_result_with(stack))
    fake_k8s = MagicMock()
    fake_k8s.delete_application = AsyncMock()
    fake_target = MagicMock()
    fake_target.delete = AsyncMock()
    with patch(
        "app.api.llmd.argocd_placement_for",
        AsyncMock(return_value=(fake_k8s, "argocd", "https://kubernetes.default.svc")),
    ), patch("app.api.llmd.k8s_for_cluster", AsyncMock(return_value=fake_target)):
        async with client_for_user(super_user) as client:
            resp = await client.delete(f"/api/admin/llmd-stacks/{stack.id}")
    assert resp.status_code == 200
    fake_k8s.delete_application.assert_awaited_once()
    fake_target.delete.assert_awaited_once_with("team-a", {"ingress": "llmd-demo-ingress"})
```

**(e)** Add a serializer field test. Append:

```python
def test_serialize_reports_ingress_host():
    over = _serialize(_stack(argo_app_name="llmd-demo"), {"sync_status": "Synced"})
    assert over["ingress_host"] == "llmd-demo.llm-d.local"
```

Note: `_serialize` is already imported at the top of `test_llmd.py`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_llmd.py -q`
Expected: FAIL — the create test's `fake_target.create_or_patch.assert_awaited_once()` fails (not called yet), `test_delete_stack_removes_ingress` fails (`delete` not called), `test_serialize_reports_ingress_host` fails (`KeyError: 'ingress_host'`).

- [ ] **Step 3: Import `k8s_for_cluster` and add the `_ingress_for` helper**

In `backend/app/api/llmd.py`, change the clusters import (line 26) from:

```python
from app.services.clusters import argocd_placement_for
```
to:
```python
from app.services.clusters import argocd_placement_for, k8s_for_cluster
```

Add `build_llmd_ingress` to the `llmd_manifests` import block (lines 27-32):

```python
from app.services.llmd_manifests import (
    argo_app_name_for,
    build_argo_application,
    build_llmd_ingress,
    build_llmd_values,
    default_llmd_values,
)
```

Add the helper immediately after the `_values_for` function (`_values_for` ends ~line 133; on `origin/main` it does not take a `proxy_image` arg):

```python
def _ingress_for(stack: CustomLlmdStack) -> dict:
    return build_llmd_ingress(
        stack,
        ingress_class=settings.llmd_ingress_class,
        ingress_domain=settings.effective_ingress_domain,
        ingress_path=settings.llmd_ingress_path or "/",
    )
```

- [ ] **Step 4: Upsert the Ingress on create**

In `create_stack`, replace the apply `try` block (~lines 321-326 on `origin/main`; match by content) with:

```python
    try:
        k8s, argocd_ns, dest_server = await argocd_placement_for(db, stack.cluster_id)
        await k8s.apply_application(argocd_ns, _application_for(stack, argocd_ns, dest_server))
        target_k8s = await k8s_for_cluster(db, stack.cluster_id)
        await target_k8s.create_or_patch(stack.namespace, [_ingress_for(stack)])
    except Exception as e:
        logger.exception("ArgoCD Application apply failed for stack %s", stack.name)
        raise HTTPException(status_code=502, detail=f"ArgoCD apply failed: {_k8s_error_message(e)}")
```

- [ ] **Step 5: Upsert the Ingress on update**

In `update_stack`, replace the apply `try` block (~lines 375-380 on `origin/main`; match by content) with:

```python
    try:
        k8s, argocd_ns, dest_server = await argocd_placement_for(db, stack.cluster_id)
        await k8s.apply_application(argocd_ns, _application_for(stack, argocd_ns, dest_server))
        target_k8s = await k8s_for_cluster(db, stack.cluster_id)
        await target_k8s.create_or_patch(stack.namespace, [_ingress_for(stack)])
    except Exception as e:
        logger.exception("ArgoCD Application update failed for stack %s", stack.name)
        raise HTTPException(status_code=502, detail=f"ArgoCD update failed: {_k8s_error_message(e)}")
```

- [ ] **Step 6: Best-effort delete the Ingress on delete**

In `delete_stack`, replace the delete `try` block (~lines 397-402 on `origin/main`; match by content) with:

```python
    try:
        k8s, argocd_ns, _dest = await argocd_placement_for(db, stack.cluster_id)
        await k8s.delete_application(argocd_ns, stack.argo_app_name)
    except Exception as e:
        logger.exception("ArgoCD Application delete failed for stack %s", stack.name)
        raise HTTPException(status_code=502, detail=f"ArgoCD delete failed: {_k8s_error_message(e)}")
    try:
        target_k8s = await k8s_for_cluster(db, stack.cluster_id)
        await target_k8s.delete(stack.namespace, {"ingress": f"{stack.argo_app_name}-ingress"})
    except Exception as e:  # noqa: BLE001 — ingress cleanup is best-effort
        logger.info("llm-d ingress cleanup failed for %s: %s", stack.name, e)
```

- [ ] **Step 7: Add `ingress_host` to the serializer**

In `_serialize` (the returned dict, after the `"epp_image"` line ~182), add:

```python
        "ingress_host": f"{stack.argo_app_name}.{settings.effective_ingress_domain}",
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_llmd.py -q`
Expected: PASS (all — patched create tests, new delete/serializer tests, and the pre-existing status/manifest tests).

- [ ] **Step 9: Full llm-d suite + lint**

Run:
```bash
cd backend && python -m pytest tests/test_llmd.py tests/test_llmd_manifests.py tests/test_config.py -q
ruff check app/api/llmd.py
```
Expected: all pass; no new ruff errors on `app/api/llmd.py`.

- [ ] **Step 10: Commit**

```bash
git add backend/app/api/llmd.py backend/tests/test_llmd.py
git commit -m "feat(llmd): create/update/delete the router ingress alongside the stack"
```

---

## Verification (whole feature)

- [ ] **Backend suite delta vs baseline:**
  ```bash
  cd backend && python -m pytest tests/test_llmd.py tests/test_llmd_manifests.py tests/test_config.py -q
  ```
  Gate: 0 new failures vs `origin/main` (the ~21 pre-existing suite failures are `get_litellm_db`-related and out of scope; these three files should be fully green).

- [ ] **Lint:**
  ```bash
  cd backend && ruff check app/config.py app/services/llmd_manifests.py app/api/llmd.py
  ```

- [ ] **Manual (minikube `llmd-test`, `minikube addons enable ingress`):**
  1. Create an llm-d stack via the portal.
  2. `kubectl get ingress -n <stack-namespace>` shows `<app>-ingress` with host `<app>.llm-d.local`, backend Service `<app>-epp` port `http`.
  3. `kubectl get ingress <app>-ingress -n <ns> -o jsonpath='{.spec.rules[0].host}'` → `<app>.llm-d.local`.
  4. Delete the stack → `kubectl get ingress -n <ns>` no longer lists `<app>-ingress`.
  5. Set `APP_LLMD_INGRESS_CLASS=nginx` + `APP_LLMD_INGRESS_DOMAIN=ai.corp.internal`, recreate → Ingress has `ingressClassName: nginx` and host `<app>.ai.corp.internal`.

---

## Notes for the executor

- **Do not** add per-stack ingress form fields, TLS/cert-manager annotations, ArgoCD/GitOps management of the Ingress, or proxy `mode: service` support — all explicit non-goals in the spec.
- The `k8s_for_cluster` client targets the **workload** cluster (where the Service lives); `argocd_placement_for` targets the **ArgoCD host** cluster. They differ for cross-cluster stacks and must not be conflated.
- Frontend display of `ingress_host` is out of scope for this plan (the field is exposed for a later UI change).
