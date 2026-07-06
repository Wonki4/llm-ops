# llm-d ArgoCD CRD Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provision llm-d stacks by applying the ArgoCD `Application` custom resource to the cluster via the K8s API (reusing registered clusters), replacing the ArgoCD REST client and removing the connection registry.

**Architecture:** The portal already builds an `argoproj.io/v1alpha1` Application object. Instead of POSTing it to ArgoCD's REST API, apply it to the cluster's ArgoCD control-plane namespace via `CustomObjectsApi` using the same `k8s_for_cluster` plumbing the serving/benchmark features use; ArgoCD's controller reconciles it and writes sync/health into the CR `.status`, which the portal reads back.

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic + kubernetes_asyncio `CustomObjectsApi` (backend); Next.js + react-query + shadcn/ui + next-intl (frontend); pytest `asyncio_mode = "auto"`.

**Spec:** `docs/superpowers/specs/2026-07-06-llmd-argocd-crd-design.md`

## Global Constraints

- Branch: all commits on `feat/llmd-argocd-crd` (already checked out).
- Backend tests: `cd /Users/wongibaek/Documents/litellm-ops/backend && .venv/bin/pytest` (asyncio_mode auto — async tests need no decorator). Repo has ~21 PRE-EXISTING failures in teams/keys/me/catalog tests unrelated to this work; the gate is **no NEW failures**, not zero failures.
- Ruff: line-length 120, py311. Run `.venv/bin/ruff check <files>` before each backend commit; only pre-existing findings are acceptable.
- Application CR GVK: `group="argoproj.io"`, `version="v1alpha1"`, `plural="applications"`.
- ArgoCD control-plane namespace: per-cluster `custom_k8s_cluster.argocd_namespace` (default `"argocd"`); null-cluster stacks use `settings.argocd_namespace` (env `APP_ARGOCD_NAMESPACE`, default `"argocd"`).
- Response shape for stack status is unchanged: `{sync_status, health_status, status_message}` read from CR `.status.sync.status` / `.status.health.status` / `.status.health.message`.
- Migration head is `034_external_serving`. New migrations chain `035_cluster_argocd_namespace` → `036_drop_argocd_connection`.
- Frontend: every user-visible string added in BOTH `frontend/messages/en.json` and `frontend/messages/ko.json`. Gates: `npx tsc --noEmit` exit 0; `npm run lint` no NEW errors (4 pre-existing errors baseline).
- Migration apply (local dockerized DB): `cd backend && APP_DATABASE_URL="postgresql+asyncpg://llmproxy:dbpassword9090@localhost:5432/litellm_portal" .venv/bin/alembic upgrade head`. Version table is `custom_alembic_version`.

## File Map

| File | Action | Responsibility |
|---|---|---|
| `backend/app/clients/k8s.py` | Modify | Add `apply_application` / `get_application` / `delete_application` (CustomObjectsApi) |
| `backend/app/services/llmd_manifests.py` | Modify | `build_argo_application` sets `metadata.namespace` |
| `backend/app/config.py` | Modify | Add `argocd_namespace: str = "argocd"` |
| `backend/app/services/clusters.py` | Modify | Add `argocd_namespace_for(db, cluster_id)` |
| `backend/app/api/llmd.py` | Rewrite | CRD create/update/delete/status/applied; drop `/resource`; K8s error mapping |
| `backend/app/db/models/custom_k8s_cluster.py` | Modify | Add `argocd_namespace` column |
| `backend/app/api/k8s_clusters.py` | Modify | Wire `argocd_namespace` into request models, serialize, create/update |
| `backend/migrations/versions/035_cluster_argocd_namespace.py` | Create | Add `custom_k8s_cluster.argocd_namespace` |
| `backend/migrations/versions/036_drop_argocd_connection.py` | Create | Drop `custom_llmd_stack.argocd_connection_id` + `custom_argocd_connection` table |
| `backend/app/db/models/custom_llmd_stack.py` | Modify | Remove `argocd_connection_id` mapped column |
| `backend/app/clients/argocd.py` | Delete | REST client removed |
| `backend/app/api/argocd_connections.py` | Delete | Connection registry API removed |
| `backend/app/db/models/custom_argocd_connection.py` | Delete | Connection model removed |
| `backend/app/db/models/__init__.py` | Modify | Drop `CustomArgocdConnection` export |
| `backend/app/main.py` | Modify | Drop `argocd_connections` import + router |
| `backend/tests/test_llmd.py` | Modify | Rewrite for CRD flow |
| `backend/tests/test_argocd.py` | Delete | Connection-registry tests removed |
| `backend/tests/test_k8s_clusters.py` | Modify | Cover `argocd_namespace` round-trip |
| `frontend/src/components/cluster-settings-tab.tsx` | Modify | Add "ArgoCD namespace" field |
| `frontend/src/components/argocd-settings-tab.tsx` | Delete | Connections tab removed |
| `frontend/src/app/(app)/admin/settings/page.tsx` | Modify | Drop the ArgoCD tab |
| `frontend/src/hooks/use-api.ts` | Modify | Drop ArgoCD-connection hooks/types; add cluster `argocd_namespace` |
| `frontend/src/app/(app)/admin/llmd/new/page.tsx` | Modify | Connection selector → cluster selector |
| `frontend/src/app/(app)/admin/llmd/[id]/page.tsx` | Modify | Drop resource-manifest view; connection → cluster |
| `frontend/messages/en.json`, `frontend/messages/ko.json` | Modify | Add cluster argocd-namespace strings; remove connection strings |
| `deploy/rbac/portal-k8s-rbac.yaml` | Modify | Add `applications.argoproj.io` rule; update header note |

---

### Task 1: K8sClient Application-CR methods

**Files:**
- Modify: `backend/app/clients/k8s.py` (add after `read_service_cluster_ip`, before the Jobs section)
- Test: `backend/tests/test_llmd.py` (new tests appended; file rewritten in Task 4 — for now add a focused test file section)

**Interfaces:**
- Produces (Task 4 consumes):
  - `async apply_application(namespace: str, manifest: dict) -> None` — read-then create-or-patch the Application named `manifest["metadata"]["name"]`.
  - `async get_application(namespace: str, name: str) -> dict | None` — the CR dict, or None on 404.
  - `async delete_application(namespace: str, name: str) -> None` — foreground cascade; 404 swallowed.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_llmd_k8s.py`:

```python
"""Application-CR operations on K8sClient (CustomObjectsApi)."""

import types
from unittest.mock import AsyncMock, MagicMock, patch

from kubernetes_asyncio.client.exceptions import ApiException

from app.clients.k8s import K8sClient

GVK = dict(group="argoproj.io", version="v1alpha1", plural="applications")


def _client_with(co):
    fake_api = MagicMock()
    fake_api.close = AsyncMock()
    return fake_api, patch.object(K8sClient, "_api_client", AsyncMock(return_value=fake_api)), \
        patch("app.clients.k8s.client.CustomObjectsApi", return_value=co)


async def test_apply_application_creates_when_absent():
    co = MagicMock()
    co.get_namespaced_custom_object = AsyncMock(side_effect=ApiException(status=404))
    co.create_namespaced_custom_object = AsyncMock()
    co.patch_namespaced_custom_object = AsyncMock()
    _api, p_api, p_co = _client_with(co)
    manifest = {"metadata": {"name": "llmd-x"}, "spec": {}}
    with p_api, p_co:
        await K8sClient().apply_application("argocd", manifest)
    co.create_namespaced_custom_object.assert_awaited_once()
    co.patch_namespaced_custom_object.assert_not_called()
    kwargs = co.create_namespaced_custom_object.await_args.kwargs
    assert kwargs["namespace"] == "argocd" and kwargs["body"] == manifest
    assert kwargs["group"] == "argoproj.io" and kwargs["plural"] == "applications"


async def test_apply_application_patches_when_present():
    co = MagicMock()
    co.get_namespaced_custom_object = AsyncMock(return_value={"metadata": {"name": "llmd-x"}})
    co.create_namespaced_custom_object = AsyncMock()
    co.patch_namespaced_custom_object = AsyncMock()
    _api, p_api, p_co = _client_with(co)
    with p_api, p_co:
        await K8sClient().apply_application("argocd", {"metadata": {"name": "llmd-x"}, "spec": {}})
    co.patch_namespaced_custom_object.assert_awaited_once()
    co.create_namespaced_custom_object.assert_not_called()


async def test_get_application_returns_none_on_404():
    co = MagicMock()
    co.get_namespaced_custom_object = AsyncMock(side_effect=ApiException(status=404))
    _api, p_api, p_co = _client_with(co)
    with p_api, p_co:
        assert await K8sClient().get_application("argocd", "llmd-x") is None


async def test_get_application_returns_object():
    co = MagicMock()
    co.get_namespaced_custom_object = AsyncMock(return_value={"status": {"sync": {"status": "Synced"}}})
    _api, p_api, p_co = _client_with(co)
    with p_api, p_co:
        obj = await K8sClient().get_application("argocd", "llmd-x")
    assert obj["status"]["sync"]["status"] == "Synced"


async def test_delete_application_swallows_404():
    co = MagicMock()
    co.delete_namespaced_custom_object = AsyncMock(side_effect=ApiException(status=404))
    _api, p_api, p_co = _client_with(co)
    with p_api, p_co:
        await K8sClient().delete_application("argocd", "llmd-x")  # no raise
    co.delete_namespaced_custom_object.assert_awaited_once()
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/wongibaek/Documents/litellm-ops/backend && .venv/bin/pytest tests/test_llmd_k8s.py -v`
Expected: FAIL — `AttributeError: 'K8sClient' object has no attribute 'apply_application'`.

- [ ] **Step 3: Implement the methods**

In `backend/app/clients/k8s.py`, after `read_service_cluster_ip` (before the `# ─── Batch v1 (Jobs)` comment), add:

```python
    # ─── ArgoCD Applications (custom resource) ──────────────────────

    _ARGO = dict(group="argoproj.io", version="v1alpha1", plural="applications")

    async def apply_application(self, namespace: str, manifest: dict) -> None:
        """Create-or-patch an argoproj.io Application in ``namespace``.

        Read then create (absent) or merge-patch (present) — mirrors
        create_or_patch for built-in kinds. ArgoCD's controller reconciles it.
        """
        api_client = await self._api_client()
        try:
            co = client.CustomObjectsApi(api_client)
            name = manifest["metadata"]["name"]
            try:
                await co.get_namespaced_custom_object(**self._ARGO, namespace=namespace, name=name)
                exists = True
            except ApiException as e:
                if e.status == 404:
                    exists = False
                else:
                    raise
            if exists:
                await co.patch_namespaced_custom_object(
                    **self._ARGO, namespace=namespace, name=name, body=manifest
                )
            else:
                await co.create_namespaced_custom_object(**self._ARGO, namespace=namespace, body=manifest)
        finally:
            await api_client.close()

    async def get_application(self, namespace: str, name: str) -> dict | None:
        """Read an Application CR; None if it does not exist."""
        api_client = await self._api_client()
        try:
            co = client.CustomObjectsApi(api_client)
            try:
                return await co.get_namespaced_custom_object(**self._ARGO, namespace=namespace, name=name)
            except ApiException as e:
                if e.status == 404:
                    return None
                raise
        finally:
            await api_client.close()

    async def delete_application(self, namespace: str, name: str) -> None:
        """Delete an Application (cascades to its workloads); 404 swallowed."""
        api_client = await self._api_client()
        try:
            co = client.CustomObjectsApi(api_client)
            try:
                await co.delete_namespaced_custom_object(
                    **self._ARGO, namespace=namespace, name=name, propagation_policy="Foreground"
                )
            except ApiException as e:
                if e.status != 404:
                    raise
        finally:
            await api_client.close()
```

- [ ] **Step 4: Run to verify pass**

Run: `cd /Users/wongibaek/Documents/litellm-ops/backend && .venv/bin/pytest tests/test_llmd_k8s.py -v && .venv/bin/ruff check app/clients/k8s.py tests/test_llmd_k8s.py`
Expected: 5 passed; ruff clean.

- [ ] **Step 5: Commit**

```bash
cd /Users/wongibaek/Documents/litellm-ops
git add backend/app/clients/k8s.py backend/tests/test_llmd_k8s.py
git commit -m "feat(k8s): apply/get/delete argoproj.io Application via CustomObjectsApi"
```

---

### Task 2: Manifest namespace + config + namespace resolver

**Files:**
- Modify: `backend/app/services/llmd_manifests.py` (`build_argo_application`)
- Modify: `backend/app/config.py` (add `argocd_namespace`)
- Modify: `backend/app/services/clusters.py` (add `argocd_namespace_for`)
- Test: `backend/tests/test_llmd_k8s.py` (append)

**Interfaces:**
- Consumes: `CustomK8sCluster` (existing), `settings` (existing).
- Produces (Task 4 consumes):
  - `build_argo_application(stack, *, chart_repo, chart_name, chart_version, values, project, argocd_namespace)` — now requires `argocd_namespace`, sets `metadata.namespace`.
  - `async argocd_namespace_for(db, cluster_id: uuid.UUID | str | None) -> str`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_llmd_k8s.py`:

```python
import uuid

from app.services.llmd_manifests import build_argo_application


def _stack(**kw):
    base = dict(argo_app_name="llmd-demo", namespace="team-a")
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_build_argo_application_sets_metadata_namespace():
    app = build_argo_application(
        _stack(), chart_repo="oci://r", chart_name="standalone", chart_version="v1",
        values={"a": 1}, project="llm-d", argocd_namespace="argocd",
    )
    assert app["metadata"]["namespace"] == "argocd"
    assert app["metadata"]["name"] == "llmd-demo"
    assert app["spec"]["destination"]["namespace"] == "team-a"
    assert app["spec"]["source"]["helm"]["valuesObject"] == {"a": 1}


async def test_argocd_namespace_for_null_cluster_uses_global():
    from app.services.clusters import argocd_namespace_for
    db = MagicMock()
    db.execute = AsyncMock()
    assert await argocd_namespace_for(db, None) == "argocd"
    db.execute.assert_not_called()


async def test_argocd_namespace_for_uses_cluster_value():
    from app.services.clusters import argocd_namespace_for
    row = types.SimpleNamespace(argocd_namespace="argo-system")
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    assert await argocd_namespace_for(db, uuid.uuid4()) == "argo-system"
```

- [ ] **Step 2: Run to verify fail**

Run: `cd /Users/wongibaek/Documents/litellm-ops/backend && .venv/bin/pytest tests/test_llmd_k8s.py -k "argo_application or argocd_namespace_for" -v`
Expected: FAIL — `build_argo_application()` missing `argocd_namespace` kwarg / `argocd_namespace_for` import error.

- [ ] **Step 3: Implement**

In `backend/app/services/llmd_manifests.py`, change the `build_argo_application` signature and metadata:

```python
def build_argo_application(
    stack: CustomLlmdStack,
    *,
    chart_repo: str,
    chart_name: str,
    chart_version: str,
    values: dict,
    project: str,
    argocd_namespace: str,
) -> dict:
```

and inside the returned dict, change `metadata` to:

```python
        "metadata": {
            "name": stack.argo_app_name,
            "namespace": argocd_namespace,
            "labels": {"app.kubernetes.io/managed-by": MANAGED_BY},
        },
```

In `backend/app/config.py`, add next to the other argo/llmd settings (after `argo_project`):

```python
    argocd_namespace: str = "argocd"  # ArgoCD control-plane ns for null-cluster stacks
```

In `backend/app/services/clusters.py`, add the resolver (imports `select`, `CustomK8sCluster`, `settings` already present or add them):

```python
async def argocd_namespace_for(db: AsyncSession, cluster_id: uuid.UUID | str | None) -> str:
    """The ArgoCD control-plane namespace for a stack's cluster.

    A registered cluster's ``argocd_namespace`` wins; a null cluster (portal
    default kubeconfig) falls back to the global ``settings.argocd_namespace``.
    """
    from app.config import settings

    if not cluster_id:
        return settings.argocd_namespace
    cid = cluster_id if isinstance(cluster_id, uuid.UUID) else uuid.UUID(str(cluster_id))
    row = (
        await db.execute(select(CustomK8sCluster).where(CustomK8sCluster.id == cid))
    ).scalar_one_or_none()
    return (row.argocd_namespace if row and row.argocd_namespace else settings.argocd_namespace)
```

(If `clusters.py` lacks the `select` / `CustomK8sCluster` imports, they are already there for `k8s_for_cluster`; reuse them. This resolver does not depend on the `argocd_namespace` column existing yet — Task 3 adds it — but the test mocks the row, so it passes now.)

- [ ] **Step 4: Run to verify pass**

Run: `cd /Users/wongibaek/Documents/litellm-ops/backend && .venv/bin/pytest tests/test_llmd_k8s.py -v && .venv/bin/ruff check app/services/llmd_manifests.py app/config.py app/services/clusters.py`
Expected: all pass; ruff clean. (Note: existing callers of `build_argo_application` in `api/llmd.py` are now broken — that file is rewritten in Task 4. Do NOT run the full suite here.)

- [ ] **Step 5: Commit**

```bash
cd /Users/wongibaek/Documents/litellm-ops
git add backend/app/services/llmd_manifests.py backend/app/config.py backend/app/services/clusters.py backend/tests/test_llmd_k8s.py
git commit -m "feat(llmd): Application metadata.namespace + per-cluster argocd namespace resolver"
```

---

### Task 3: cluster.argocd_namespace — model, migration 035, cluster API

**Files:**
- Modify: `backend/app/db/models/custom_k8s_cluster.py`
- Create: `backend/migrations/versions/035_cluster_argocd_namespace.py`
- Modify: `backend/app/api/k8s_clusters.py` (request models, `_serialize`, create/update handlers)
- Test: `backend/tests/test_k8s_clusters.py`

**Interfaces:**
- Produces: `CustomK8sCluster.argocd_namespace: str` (default `"argocd"`); the clusters API accepts/returns `argocd_namespace`.

- [ ] **Step 1: Add the model column**

In `backend/app/db/models/custom_k8s_cluster.py`, after the `namespace` column, add:

```python
    argocd_namespace: Mapped[str] = mapped_column(
        String(128), nullable=False, default="argocd", server_default="argocd"
    )
```

- [ ] **Step 2: Write the migration**

Create `backend/migrations/versions/035_cluster_argocd_namespace.py`:

```python
"""Per-cluster ArgoCD control-plane namespace.

Adds custom_k8s_cluster.argocd_namespace (default 'argocd'); llm-d stacks
apply their Application CR into this namespace via the K8s API.

Revision ID: 035_cluster_argocd_namespace
Revises: 034_external_serving
"""

from alembic import op
import sqlalchemy as sa

revision = "035_cluster_argocd_namespace"
down_revision = "034_external_serving"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_k8s_cluster",
        sa.Column("argocd_namespace", sa.String(128), nullable=False, server_default="argocd"),
    )


def downgrade() -> None:
    op.drop_column("custom_k8s_cluster", "argocd_namespace")
```

- [ ] **Step 3: Wire the clusters API**

In `backend/app/api/k8s_clusters.py`:

`CreateClusterRequest` — add field:
```python
    argocd_namespace: str = "argocd"
```
`UpdateClusterRequest` — add field:
```python
    argocd_namespace: str | None = None
```
`_serialize` — add to the returned dict (after `"namespace"`):
```python
        "argocd_namespace": c.argocd_namespace,
```
`create_cluster` — add to the `CustomK8sCluster(...)` kwargs (after `namespace=body.namespace,`):
```python
        argocd_namespace=body.argocd_namespace or "argocd",
```
`update_cluster` — in the block that applies `UpdateClusterRequest` fields, add:
```python
    if body.argocd_namespace is not None:
        cluster.argocd_namespace = body.argocd_namespace
```
(Match the exact style of the neighboring `if body.<field> is not None:` updates already in that handler.)

- [ ] **Step 4: Write the failing test**

Append to `backend/tests/test_k8s_clusters.py`:

```python
from app.api.k8s_clusters import CreateClusterRequest, _serialize as _serialize_cluster


def test_create_cluster_request_defaults_argocd_namespace():
    req = CreateClusterRequest(name="c", context="ctx", kubeconfig="kc")
    assert req.argocd_namespace == "argocd"


def test_cluster_serialize_includes_argocd_namespace():
    c = types.SimpleNamespace(
        id=uuid.uuid4(), name="c", context="ctx", namespace="default",
        argocd_namespace="argo-system", api_server="https://s", is_default=False,
        description=None, default_nfs_server=None, default_nfs_path=None,
        default_nfs_mount_path=None, kubeconfig_encrypted="x",
        created_by=None, created_at=None, updated_at=None,
    )
    assert _serialize_cluster(c)["argocd_namespace"] == "argo-system"
```

- [ ] **Step 5: Run test, apply migration, verify**

```bash
cd /Users/wongibaek/Documents/litellm-ops/backend
.venv/bin/pytest tests/test_k8s_clusters.py -k argocd_namespace -v
APP_DATABASE_URL="postgresql+asyncpg://llmproxy:dbpassword9090@localhost:5432/litellm_portal" .venv/bin/alembic upgrade head
.venv/bin/ruff check app/api/k8s_clusters.py app/db/models/custom_k8s_cluster.py migrations/versions/035_cluster_argocd_namespace.py
```

Expected: tests pass; migration logs `Running upgrade 034_external_serving -> 035_cluster_argocd_namespace`; ruff clean. Verify column: `docker exec litellm_db psql -U llmproxy -d litellm_portal -c "\d custom_k8s_cluster" | grep argocd_namespace`.

- [ ] **Step 6: Commit**

```bash
cd /Users/wongibaek/Documents/litellm-ops
git add backend/app/db/models/custom_k8s_cluster.py backend/migrations/versions/035_cluster_argocd_namespace.py backend/app/api/k8s_clusters.py backend/tests/test_k8s_clusters.py
git commit -m "feat(clusters): per-cluster argocd_namespace field + migration 035"
```

---

### Task 4: Rewrite api/llmd.py for the CRD flow

**Files:**
- Rewrite: `backend/app/api/llmd.py`
- Rewrite: `backend/tests/test_llmd.py`

**Interfaces:**
- Consumes: `K8sClient.apply_application/get_application/delete_application` (Task 1), `build_argo_application(..., argocd_namespace=...)` (Task 2), `argocd_namespace_for` (Task 2), `k8s_for_cluster` (existing).
- Produces: `POST/PUT/DELETE/GET /api/admin/llmd-stacks*` on the CRD path. Create body: `{name, target_model_name, cluster_id?: str|null, namespace, values_yaml}`. `_serialize` returns `cluster_id` (no `argocd_connection_id`). `/resource` endpoint removed.

- [ ] **Step 1: Write the failing tests**

Rewrite `backend/tests/test_llmd.py`:

```python
"""llm-d stack API — ArgoCD CRD provisioning."""

import types
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.api.llmd import _argo_status, _k8s_error_message
from kubernetes_asyncio.client.exceptions import ApiException


def test_argo_status_from_cr_object():
    obj = {"status": {"sync": {"status": "Synced"}, "health": {"status": "Healthy", "message": "ok"}}}
    assert _argo_status(obj) == {"sync_status": "Synced", "health_status": "Healthy", "status_message": "ok"}


def test_argo_status_unknown_when_absent():
    assert _argo_status(None)["sync_status"] == "Unknown"


def test_k8s_error_message_403_hint():
    msg = _k8s_error_message(ApiException(status=403, reason="Forbidden"))
    assert "RBAC" in msg or "permission" in msg.lower()


async def test_create_stack_applies_application(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_none_result())
    fake_k8s = MagicMock()
    fake_k8s.apply_application = AsyncMock()
    fake_k8s.get_application = AsyncMock(return_value=None)
    with patch("app.api.llmd.k8s_for_cluster", AsyncMock(return_value=fake_k8s)), \
         patch("app.api.llmd.argocd_namespace_for", AsyncMock(return_value="argocd")):
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


async def test_create_stack_argocd_rbac_denied_502(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_none_result())
    fake_k8s = MagicMock()
    fake_k8s.apply_application = AsyncMock(side_effect=ApiException(status=403, reason="Forbidden"))
    with patch("app.api.llmd.k8s_for_cluster", AsyncMock(return_value=fake_k8s)), \
         patch("app.api.llmd.argocd_namespace_for", AsyncMock(return_value="argocd")):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/admin/llmd-stacks", json={
                "name": "demo", "target_model_name": "qwen", "namespace": "team-a", "values_yaml": "",
            })
    assert resp.status_code == 502


def _none_result():
    r = MagicMock()
    r.scalar_one_or_none.return_value = None
    r.scalars.return_value.all.return_value = []
    return r
```

- [ ] **Step 2: Run to verify fail**

Run: `cd /Users/wongibaek/Documents/litellm-ops/backend && .venv/bin/pytest tests/test_llmd.py -v`
Expected: FAIL — `_k8s_error_message` import error / current llmd.py still imports the deleted-in-spirit ArgoCD client.

- [ ] **Step 3: Rewrite `backend/app/api/llmd.py`**

Replace the ENTIRE file with:

```python
"""Admin endpoints for llm-d serving stacks (ArgoCD CRD-managed).

The portal renders an argoproj.io Application per stack and applies it to the
cluster's ArgoCD control-plane namespace via the K8s API (using the stack's
registered cluster, or the portal default kubeconfig). ArgoCD's controller
reconciles it; sync/health is read live from the Application CR, never persisted.
"""

import logging
import uuid

import yaml
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kubernetes_asyncio.client.exceptions import ApiException

from app.auth.deps import require_super_user
from app.clients.k8s import K8sNotConfigured
from app.config import settings
from app.db.models.custom_llmd_stack import CustomLlmdStack
from app.db.models.custom_user import CustomUser
from app.db.session import get_db
from app.services.clusters import argocd_namespace_for, k8s_for_cluster
from app.services.llmd_manifests import (
    argo_app_name_for,
    build_argo_application,
    build_llmd_values,
    default_llmd_values,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/llmd-stacks", tags=["llmd-stacks"])


class CreateLlmdStackRequest(BaseModel):
    name: str
    target_model_name: str  # an existing model deployment the router targets
    cluster_id: str | None = None  # registered cluster; None = portal default kubeconfig
    namespace: str = "default"
    values_yaml: str = ""  # full Helm values.yaml the user authored


class UpdateLlmdStackRequest(BaseModel):
    namespace: str | None = None
    values_yaml: str | None = None


class DefaultValuesRequest(BaseModel):
    target_model_name: str = ""


def _parse_values_yaml(text: str) -> dict:
    if not text or not text.strip():
        return {}
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid values YAML: {e}")
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="values.yaml must be a mapping (key: value)")
    return parsed


def _argo_status(obj: dict | None) -> dict:
    """Extract sync/health from an Application CR (Unknown when absent)."""
    if not obj:
        return {"sync_status": "Unknown", "health_status": "Unknown", "status_message": None}
    st = obj.get("status", {}) or {}
    return {
        "sync_status": (st.get("sync") or {}).get("status", "Unknown"),
        "health_status": (st.get("health") or {}).get("status", "Unknown"),
        "status_message": (st.get("health") or {}).get("message"),
    }


def _k8s_error_message(e: Exception) -> str:
    """Human-readable reason a K8s Application op failed, for the UI."""
    if isinstance(e, K8sNotConfigured):
        return "No kubeconfig is configured for this cluster — K8s access is disabled."
    if isinstance(e, ApiException):
        if e.status == 403:
            return "The portal lacks RBAC to manage applications.argoproj.io in the ArgoCD namespace."
        if e.status == 404:
            return "ArgoCD Application CRD or namespace not found — is ArgoCD installed on this cluster?"
        body = (getattr(e, "body", None) or "").strip()
        return body[:600] or f"Kubernetes API returned HTTP {e.status}."
    return str(e) or "Kubernetes request failed."


def _values_for(stack: CustomLlmdStack) -> dict:
    return build_llmd_values(
        stack,
        epp_registry=settings.llmd_epp_image_registry,
        epp_repository=settings.llmd_epp_image_repository,
        epp_tag=settings.llmd_epp_image_tag,
    )


def _application_for(stack: CustomLlmdStack, argocd_namespace: str) -> dict:
    return build_argo_application(
        stack,
        chart_repo=settings.llmd_chart_repo,
        chart_name=settings.llmd_chart_name,
        chart_version=settings.llmd_chart_version,
        values=stack.values_snapshot,
        project=settings.argo_project,
        argocd_namespace=argocd_namespace,
    )


def _require_valid_name(name: str) -> str:
    if not name or not name.strip():
        raise HTTPException(status_code=400, detail="Stack name is required.")
    app_name = argo_app_name_for(name)
    if app_name in ("llmd-", "llmd"):
        raise HTTPException(
            status_code=400,
            detail="Stack name must contain letters or digits (a–z, 0–9, hyphen).",
        )
    if len(app_name) > 53:
        raise HTTPException(
            status_code=400,
            detail=f"Stack name is too long — the resulting app name '{app_name}' exceeds 53 characters.",
        )
    return app_name


async def _live_status(db: AsyncSession, stack: CustomLlmdStack) -> dict:
    try:
        argocd_ns = await argocd_namespace_for(db, stack.cluster_id)
        k8s = await k8s_for_cluster(db, stack.cluster_id)
        obj = await k8s.get_application(argocd_ns, stack.argo_app_name)
        return _argo_status(obj)
    except Exception as e:  # noqa: BLE001 — status is best-effort
        logger.info("llm-d status read failed for %s: %s", stack.name, e)
        return _argo_status(None)


def _serialize(stack: CustomLlmdStack, status_fields: dict) -> dict:
    return {
        "id": str(stack.id),
        "name": stack.name,
        "target_model_name": stack.target_model_name,
        "cluster_id": str(stack.cluster_id) if stack.cluster_id else None,
        "namespace": stack.namespace,
        "argo_app_name": stack.argo_app_name,
        "chart_repo": settings.llmd_chart_repo,
        "chart_name": settings.llmd_chart_name,
        "chart_version": settings.llmd_chart_version,
        "epp_image": f"{settings.llmd_epp_image_registry}/{settings.llmd_epp_image_repository}:{settings.llmd_epp_image_tag}",
        "helm_values": stack.helm_values,
        "values_yaml": yaml.safe_dump(stack.helm_values, sort_keys=False, default_flow_style=False) if stack.helm_values else "",
        "created_by": stack.created_by,
        "created_at": stack.created_at.isoformat() if stack.created_at else None,
        "updated_at": stack.updated_at.isoformat() if stack.updated_at else None,
        **status_fields,
    }


@router.get("")
async def list_stacks(
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    rows = (await db.execute(select(CustomLlmdStack).order_by(CustomLlmdStack.created_at.desc()))).scalars().all()
    return {"stacks": [_serialize(s, await _live_status(db, s)) for s in rows]}


@router.get("/{stack_id}/applied")
async def applied_values(
    stack_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Effective rendered values + live ArgoCD state (applied valuesObject and
    deployed resources from the Application CR status). Live fields are
    best-effort: null/empty when the cluster is unreachable or unsynced."""
    stack = (
        await db.execute(select(CustomLlmdStack).where(CustomLlmdStack.id == uuid.UUID(stack_id)))
    ).scalar_one_or_none()
    if stack is None:
        raise HTTPException(status_code=404, detail="Stack not found")

    live_values: dict | None = None
    resources: list[dict] = []
    revision: str | None = None
    live_error: str | None = None
    try:
        argocd_ns = await argocd_namespace_for(db, stack.cluster_id)
        k8s = await k8s_for_cluster(db, stack.cluster_id)
        obj = await k8s.get_application(argocd_ns, stack.argo_app_name)
        if obj:
            src = (obj.get("spec") or {}).get("source") or {}
            live_values = (src.get("helm") or {}).get("valuesObject")
            st = obj.get("status") or {}
            revision = (st.get("sync") or {}).get("revision")
            resources = [
                {
                    "group": r.get("group") or "",
                    "version": r.get("version") or "v1",
                    "kind": r.get("kind"),
                    "name": r.get("name"),
                    "namespace": r.get("namespace"),
                    "status": r.get("status"),
                    "health": (r.get("health") or {}).get("status"),
                }
                for r in (st.get("resources") or [])
            ]
        else:
            live_error = "The ArgoCD Application was not found — it may have been deleted."
    except Exception as e:  # noqa: BLE001 — live state is best-effort
        logger.info("llm-d applied read failed for %s: %s", stack.name, e)
        live_error = _k8s_error_message(e)

    return {
        "effective_values": stack.values_snapshot,
        "live_values": live_values,
        "resources": resources,
        "revision": revision,
        "live_error": live_error,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_stack(
    body: CreateLlmdStackRequest,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    app_name = _require_valid_name(body.name)
    if not body.namespace or not body.namespace.strip():
        raise HTTPException(status_code=400, detail="Namespace is required.")
    if (await db.execute(select(CustomLlmdStack).where(CustomLlmdStack.name == body.name))).scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Stack '{body.name}' already exists")

    helm_values = _parse_values_yaml(body.values_yaml) or default_llmd_values(
        body.target_model_name,
        epp_registry=settings.llmd_epp_image_registry,
        epp_repository=settings.llmd_epp_image_repository,
        epp_tag=settings.llmd_epp_image_tag,
    )
    stack = CustomLlmdStack(
        id=uuid.uuid4(),
        name=body.name,
        target_model_name=body.target_model_name,
        cluster_id=uuid.UUID(body.cluster_id) if body.cluster_id else None,
        namespace=body.namespace,
        argo_app_name=app_name,
        helm_values=helm_values,
        values_snapshot={},
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    stack.values_snapshot = _values_for(stack)
    db.add(stack)
    await db.flush()

    try:
        argocd_ns = await argocd_namespace_for(db, stack.cluster_id)
        k8s = await k8s_for_cluster(db, stack.cluster_id)
        await k8s.apply_application(argocd_ns, _application_for(stack, argocd_ns))
    except Exception as e:
        logger.exception("ArgoCD Application apply failed for stack %s", stack.name)
        raise HTTPException(status_code=502, detail=f"ArgoCD apply failed: {_k8s_error_message(e)}")
    await db.commit()
    await db.refresh(stack)
    return _serialize(stack, await _live_status(db, stack))


@router.post("/default-values")
async def default_values(
    body: DefaultValuesRequest,
    user: CustomUser = Depends(require_super_user),
) -> dict:
    values = default_llmd_values(
        body.target_model_name,
        epp_registry=settings.llmd_epp_image_registry,
        epp_repository=settings.llmd_epp_image_repository,
        epp_tag=settings.llmd_epp_image_tag,
    )
    return {
        "values": values,
        "values_yaml": yaml.safe_dump(values, sort_keys=False, default_flow_style=False),
    }


@router.put("/{stack_id}")
async def update_stack(
    stack_id: str,
    body: UpdateLlmdStackRequest,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    stack = (
        await db.execute(select(CustomLlmdStack).where(CustomLlmdStack.id == uuid.UUID(stack_id)))
    ).scalar_one_or_none()
    if not stack:
        raise HTTPException(status_code=404, detail="Stack not found")

    if body.namespace is not None:
        stack.namespace = body.namespace
    if body.values_yaml is not None:
        stack.helm_values = _parse_values_yaml(body.values_yaml)
    stack.values_snapshot = _values_for(stack)
    stack.updated_by = user.user_id
    await db.flush()

    try:
        argocd_ns = await argocd_namespace_for(db, stack.cluster_id)
        k8s = await k8s_for_cluster(db, stack.cluster_id)
        await k8s.apply_application(argocd_ns, _application_for(stack, argocd_ns))
    except Exception as e:
        logger.exception("ArgoCD Application update failed for stack %s", stack.name)
        raise HTTPException(status_code=502, detail=f"ArgoCD update failed: {_k8s_error_message(e)}")
    await db.commit()
    await db.refresh(stack)
    return _serialize(stack, await _live_status(db, stack))


@router.delete("/{stack_id}")
async def delete_stack(
    stack_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    stack = (
        await db.execute(select(CustomLlmdStack).where(CustomLlmdStack.id == uuid.UUID(stack_id)))
    ).scalar_one_or_none()
    if not stack:
        raise HTTPException(status_code=404, detail="Stack not found")
    try:
        argocd_ns = await argocd_namespace_for(db, stack.cluster_id)
        k8s = await k8s_for_cluster(db, stack.cluster_id)
        await k8s.delete_application(argocd_ns, stack.argo_app_name)
    except Exception as e:
        logger.exception("ArgoCD Application delete failed for stack %s", stack.name)
        raise HTTPException(status_code=502, detail=f"ArgoCD delete failed: {_k8s_error_message(e)}")
    await db.delete(stack)
    await db.commit()
    return {"ok": True}
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/wongibaek/Documents/litellm-ops/backend && .venv/bin/pytest tests/test_llmd.py tests/test_llmd_k8s.py -v && .venv/bin/ruff check app/api/llmd.py tests/test_llmd.py`
Expected: all pass; ruff clean. (`CustomLlmdStack` still has the `argocd_connection_id` attribute/column here — harmless; removed in Task 5.)

- [ ] **Step 5: Commit**

```bash
cd /Users/wongibaek/Documents/litellm-ops
git add backend/app/api/llmd.py backend/tests/test_llmd.py
git commit -m "feat(llmd): provision via ArgoCD CRD (apply Application through k8s_for_cluster)"
```

---

### Task 5: Remove the ArgoCD connection registry + migration 036

**Files:**
- Delete: `backend/app/clients/argocd.py`, `backend/app/api/argocd_connections.py`, `backend/app/db/models/custom_argocd_connection.py`, `backend/tests/test_argocd.py`
- Modify: `backend/app/db/models/__init__.py`, `backend/app/main.py`, `backend/app/db/models/custom_llmd_stack.py`
- Create: `backend/migrations/versions/036_drop_argocd_connection.py`

**Interfaces:** none produced; removes dead surface. After this task `CustomLlmdStack` has no `argocd_connection_id`.

- [ ] **Step 1: Delete the files**

```bash
cd /Users/wongibaek/Documents/litellm-ops
git rm backend/app/clients/argocd.py backend/app/api/argocd_connections.py \
  backend/app/db/models/custom_argocd_connection.py backend/tests/test_argocd.py
```

- [ ] **Step 2: Remove references**

In `backend/app/db/models/__init__.py`: delete the `from app.db.models.custom_argocd_connection import CustomArgocdConnection` import line and the `"CustomArgocdConnection",` entry in `__all__`.

In `backend/app/main.py`: delete the `argocd_connections,` name from the api import block and the `app.include_router(argocd_connections.router)` line.

In `backend/app/db/models/custom_llmd_stack.py`: delete the entire `argocd_connection_id` mapped column block (the comment `# Which registered ArgoCD connection...` plus the `argocd_connection_id: Mapped[uuid.UUID | None] = mapped_column(...)` statement). If `ForeignKey` becomes unused after this, keep it — `cluster_id` still uses it.

- [ ] **Step 3: Write the drop migration**

Create `backend/migrations/versions/036_drop_argocd_connection.py`:

```python
"""Drop the ArgoCD connection registry (llm-d now provisions via the K8s API).

Removes custom_llmd_stack.argocd_connection_id and the custom_argocd_connection
table. Existing stacks keep their cluster_id (null = portal default kubeconfig)
and argo_app_name, so a CRD apply adopts their already-existing Application CRs.

Revision ID: 036_drop_argocd_connection
Revises: 035_cluster_argocd_namespace
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "036_drop_argocd_connection"
down_revision = "035_cluster_argocd_namespace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("custom_llmd_stack", "argocd_connection_id")
    op.drop_table("custom_argocd_connection")


def downgrade() -> None:
    op.create_table(
        "custom_argocd_connection",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("server_url", sa.String(512), nullable=False),
        sa.Column("token_encrypted", sa.Text(), nullable=False),
        sa.Column("insecure_skip_verify", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("updated_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.add_column(
        "custom_llmd_stack",
        sa.Column("argocd_connection_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
```

- [ ] **Step 4: Apply migration + verify import graph + full suite**

```bash
cd /Users/wongibaek/Documents/litellm-ops/backend
APP_DATABASE_URL="postgresql+asyncpg://llmproxy:dbpassword9090@localhost:5432/litellm_portal" .venv/bin/alembic upgrade head
.venv/bin/python -c "import app.main; print('import ok')"
.venv/bin/pytest -q
.venv/bin/ruff check app/ tests/
```

Expected: migration logs `035 -> 036`; `import ok` (proves no dangling `CustomArgocdConnection` / `argocd_connections` references); full suite has NO new failures vs the pre-existing baseline (the deleted `test_argocd.py` is gone); ruff clean. Verify: `docker exec litellm_db psql -U llmproxy -d litellm_portal -tc "SELECT count(*) FROM custom_llmd_stack;"` still returns 4 (stacks preserved) and `\dt custom_argocd_connection` shows no such table.

- [ ] **Step 5: Commit**

```bash
cd /Users/wongibaek/Documents/litellm-ops
git add -A backend/
git commit -m "refactor(llmd): remove ArgoCD REST connection registry + migration 036"
```

---

### Task 6: Frontend — cluster argocd_namespace field, remove connections tab

**Files:**
- Modify: `frontend/src/components/cluster-settings-tab.tsx`
- Delete: `frontend/src/components/argocd-settings-tab.tsx`
- Modify: `frontend/src/app/(app)/admin/settings/page.tsx`
- Modify: `frontend/src/hooks/use-api.ts`
- Modify: `frontend/messages/en.json`, `frontend/messages/ko.json`

**Interfaces:** the cluster create/edit form sends and displays `argocd_namespace`.

- [ ] **Step 1: use-api — cluster type + drop ArgoCD hooks**

In `frontend/src/hooks/use-api.ts`:
- Add `argocd_namespace: string;` to the `K8sClusterSummary` interface (find it near `useK8sClusters`) and add `argocd_namespace?: string;` to the cluster create/update body types.
- Delete the entire `// ─── ArgoCD connections ───` section (interfaces `CreateArgocdConnectionBody`, `UpdateArgocdConnectionBody`, and hooks `useArgocdConnections`, `useCreateArgocdConnection`, `useUpdateArgocdConnection`, `useDeleteArgocdConnection`, `useTestArgocdConnection` — whatever exists there, lines ~1383+).
- Remove the now-unused `ArgocdConnectionSummary`, `ArgocdTestResult` type imports at the top (lines ~42-43).

- [ ] **Step 2: cluster-settings-tab — add the field**

In `frontend/src/components/cluster-settings-tab.tsx`, add an "ArgoCD namespace" text input to the create/edit cluster form, next to the existing `namespace` field, bound to the form's `argocd_namespace` state (default `"argocd"`), and include `argocd_namespace` in the create/update mutation body. Match the existing field's Label/Input pattern in that file. Add the label via `t("argocdNamespaceLabel")` and helper `t("argocdNamespaceHint")`.

- [ ] **Step 3: settings page — drop the ArgoCD tab**

In `frontend/src/app/(app)/admin/settings/page.tsx`: remove `import { ArgocdSettingsTab } from "@/components/argocd-settings-tab";`, the `<TabsTrigger value="argocd">{t("tabArgocd")}</TabsTrigger>`, and the matching `<TabsContent value="argocd"><ArgocdSettingsTab/></TabsContent>`. Then delete the file:

```bash
git rm frontend/src/components/argocd-settings-tab.tsx
```

- [ ] **Step 4: i18n**

In BOTH `frontend/messages/en.json` and `frontend/messages/ko.json`, in the cluster-settings message group add `argocdNamespaceLabel` / `argocdNamespaceHint`; and remove the `tabArgocd` key + any `argocd*` connection strings that are now unreferenced. en values: `"argocdNamespaceLabel": "ArgoCD namespace"`, `"argocdNamespaceHint": "Namespace where this cluster's ArgoCD watches Application resources (default: argocd)."`. ko values: `"argocdNamespaceLabel": "ArgoCD 네임스페이스"`, `"argocdNamespaceHint": "이 클러스터의 ArgoCD가 Application 리소스를 watch하는 네임스페이스 (기본: argocd)."`.

- [ ] **Step 5: Gates + commit**

```bash
cd /Users/wongibaek/Documents/litellm-ops/frontend
python3 -c "import json; json.load(open('messages/en.json')); json.load(open('messages/ko.json')); print('json ok')"
npx tsc --noEmit && echo TSC_OK
npm run lint 2>&1 | tail -2
cd /Users/wongibaek/Documents/litellm-ops
git add -A frontend/
git commit -m "feat(frontend): cluster argocd namespace field; remove ArgoCD connections tab"
```

Expected: json ok; TSC_OK (0 errors — any dangling reference to the removed hooks/types is a real error to fix); lint at the 4-error pre-existing baseline.

---

### Task 7: Frontend — llm-d forms use cluster, drop resource view

**Files:**
- Modify: `frontend/src/app/(app)/admin/llmd/new/page.tsx`
- Modify: `frontend/src/app/(app)/admin/llmd/[id]/page.tsx`
- Modify: `frontend/messages/en.json`, `frontend/messages/ko.json`

**Interfaces:** create posts `cluster_id` instead of `argocd_connection_id`; detail page no longer calls `/resource`.

- [ ] **Step 1: new stack form — connection → cluster**

In `frontend/src/app/(app)/admin/llmd/new/page.tsx`: replace the ArgoCD-connection `<Select>` (fed by `useArgocdConnections`) with a cluster `<Select>` fed by `useK8sClusters` (same hook the serving/benchmark forms use). Include a "Portal default" option mapping to `cluster_id: null`. Change the create mutation body field from `argocd_connection_id` to `cluster_id`. Remove the now-unused `useArgocdConnections` import. Use `t("clusterLabel")` (add if absent) for the field label.

- [ ] **Step 2: detail page — drop resource-manifest view + connection display**

In `frontend/src/app/(app)/admin/llmd/[id]/page.tsx`: remove the UI + fetch that calls `GET /api/admin/llmd-stacks/{id}/resource` (the per-resource live-manifest viewer). Keep the applied-resources list (from `/applied` — `resources[]` array) as a read-only table. Replace any "ArgoCD connection" display with the cluster name (from the stack's `cluster_id` resolved against `useK8sClusters`, or "Portal default" when null). Remove unused imports/hooks (`useArgocdConnections`, any resource-manifest hook).

- [ ] **Step 3: i18n**

Remove llm-d connection strings that are now unreferenced; add `clusterLabel` (en `"Cluster"`, ko `"클러스터"`) and, if the detail page shows a default label, `clusterDefault` (en `"Portal default"`, ko `"포털 기본"`) in both message files under the llm-d group.

- [ ] **Step 4: Gates + commit**

```bash
cd /Users/wongibaek/Documents/litellm-ops/frontend
python3 -c "import json; json.load(open('messages/en.json')); json.load(open('messages/ko.json')); print('json ok')"
npx tsc --noEmit && echo TSC_OK
npm run lint 2>&1 | tail -2
cd /Users/wongibaek/Documents/litellm-ops
git add -A frontend/
git commit -m "feat(frontend): llm-d stack targets a registered cluster; drop resource-manifest view"
```

Expected: json ok; TSC_OK; lint baseline unchanged.

---

### Task 8: RBAC + full verification

**Files:**
- Modify: `deploy/rbac/portal-k8s-rbac.yaml`

- [ ] **Step 1: RBAC — add the Application rule + fix the note**

In `deploy/rbac/portal-k8s-rbac.yaml`:
- Update the header comment: the note stating llm-d goes through ArgoCD's REST API so "NO argoproj.io ... verbs are needed" is now false — replace it with a line saying the portal applies `applications.argoproj.io` directly (CRD-based), scoped to the ArgoCD namespace.
- Add a rule under the ClusterRole `rules:`:

```yaml
  # --- llm-d stacks: manage ArgoCD Application CRs (CRD-based provisioning) ---
  - apiGroups: ["argoproj.io"]
    resources: ["applications"]
    verbs: ["get", "list", "create", "update", "patch", "delete"]
```

- [ ] **Step 2: Full backend suite + import**

```bash
cd /Users/wongibaek/Documents/litellm-ops/backend
.venv/bin/python -c "import app.main; print('import ok')"
.venv/bin/pytest -q 2>&1 | tail -3
.venv/bin/ruff check app/ tests/
```

Expected: import ok; no NEW failures vs baseline; ruff clean.

- [ ] **Step 3: Frontend build**

```bash
cd /Users/wongibaek/Documents/litellm-ops/frontend && npm run build 2>&1 | tail -5
```

Expected: build succeeds.

- [ ] **Step 4: Rebuild + smoke the running stack**

```bash
cd /Users/wongibaek/Documents/litellm-ops
docker compose up -d --build backend backend-worker frontend
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8002/api/admin/llmd-stacks
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8002/api/admin/argocd-connections
```

Expected: backend healthy; `/llmd-stacks` → `401` (route resolves, auth required); `/argocd-connections` → `404` (route removed). Then browse http://localhost:3003/admin/settings — the ArgoCD tab is gone, the cluster form has an "ArgoCD namespace" field; http://localhost:3003/admin/llmd/new shows a cluster selector.

- [ ] **Step 5: Commit + finish**

```bash
cd /Users/wongibaek/Documents/litellm-ops
git add deploy/rbac/portal-k8s-rbac.yaml
git commit -m "chore(rbac): grant applications.argoproj.io for CRD-based llm-d"
```

Then use superpowers:finishing-a-development-branch.

**Operational note (call out to the user, not a code step):** the 4 existing stacks have `cluster_id = null`, so after migration they resolve to the portal default kubeconfig + global `APP_ARGOCD_NAMESPACE`. For their Application CRs to keep being managed, the portal's default kubeconfig must reach the same cluster/namespace where ArgoCD already holds those Applications. If a stack's ArgoCD lives on a registered cluster instead, edit the stack (or set its `cluster_id`) so it points there.
