# llm-d ClientIP direct-route set — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in per-stack ClientIP-affinity Service + Ingress that selects the llm-d model-server pods (via `router.modelServers.matchLabels`) and is deployed alongside the existing EPP ingress.

**Architecture:** Pure manifest builders in `llmd_manifests.py` produce a `sessionAffinity: ClientIP` Service (selector = matchLabels, port 80 → modelServers targetPort) and an Ingress in front of it. Two new nullable-safe columns on `custom_llmd_stack` gate/parameterize it. `api/llmd.py` validates (400 on empty matchLabels while enabled), upserts the pair on create/update when enabled, cleans it up when toggled off or on delete. Frontend adds a toggle + host override to the create/edit forms.

**Tech Stack:** FastAPI · SQLAlchemy async · Alembic · kubernetes_asyncio · Next.js app-router · next-intl (en/ko).

## Global Constraints

- **Never stage the `litellm` submodule** — it is dirty for unrelated reasons; `git add` only the exact files each task names.
- **Commit trailer required** on every commit: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- **Backend test gate:** 0 NEW failures vs the origin/main baseline. The conftest does not mock `get_litellm_db`, so ~21 failures + ~11 errors are pre-existing — compare the set/count, never the absolute number.
- **Frontend gates:** `npx tsc --noEmit` exits 0; `npm run lint` introduces 0 new problems (baseline 17: 4 errors, 13 warnings).
- **i18n parity:** `messages/en.json` and `messages/ko.json` must have identical key sets under `llmd` (project convention).
- **Air-gap:** no new external runtime dependencies.
- **Naming (verbatim):** Service `{argo_app_name}-clientip`; Ingress `{argo_app_name}-clientip-ingress`; derived host `{argo_app_name}-direct.{effective_ingress_domain}`; managed-by label value `litellm-portal` (the `MANAGED_BY` constant).
- **Validation-before-write:** the empty-matchLabels 400 must be raised *before* any Kubernetes API call and *outside* the `try` that maps errors to 502 (an `HTTPException` caught by that `except Exception` would be rewritten to 502).

---

## File Structure

| File | Responsibility |
|------|----------------|
| `backend/app/services/llmd_manifests.py` | Pure builders: `clientip_service_name`, `modelservers_target`, `build_clientip_service`, `build_clientip_ingress` |
| `backend/app/db/models/custom_llmd_stack.py` | `clientip_enabled`, `clientip_ingress_host` columns |
| `backend/migrations/versions/045_llmd_clientip_route.py` | add/drop the two columns |
| `backend/app/api/llmd.py` | request fields, resolver/manifest/validation helpers, create/update/delete wiring, serialize |
| `backend/tests/test_llmd_manifests.py` | builder unit tests |
| `backend/tests/test_llmd.py` | API tests + `_stack` helper update |
| `frontend/src/types/index.ts` | `LlmdStackSummary` clientip fields |
| `frontend/src/hooks/use-api.ts` | `CreateLlmdStackBody` / `UpdateLlmdStackBody` clientip fields |
| `frontend/src/app/(app)/admin/llmd/new/page.tsx` | toggle + host field |
| `frontend/src/app/(app)/admin/llmd/[id]/page.tsx` | toggle + host field + detail display |
| `frontend/messages/en.json`, `frontend/messages/ko.json` | `llmd.clientip*` keys |

---

## Task 1: Pure manifest builders

**Files:**
- Modify: `backend/app/services/llmd_manifests.py` (add after `build_llmd_ingress`, which ends at line 113)
- Test: `backend/tests/test_llmd_manifests.py`

**Interfaces:**
- Consumes: `MANAGED_BY` (module constant), `CustomLlmdStack` (only `.argo_app_name`, `.namespace` are read).
- Produces:
  - `clientip_service_name(stack) -> str`
  - `modelservers_target(values: dict) -> tuple[dict, int]`
  - `build_clientip_service(stack, *, match_labels: dict, target_port: int) -> dict`
  - `build_clientip_ingress(stack, *, host: str, ingress_class: str, ingress_path: str) -> dict`

- [ ] **Step 1: Write the failing tests**

Add these imports to the existing import block at the top of `backend/tests/test_llmd_manifests.py` (the `from app.services.llmd_manifests import (...)` list):

```python
    build_clientip_ingress,
    build_clientip_service,
    clientip_service_name,
    modelservers_target,
```

Append these tests to the end of `backend/tests/test_llmd_manifests.py`:

```python
def test_clientip_service_name():
    assert clientip_service_name(_stack()) == "llmd-my-stack-clientip"


def test_build_clientip_service_shape():
    svc = build_clientip_service(_stack(), match_labels={"app": "vllm"}, target_port=8000)
    assert svc["apiVersion"] == "v1"
    assert svc["kind"] == "Service"
    assert svc["metadata"]["name"] == "llmd-my-stack-clientip"
    assert svc["metadata"]["namespace"] == "llmd-my-stack"
    assert svc["metadata"]["labels"]["app.kubernetes.io/managed-by"] == MANAGED_BY
    spec = svc["spec"]
    assert spec["type"] == "ClusterIP"
    assert spec["sessionAffinity"] == "ClientIP"
    assert spec["selector"] == {"app": "vllm"}
    assert spec["ports"] == [{"name": "http", "port": 80, "targetPort": 8000, "protocol": "TCP"}]


def test_build_clientip_service_uses_given_target_port():
    svc = build_clientip_service(_stack(), match_labels={"app": "vllm"}, target_port=8001)
    assert svc["spec"]["ports"][0]["targetPort"] == 8001


def test_build_clientip_ingress_backend_is_clientip_service():
    ing = build_clientip_ingress(
        _stack(), host="direct.corp.internal", ingress_class="nginx", ingress_path="/"
    )
    assert ing["kind"] == "Ingress"
    assert ing["metadata"]["name"] == "llmd-my-stack-clientip-ingress"
    assert ing["metadata"]["labels"]["app.kubernetes.io/managed-by"] == MANAGED_BY
    rule = ing["spec"]["rules"][0]
    assert rule["host"] == "direct.corp.internal"
    backend = rule["http"]["paths"][0]["backend"]["service"]
    assert backend["name"] == "llmd-my-stack-clientip"
    assert backend["port"] == {"number": 80}
    assert ing["spec"]["ingressClassName"] == "nginx"


def test_build_clientip_ingress_omits_class_when_empty():
    ing = build_clientip_ingress(_stack(), host="x", ingress_class="", ingress_path="/")
    assert "ingressClassName" not in ing["spec"]


def test_modelservers_target_extracts_labels_and_port():
    values = {"router": {"modelServers": {"matchLabels": {"app": "vllm"}, "targetPorts": [{"number": 8000}]}}}
    assert modelservers_target(values) == ({"app": "vllm"}, 8000)


def test_modelservers_target_defaults_when_missing():
    assert modelservers_target({}) == ({}, 8000)
    assert modelservers_target({"router": {"modelServers": {}}}) == ({}, 8000)
    assert modelservers_target({"router": {"modelServers": {"targetPorts": []}}}) == ({}, 8000)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_llmd_manifests.py -q`
Expected: FAIL — `ImportError: cannot import name 'clientip_service_name'`.

- [ ] **Step 3: Implement the builders**

In `backend/app/services/llmd_manifests.py`, insert the following immediately after `build_llmd_ingress` (after line 113, before `default_llmd_values`):

```python
def clientip_service_name(stack: CustomLlmdStack) -> str:
    """The ClientIP direct-route Service: ``<argo_app_name>-clientip``.

    Selects the model-server pods directly (router.modelServers.matchLabels)
    with sessionAffinity ClientIP, so a client IP sticks to one vLLM pod —
    a sticky path alongside (not through) the EPP router.
    """
    return f"{stack.argo_app_name}-clientip"


def modelservers_target(values: dict) -> tuple[dict, int]:
    """Extract ``(matchLabels, targetPort)`` from rendered router values.

    Reads ``router.modelServers.matchLabels`` (the pods the router targets) and
    ``router.modelServers.targetPorts[0].number`` (the vLLM port). Tolerant of
    missing keys: labels default to ``{}``, port to ``8000``.
    """
    ms = ((values or {}).get("router") or {}).get("modelServers") or {}
    labels = ms.get("matchLabels") or {}
    ports = ms.get("targetPorts") or []
    port = 8000
    if ports and isinstance(ports[0], dict) and ports[0].get("number"):
        port = int(ports[0]["number"])
    return dict(labels), port


def build_clientip_service(stack: CustomLlmdStack, *, match_labels: dict, target_port: int) -> dict:
    """A ClusterIP Service with ``sessionAffinity: ClientIP`` selecting the
    model-server pods (``match_labels``), fronting their vLLM port.

    Port 80 -> targetPort ``target_port`` (the modelServers targetPort, 8000 by
    default). ``sessionAffinityConfig`` is omitted -> K8s default (10800s).
    Managed by the portal, not ArgoCD.
    """
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": clientip_service_name(stack),
            "namespace": stack.namespace,
            "labels": {"app.kubernetes.io/managed-by": MANAGED_BY},
        },
        "spec": {
            "type": "ClusterIP",
            "sessionAffinity": "ClientIP",
            "selector": dict(match_labels),
            "ports": [{"name": "http", "port": 80, "targetPort": target_port, "protocol": "TCP"}],
        },
    }


def build_clientip_ingress(
    stack: CustomLlmdStack, *, host: str, ingress_class: str, ingress_path: str
) -> dict:
    """An Ingress fronting the ClientIP direct-route Service.

    Backend: Service ``<argo_app_name>-clientip`` port number 80. ``host`` is the
    fully-resolved rule host (the caller applies the override or the derived
    default). ``ingress_class`` empty omits ``ingressClassName`` (cluster
    default). Managed by the portal, not ArgoCD.
    """
    spec: dict = {
        "rules": [
            {
                "host": host,
                "http": {
                    "paths": [
                        {
                            "path": ingress_path,
                            "pathType": "Prefix",
                            "backend": {
                                "service": {
                                    "name": clientip_service_name(stack),
                                    "port": {"number": 80},
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
            "name": f"{stack.argo_app_name}-clientip-ingress",
            "namespace": stack.namespace,
            "labels": {"app.kubernetes.io/managed-by": MANAGED_BY},
        },
        "spec": spec,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_llmd_manifests.py -q`
Expected: PASS (all tests in the file green).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/llmd_manifests.py backend/tests/test_llmd_manifests.py
git commit -m "$(cat <<'EOF'
feat(llmd): ClientIP direct-route manifest builders

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Data model columns + migration

**Files:**
- Modify: `backend/app/db/models/custom_llmd_stack.py` (import line 10; insert columns after `ingress_class` at line 51)
- Create: `backend/migrations/versions/045_llmd_clientip_route.py`

**Interfaces:**
- Produces: `CustomLlmdStack.clientip_enabled: bool` (default False), `CustomLlmdStack.clientip_ingress_host: str | None`.

- [ ] **Step 1: Add the `Boolean` import**

In `backend/app/db/models/custom_llmd_stack.py` line 10, change:

```python
from sqlalchemy import DateTime, ForeignKey, String, func
```
to:
```python
from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
```

- [ ] **Step 2: Add the two columns**

In the same file, insert immediately after the `ingress_class` column (line 51) and before `created_by` (line 52):

```python
    # ClientIP direct-route (opt-in). When enabled, the portal lays down a
    # sessionAffinity=ClientIP Service selecting the model-server pods
    # (router.modelServers.matchLabels) + an Ingress in front of it, alongside
    # the EPP ingress. clientip_ingress_host NULL ->
    # "{argo_app_name}-direct.{effective_ingress_domain}".
    clientip_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    clientip_ingress_host: Mapped[str | None] = mapped_column(String(253), nullable=True)
```

- [ ] **Step 3: Create the migration**

Create `backend/migrations/versions/045_llmd_clientip_route.py`:

```python
"""llm-d ClientIP direct-route set: opt-in toggle + ingress host override.

Two nullable-safe columns on custom_llmd_stack:
- clientip_enabled (bool, default false): when on, the portal deploys a
  sessionAffinity=ClientIP Service (selecting router.modelServers.matchLabels
  pods) + an Ingress, alongside the EPP ingress.
- clientip_ingress_host (nullable): full host override; NULL ->
  "{argo_app_name}-direct.{effective_ingress_domain}".

Revision ID: 045_llmd_clientip_route
Revises: 044_serving_recipe
"""

import sqlalchemy as sa
from alembic import op

revision = "045_llmd_clientip_route"
down_revision = "044_serving_recipe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_llmd_stack",
        sa.Column("clientip_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "custom_llmd_stack",
        sa.Column("clientip_ingress_host", sa.String(253), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("custom_llmd_stack", "clientip_ingress_host")
    op.drop_column("custom_llmd_stack", "clientip_enabled")
```

- [ ] **Step 4: Verify the model + single migration head**

Run:
```bash
cd backend
python -c "from app.db.models.custom_llmd_stack import CustomLlmdStack as C; assert hasattr(C, 'clientip_enabled') and hasattr(C, 'clientip_ingress_host'); print('columns OK')"
python -c "from alembic.config import Config; from alembic.script import ScriptDirectory; s=ScriptDirectory.from_config(Config('alembic.ini')); print('heads:', s.get_heads())"
```
Expected: `columns OK`, then `heads: ('045_llmd_clientip_route',)` (exactly one head; the new revision chains onto `044_serving_recipe`).

> If `alembic.ini` is not at `backend/alembic.ini`, locate it with `find backend -name alembic.ini` and use that path — the assertion (single head `045_llmd_clientip_route`) is unchanged.

- [ ] **Step 5: Commit**

```bash
git add backend/app/db/models/custom_llmd_stack.py backend/migrations/versions/045_llmd_clientip_route.py
git commit -m "$(cat <<'EOF'
feat(llmd): clientip_enabled + clientip_ingress_host columns (migration 045)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: API wiring

**Files:**
- Modify: `backend/app/api/llmd.py`
- Test: `backend/tests/test_llmd.py`

**Interfaces:**
- Consumes: `build_clientip_service`, `build_clientip_ingress`, `clientip_service_name`, `modelservers_target` (Task 1); `stack.clientip_enabled`, `stack.clientip_ingress_host`, `stack.values_snapshot` (Task 2); existing `_ingress_class`, `settings.effective_ingress_domain`, `settings.llmd_ingress_path`, `argocd_placement_for`, `k8s_for_cluster`.
- Produces: serialize keys `clientip_enabled`, `clientip_ingress_host`, `clientip_service`, `clientip_overrides`; request fields `clientip_enabled`, `clientip_ingress_host`.

- [ ] **Step 1: Write the failing tests**

First update the shared `_stack()` helper in `backend/tests/test_llmd.py`. In its `base = dict(...)` (lines 120-127), add these two entries (e.g. after the `ingress_host=None, ingress_class=None,` line):

```python
        clientip_enabled=False, clientip_ingress_host=None,
```

Then append these tests to `backend/tests/test_llmd.py`:

```python
async def test_create_stack_with_clientip_upserts_service_and_ingress(client_for_user, super_user, mock_db):
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
                "namespace": "team-a", "values_yaml": "", "clientip_enabled": True,
            })
    assert resp.status_code == 201
    # Two create_or_patch calls: EPP ingress first, then the ClientIP pair.
    assert fake_target.create_or_patch.await_count == 2
    _ns, clientip = fake_target.create_or_patch.await_args_list[1].args
    by_kind = {m["kind"]: m for m in clientip}
    assert by_kind["Service"]["spec"]["sessionAffinity"] == "ClientIP"
    assert by_kind["Service"]["spec"]["selector"] == {"llm-ops/model-name": "qwen"}
    assert by_kind["Service"]["metadata"]["name"] == "llmd-demo-clientip"
    assert by_kind["Ingress"]["metadata"]["name"] == "llmd-demo-clientip-ingress"
    assert by_kind["Ingress"]["spec"]["rules"][0]["host"] == "llmd-demo-direct.llm-d.local"
    assert by_kind["Ingress"]["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"]["name"] == "llmd-demo-clientip"


async def test_create_stack_clientip_without_labels_400(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_none_result())
    fake_target = MagicMock()
    fake_target.create_or_patch = AsyncMock()
    with patch(
        "app.api.llmd.argocd_placement_for",
        AsyncMock(return_value=(MagicMock(), "argocd", "https://kubernetes.default.svc")),
    ), patch("app.api.llmd.k8s_for_cluster", AsyncMock(return_value=fake_target)):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/admin/llmd-stacks", json={
                "name": "demo", "target_model_name": "", "cluster_id": None,
                "namespace": "team-a", "values_yaml": "", "clientip_enabled": True,
            })
    assert resp.status_code == 400
    assert "matchLabels" in resp.json()["detail"]
    fake_target.create_or_patch.assert_not_awaited()


async def test_update_toggle_off_deletes_clientip(client_for_user, super_user, mock_db):
    stack = _stack(name="demo", namespace="team-a", argo_app_name="llmd-demo", clientip_enabled=True)
    mock_db.execute = AsyncMock(return_value=_result_with(stack))
    fake_k8s = MagicMock()
    fake_k8s.apply_application = AsyncMock()
    fake_k8s.get_application = AsyncMock(return_value=None)
    fake_target = MagicMock()
    fake_target.create_or_patch = AsyncMock()
    fake_target.delete = AsyncMock()
    with patch(
        "app.api.llmd.argocd_placement_for",
        AsyncMock(return_value=(fake_k8s, "argocd", "https://kubernetes.default.svc")),
    ), patch("app.api.llmd.k8s_for_cluster", AsyncMock(return_value=fake_target)):
        async with client_for_user(super_user) as client:
            resp = await client.put(f"/api/admin/llmd-stacks/{stack.id}", json={"clientip_enabled": False})
    assert resp.status_code == 200
    fake_target.delete.assert_awaited_once_with(
        "team-a", {"service": "llmd-demo-clientip", "ingress": "llmd-demo-clientip-ingress"}
    )


async def test_delete_stack_enabled_removes_clientip(client_for_user, super_user, mock_db):
    stack = _stack(name="demo", namespace="team-a", argo_app_name="llmd-demo", clientip_enabled=True)
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
    assert fake_target.delete.await_count == 2
    fake_target.delete.assert_any_await(
        "team-a", {"service": "llmd-demo-clientip", "ingress": "llmd-demo-clientip-ingress"}
    )


def test_serialize_reports_clientip_fields():
    base = _serialize(_stack(argo_app_name="llmd-demo"), {"sync_status": "Synced"})
    assert base["clientip_enabled"] is False
    assert base["clientip_ingress_host"] == "llmd-demo-direct.llm-d.local"
    assert base["clientip_service"] == "llmd-demo-clientip"
    assert base["clientip_overrides"] == {"ingress_host": None}
    over = _serialize(
        _stack(argo_app_name="llmd-demo", clientip_enabled=True, clientip_ingress_host="sticky.corp"),
        {"sync_status": "Synced"},
    )
    assert over["clientip_enabled"] is True
    assert over["clientip_ingress_host"] == "sticky.corp"
    assert over["clientip_overrides"] == {"ingress_host": "sticky.corp"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_llmd.py -q`
Expected: FAIL — the new tests error (e.g. `KeyError: 'clientip_enabled'` in `_serialize`, and the create/update assertions fail because the wiring doesn't exist).

- [ ] **Step 3: Add the request fields**

In `backend/app/api/llmd.py`, add to `CreateLlmdStackRequest` (after `ingress_class` at line 53):

```python
    clientip_enabled: bool = False
    clientip_ingress_host: str | None = None
```

Add to `UpdateLlmdStackRequest` (after `ingress_class` at line 66):

```python
    clientip_enabled: bool | None = None
    clientip_ingress_host: str | None = None
```

- [ ] **Step 4: Extend the manifest imports**

In `backend/app/api/llmd.py`, extend the existing `from app.services.llmd_manifests import (...)` block (lines 27-33) to also import:

```python
    build_clientip_ingress,
    build_clientip_service,
    clientip_service_name,
    modelservers_target,
```

- [ ] **Step 5: Add the resolver / manifest / validation helpers**

In `backend/app/api/llmd.py`, insert immediately after `_ingress_for` (after line 175):

```python
def _clientip_host(stack: CustomLlmdStack) -> str:
    """Effective ClientIP ingress host: per-stack override, else
    {argo_app_name}-direct.{global domain}."""
    return stack.clientip_ingress_host or f"{stack.argo_app_name}-direct.{settings.effective_ingress_domain}"


def _clientip_selector(stack: CustomLlmdStack) -> tuple[dict, int]:
    """(matchLabels, targetPort) the ClientIP Service should use, read from the
    stack's rendered values (same modelServers block the router targets)."""
    return modelservers_target(stack.values_snapshot)


def _clientip_cleanup_names(stack: CustomLlmdStack) -> dict:
    return {
        "service": clientip_service_name(stack),
        "ingress": f"{stack.argo_app_name}-clientip-ingress",
    }


def _clientip_manifests(stack: CustomLlmdStack) -> list[dict]:
    """[Service, Ingress] for the ClientIP direct route when enabled, else []."""
    if not stack.clientip_enabled:
        return []
    match_labels, target_port = _clientip_selector(stack)
    return [
        build_clientip_service(stack, match_labels=match_labels, target_port=target_port),
        build_clientip_ingress(
            stack,
            host=_clientip_host(stack),
            ingress_class=_ingress_class(stack),
            ingress_path=settings.llmd_ingress_path or "/",
        ),
    ]


def _require_clientip_selector(stack: CustomLlmdStack) -> None:
    """400 when the ClientIP route is enabled but modelServers.matchLabels is
    empty — a selector-less Service would bind no endpoints. Must be called
    before any K8s write and outside the 502-mapping try block."""
    if stack.clientip_enabled and not _clientip_selector(stack)[0]:
        raise HTTPException(
            status_code=400,
            detail="modelServers.matchLabels is required to enable the ClientIP direct route.",
        )
```

- [ ] **Step 6: Wire create_stack**

In `create_stack`, add the two columns to the `CustomLlmdStack(...)` constructor (after the `ingress_class=...` kwarg at line 366):

```python
        clientip_enabled=body.clientip_enabled,
        clientip_ingress_host=(body.clientip_ingress_host or "").strip() or None,
```

Then change the block at lines 370-378 from:

```python
    stack.values_snapshot = _values_for(stack)
    db.add(stack)
    await db.flush()

    try:
        k8s, argocd_ns, dest_server = await argocd_placement_for(db, stack.cluster_id)
        await k8s.apply_application(argocd_ns, _application_for(stack, argocd_ns, dest_server))
        target_k8s = await k8s_for_cluster(db, stack.cluster_id)
        await target_k8s.create_or_patch(stack.namespace, [_ingress_for(stack)])
```

to:

```python
    stack.values_snapshot = _values_for(stack)
    _require_clientip_selector(stack)  # 400 before any K8s write (outside the try)
    db.add(stack)
    await db.flush()

    try:
        k8s, argocd_ns, dest_server = await argocd_placement_for(db, stack.cluster_id)
        await k8s.apply_application(argocd_ns, _application_for(stack, argocd_ns, dest_server))
        target_k8s = await k8s_for_cluster(db, stack.cluster_id)
        await target_k8s.create_or_patch(stack.namespace, [_ingress_for(stack)])
        clientip = _clientip_manifests(stack)
        if clientip:
            await target_k8s.create_or_patch(stack.namespace, clientip)
```

- [ ] **Step 7: Wire update_stack**

In `update_stack`, add after the `if body.values_yaml is not None:` block (after line 421):

```python
    if body.clientip_enabled is not None:
        stack.clientip_enabled = body.clientip_enabled
    if body.clientip_ingress_host is not None:
        stack.clientip_ingress_host = body.clientip_ingress_host.strip() or None
```

Then change the block at lines 430-442 from:

```python
    stack.values_snapshot = _values_for(stack)
    stack.updated_by = user.user_id
    await db.flush()

    try:
        k8s, argocd_ns, dest_server = await argocd_placement_for(db, stack.cluster_id)
        await k8s.apply_application(argocd_ns, _application_for(stack, argocd_ns, dest_server))
        target_k8s = await k8s_for_cluster(db, stack.cluster_id)
        await target_k8s.create_or_patch(stack.namespace, [_ingress_for(stack)])
    except Exception as e:
        logger.exception("ArgoCD Application update failed for stack %s", stack.name)
        raise HTTPException(status_code=502, detail=f"ArgoCD update failed: {_k8s_error_message(e)}")
    await db.commit()
```

to:

```python
    stack.values_snapshot = _values_for(stack)
    _require_clientip_selector(stack)  # 400 before any K8s write (outside the try)
    stack.updated_by = user.user_id
    await db.flush()

    try:
        k8s, argocd_ns, dest_server = await argocd_placement_for(db, stack.cluster_id)
        await k8s.apply_application(argocd_ns, _application_for(stack, argocd_ns, dest_server))
        target_k8s = await k8s_for_cluster(db, stack.cluster_id)
        await target_k8s.create_or_patch(stack.namespace, [_ingress_for(stack)])
        clientip = _clientip_manifests(stack)
        if clientip:
            await target_k8s.create_or_patch(stack.namespace, clientip)
    except Exception as e:
        logger.exception("ArgoCD Application update failed for stack %s", stack.name)
        raise HTTPException(status_code=502, detail=f"ArgoCD update failed: {_k8s_error_message(e)}")
    if not stack.clientip_enabled:
        # Toggled off (or never on): best-effort remove the pair so the cluster
        # matches the desired state. Cleanup failure must not fail the update.
        try:
            await target_k8s.delete(stack.namespace, _clientip_cleanup_names(stack))
        except Exception as e:  # noqa: BLE001 — cleanup is best-effort
            logger.info("llm-d clientip cleanup failed for %s: %s", stack.name, e)
    await db.commit()
```

- [ ] **Step 8: Wire delete_stack**

In `delete_stack`, change the ingress-cleanup block at lines 464-468 from:

```python
    try:
        target_k8s = await k8s_for_cluster(db, stack.cluster_id)
        await target_k8s.delete(stack.namespace, {"ingress": f"{stack.argo_app_name}-ingress"})
    except Exception as e:  # noqa: BLE001 — ingress cleanup is best-effort
        logger.info("llm-d ingress cleanup failed for %s: %s", stack.name, e)
```

to:

```python
    try:
        target_k8s = await k8s_for_cluster(db, stack.cluster_id)
        await target_k8s.delete(stack.namespace, {"ingress": f"{stack.argo_app_name}-ingress"})
        if stack.clientip_enabled:
            await target_k8s.delete(stack.namespace, _clientip_cleanup_names(stack))
    except Exception as e:  # noqa: BLE001 — ingress cleanup is best-effort
        logger.info("llm-d ingress cleanup failed for %s: %s", stack.name, e)
```

- [ ] **Step 9: Extend _serialize**

In `_serialize` (the returned dict, before the `**status_fields,` line at line 250), add:

```python
        "clientip_enabled": stack.clientip_enabled,
        "clientip_ingress_host": _clientip_host(stack),
        "clientip_service": clientip_service_name(stack),
        "clientip_overrides": {"ingress_host": stack.clientip_ingress_host},
```

- [ ] **Step 10: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_llmd.py tests/test_llmd_manifests.py -q`
Expected: PASS (new tests green; `test_create_stack_applies_application` and `test_delete_stack_removes_ingress` still green — the disabled-default path is unchanged: one create_or_patch call and one delete call respectively).

- [ ] **Step 11: Ruff + commit**

```bash
cd backend && ruff check app/api/llmd.py app/services/llmd_manifests.py
```
Expected: no NEW findings vs the file's baseline (compare against `git stash` / origin/main if unsure).

```bash
git add backend/app/api/llmd.py backend/tests/test_llmd.py
git commit -m "$(cat <<'EOF'
feat(llmd): deploy ClientIP direct-route set on enabled stacks

Validate matchLabels before any K8s write; upsert the Service+Ingress pair
when enabled; best-effort cleanup on toggle-off and delete; expose the
clientip fields in the serialized stack.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Frontend — toggle, host field, detail display, i18n

**Files:**
- Modify: `frontend/src/types/index.ts`
- Modify: `frontend/src/hooks/use-api.ts`
- Modify: `frontend/src/app/(app)/admin/llmd/new/page.tsx`
- Modify: `frontend/src/app/(app)/admin/llmd/[id]/page.tsx`
- Modify: `frontend/messages/en.json`, `frontend/messages/ko.json`

**Interfaces:**
- Consumes: serialized keys from Task 3 (`clientip_enabled`, `clientip_ingress_host`, `clientip_service`, `clientip_overrides`); request body fields `clientip_enabled`, `clientip_ingress_host`.

- [ ] **Step 1: Types — `LlmdStackSummary`**

In `frontend/src/types/index.ts`, insert after the `ingress_overrides` block (after line 714, before `helm_values`):

```ts
  clientip_enabled: boolean;
  clientip_ingress_host: string;
  clientip_service: string;
  clientip_overrides: {
    ingress_host: string | null;
  };
```

- [ ] **Step 2: Types — request bodies**

In `frontend/src/hooks/use-api.ts`, add to `CreateLlmdStackBody` (after `ingress_class?: string | null;` at line 1522):

```ts
  clientip_enabled?: boolean;
  clientip_ingress_host?: string | null;
```

Add the same two lines to `UpdateLlmdStackBody` (after `ingress_class?: string | null;` at line 1535).

- [ ] **Step 3: new/page.tsx — state + submit + UI**

In `frontend/src/app/(app)/admin/llmd/new/page.tsx`:

(a) Add to the `FormState` type (after `ingress_host: string; ingress_class: string;` at line 35):
```ts
  clientip_enabled: boolean; clientip_ingress_host: string;
```

(b) Add to `EMPTY` (after `ingress_host: "", ingress_class: "",` at line 48):
```ts
  clientip_enabled: false, clientip_ingress_host: "",
```

(c) Add to the `body` object (after the `ingress_class: overrideOrNull(...)` line at line 126):
```ts
      clientip_enabled: form.clientip_enabled,
      clientip_ingress_host: form.clientip_ingress_host.trim() || null,
```

(d) Insert a new `<details>` block immediately after the ingress-override `</details>` (after line 325, before `</CardContent>`):
```tsx
            <details className="rounded-md border p-3">
              <summary className="cursor-pointer text-sm font-medium">{t("clientipTitle")}</summary>
              <p className="text-xs text-muted-foreground mt-1">{t("clientipHint")}</p>
              <div className="mt-3 space-y-3">
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={form.clientip_enabled}
                    onChange={(e) => setForm({ ...form, clientip_enabled: e.target.checked })}
                  />
                  {t("clientipEnableLabel")}
                </label>
                {form.clientip_enabled && (
                  <div>
                    <Label htmlFor="llmd-clientip-host">{t("clientipHostLabel")}</Label>
                    <Input id="llmd-clientip-host" value={form.clientip_ingress_host}
                      placeholder={chartDefaults ? `${form.name || "<name>"}-direct.${chartDefaults.ingress_domain}` : undefined}
                      onChange={(e) => setForm({ ...form, clientip_ingress_host: e.target.value })} />
                    <p className="text-xs text-muted-foreground mt-1">{t("clientipHostHint")}</p>
                  </div>
                )}
              </div>
            </details>
```

- [ ] **Step 4: [id]/page.tsx — state + save + detail + edit UI**

In `frontend/src/app/(app)/admin/llmd/[id]/page.tsx`:

(a) Add to the `EditState` type (after `ingress_host: string; ingress_class: string;` at line 31):
```ts
  clientip_enabled: boolean; clientip_ingress_host: string;
```

(b) Add to the `startEdit` `setForm({...})` object (after `ingress_class: stack.ingress_overrides.ingress_class ?? "",` at line 104):
```ts
      clientip_enabled: stack.clientip_enabled,
      clientip_ingress_host: stack.clientip_overrides.ingress_host ?? "",
```

(c) Add to the `handleSave` body (after `ingress_class: overrideOrNull(...)` at line 126):
```ts
          clientip_enabled: form.clientip_enabled,
          clientip_ingress_host: form.clientip_ingress_host.trim() || null,
```

(d) Add read-only detail fields to the identity grid (after the `ingressClassLabel` Field at line 216):
```tsx
            <Field label={t("clientipEnableLabel")}>{stack.clientip_enabled ? t("clientipOn") : t("clientipOff")}</Field>
            {stack.clientip_enabled && <Field label={t("clientipHostLabel")} mono>{stack.clientip_ingress_host}</Field>}
            {stack.clientip_enabled && <Field label={t("clientipServiceLabel")} mono>{stack.clientip_service}</Field>}
```

(e) Insert an edit-mode `<details>` block immediately after the ingress-override `</details>` (after line 307, before the closing `</div>` at line 308):
```tsx
              <details className="rounded-md border p-3">
                <summary className="cursor-pointer text-sm font-medium">{t("clientipTitle")}</summary>
                <p className="text-xs text-muted-foreground mt-1">{t("clientipHint")}</p>
                <div className="mt-3 space-y-3">
                  <label className="flex items-center gap-2 text-sm">
                    <input type="checkbox" checked={form.clientip_enabled}
                      onChange={(e) => setForm({ ...form, clientip_enabled: e.target.checked })} />
                    {t("clientipEnableLabel")}
                  </label>
                  {form.clientip_enabled && (
                    <div>
                      <Label htmlFor="llmd-clientip-host">{t("clientipHostLabel")}</Label>
                      <Input id="llmd-clientip-host" value={form.clientip_ingress_host} placeholder={stack.clientip_ingress_host}
                        onChange={(e) => setForm({ ...form, clientip_ingress_host: e.target.value })} />
                      <p className="text-xs text-muted-foreground mt-1">{t("clientipHostHint")}</p>
                    </div>
                  )}
                </div>
              </details>
```

- [ ] **Step 5: i18n — en.json**

In `frontend/messages/en.json`, insert after `"ingressClassHint": ...` (line 1356), before `"liveErrorTitle"`:

```json
    "clientipTitle": "ClientIP direct route",
    "clientipHint": "Also deploy a sessionAffinity=ClientIP Service selecting the model-server pods (router.modelServers.matchLabels) plus an Ingress, alongside the EPP router. A client IP then sticks to one vLLM pod.",
    "clientipEnableLabel": "Enable ClientIP direct route",
    "clientipOn": "Enabled",
    "clientipOff": "Disabled",
    "clientipHostLabel": "ClientIP ingress host",
    "clientipHostHint": "Full host for the ClientIP ingress. Blank uses the [name]-direct.[global-domain] default.",
    "clientipServiceLabel": "ClientIP service",
```

- [ ] **Step 6: i18n — ko.json**

In `frontend/messages/ko.json`, insert after `"ingressClassHint": ...` (line 1356), before `"liveErrorTitle"`:

```json
    "clientipTitle": "ClientIP 직결 경로",
    "clientipHint": "EPP 라우터와 별개로, 모델 서버 파드(router.modelServers.matchLabels)를 선택하는 sessionAffinity=ClientIP 서비스와 인그레스를 함께 배포합니다. 같은 클라이언트 IP는 하나의 vLLM 파드에 고정됩니다.",
    "clientipEnableLabel": "ClientIP 직결 경로 사용",
    "clientipOn": "사용",
    "clientipOff": "미사용",
    "clientipHostLabel": "ClientIP 인그레스 host",
    "clientipHostHint": "ClientIP 인그레스의 전체 host. 비우면 [스택명]-direct.[전역도메인] 기본값을 사용합니다.",
    "clientipServiceLabel": "ClientIP 서비스",
```

- [ ] **Step 7: Verify type-check, lint, i18n parity**

Run:
```bash
cd frontend
npx tsc --noEmit
npm run lint
python3 -c "import json; e=json.load(open('messages/en.json'))['llmd']; k=json.load(open('messages/ko.json'))['llmd']; print('en',len(e),'ko',len(k)); assert set(e)==set(k), ('DIFF', set(e)^set(k))"
```
Expected: `tsc` exits 0; `npm run lint` shows 0 new problems vs the baseline (17: 4 errors / 13 warnings); the parity line prints equal counts and does not assert.

> The stale TypeScript errors in `deploy-from-recipe-dialog.tsx` / `admin/recipes/page.tsx` are pre-existing cross-branch LSP noise — confirm they are not in the files this task touched. A clean `npx tsc --noEmit` on this branch is the source of truth.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/hooks/use-api.ts \
  "frontend/src/app/(app)/admin/llmd/new/page.tsx" \
  "frontend/src/app/(app)/admin/llmd/[id]/page.tsx" \
  frontend/messages/en.json frontend/messages/ko.json
git commit -m "$(cat <<'EOF'
feat(frontend): llm-d ClientIP direct-route toggle + host field

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Final Verification (whole branch)

**Backend:**
```bash
cd backend
python -m pytest tests/test_llmd.py tests/test_llmd_manifests.py tests/test_llmd_k8s.py -q
ruff check app/api/llmd.py app/services/llmd_manifests.py app/db/models/custom_llmd_stack.py
```
Then a full-suite baseline compare (0 new failures/errors):
```bash
python -m pytest -q 2>&1 | tail -3
```

**Frontend:**
```bash
cd frontend
npx tsc --noEmit && npm run lint
```

**Manual (dev server, not runnable headless):**
- Create a stack with the ClientIP toggle on and a target model → the detail page shows "ClientIP service: {name}" and the derived host; the applied-resources list shows the new Service + Ingress.
- Edit the stack, turn the toggle off → the Service + Ingress disappear from the cluster (best-effort delete); turning it on with an empty `router.modelServers.matchLabels` returns a 400 toast.
- Regression: a stack with the toggle off deploys exactly as before (one EPP ingress, no extra resources).

**Cluster E2E:** with a real vLLM deployment carrying the matchLabels, curl the ClientIP host repeatedly and confirm requests stick to one pod.

---

## Self-Review notes (author)

- **Spec coverage:** manifests (Task 1) · columns + migration 045 (Task 2) · request fields, validation-before-write, create/update/delete + toggle-off cleanup, serialize (Task 3) · frontend toggle/host/detail + i18n parity (Task 4). All spec sections mapped.
- **Type consistency:** `clientip_service_name`, `modelservers_target`, `build_clientip_service(*, match_labels, target_port)`, `build_clientip_ingress(*, host, ingress_class, ingress_path)` are used with identical signatures across Task 1 (def), Task 3 (call), and tests. Serialize keys (`clientip_enabled`, `clientip_ingress_host`, `clientip_service`, `clientip_overrides.ingress_host`) match the frontend `LlmdStackSummary` fields in Task 4.
- **Existing-test safety:** default (disabled) create → one `create_or_patch`; default delete → one `delete` — both preserved, so `test_create_stack_applies_application` and `test_delete_stack_removes_ingress` stay green.
