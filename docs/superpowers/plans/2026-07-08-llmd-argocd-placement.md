# llm-d ArgoCD Placement Per Cluster Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-cluster configuration of where an llm-d stack's ArgoCD Application CR is applied (which cluster's ArgoCD manages it) and what `spec.destination.server` it targets.

**Architecture:** Two nullable columns on `custom_k8s_cluster` (migration 037). A new `argocd_placement_for(db, cluster_id) -> (K8sClient, argocd_namespace, destination_server)` helper resolves them (one hop, all-local fallbacks); the five llmd.py handler sites use it and `build_argo_application` gains a required `destination_server` kwarg. Cluster CRUD API + settings dialog expose the fields. Spec: `docs/superpowers/specs/2026-07-08-llmd-argocd-placement-design.md`.

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic; kubernetes_asyncio; Next.js + next-intl.

## Global Constraints

- NULL semantics preserve today's behavior exactly: host NULL = self-managed; dest NULL = `https://kubernetes.default.svc`. Existing rows/stacks unchanged.
- Resolution is ONE HOP: the host's own `argocd_host_cluster_id` is ignored; a dangling host id falls back to the target itself.
- Host validation (400): must parse as UUID, must reference an existing cluster, must not equal the cluster's own id. Empty string clears either field on update (NFS-fields sentinel style).
- `argocd_namespace_for` is DELETED in Task 3 (llmd.py is its only consumer — verify with grep before deleting). `k8s_for_cluster` keeps its exact public behavior.
- Backend gates: `cd backend && .venv/bin/python -m pytest tests/ -q` 0 NEW failures (baseline 21 pre-existing); `.venv/bin/ruff check app/ tests/` 0 NEW (baseline 78). Frontend gates (Task 5): lint 0 NEW (baseline 4 errors/13 warnings), `npm run build` passes.
- Work on branch `feat/llmd-argocd-placement` (already checked out).
- Commit messages end with: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

---

### Task 1: Migration 037 + model columns

**Files:**
- Create: `backend/migrations/versions/037_cluster_argocd_placement.py`
- Modify: `backend/app/db/models/custom_k8s_cluster.py`

**Interfaces:**
- Produces: ORM attributes `CustomK8sCluster.argocd_host_cluster_id: uuid.UUID | None` and `CustomK8sCluster.argocd_dest_server: str | None`, consumed by Tasks 2–4.

- [ ] **Step 1: Write the migration**

Create `backend/migrations/versions/037_cluster_argocd_placement.py`:

```python
"""Per-cluster ArgoCD placement for llm-d stacks.

argocd_host_cluster_id: the cluster whose ArgoCD manages this one (NULL =
itself); the Application CR is applied there. argocd_dest_server: the
Application's spec.destination.server (NULL = https://kubernetes.default.svc).

Revision ID: 037_cluster_argocd_placement
Revises: 036_drop_argocd_connection
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "037_cluster_argocd_placement"
down_revision = "036_drop_argocd_connection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_k8s_cluster",
        sa.Column("argocd_host_cluster_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "custom_k8s_cluster",
        sa.Column("argocd_dest_server", sa.String(512), nullable=True),
    )
    op.create_foreign_key(
        "fk_k8s_cluster_argocd_host",
        "custom_k8s_cluster",
        "custom_k8s_cluster",
        ["argocd_host_cluster_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_k8s_cluster_argocd_host", "custom_k8s_cluster", type_="foreignkey")
    op.drop_column("custom_k8s_cluster", "argocd_dest_server")
    op.drop_column("custom_k8s_cluster", "argocd_host_cluster_id")
```

Before committing, confirm the exact `down_revision` string matches the `revision` value inside `backend/migrations/versions/036_drop_argocd_connection.py` (open it and copy verbatim; adjust if it differs from `036_drop_argocd_connection`).

- [ ] **Step 2: Add the ORM columns**

In `backend/app/db/models/custom_k8s_cluster.py`, extend the import line

```python
from sqlalchemy import Boolean, DateTime, String, Text, func
```

to

```python
from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
```

and insert directly after the `argocd_namespace` column (line 30):

```python
    # llm-d ArgoCD placement: which cluster's ArgoCD manages this one (NULL =
    # itself) and the destination.server URL that ArgoCD knows this cluster by
    # (NULL = the in-cluster default). One-hop resolution; see services/clusters.
    argocd_host_cluster_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("custom_k8s_cluster.id", ondelete="SET NULL"),
        nullable=True,
    )
    argocd_dest_server: Mapped[str | None] = mapped_column(String(512), nullable=True)
```

- [ ] **Step 3: Apply the migration to the local docker DB and verify**

```bash
cd backend && APP_DATABASE_URL="postgresql+asyncpg://llmproxy:dbpassword9090@localhost:5432/litellm_portal" .venv/bin/alembic upgrade head
cd backend && APP_DATABASE_URL="postgresql+asyncpg://llmproxy:dbpassword9090@localhost:5432/litellm_portal" .venv/bin/alembic current
```

Expected: upgrade runs `036… -> 037_cluster_argocd_placement`, and `current` prints `037_cluster_argocd_placement (head)`.

- [ ] **Step 4: Backend gates**

```bash
cd backend && .venv/bin/python -m pytest tests/ -q 2>&1 | tail -3
cd backend && .venv/bin/ruff check app/ tests/ 2>&1 | tail -2
```

Expected: same failure count as baseline (21 pre-existing, 0 new — record the exact tail line), ruff 78 (0 new).

- [ ] **Step 5: Commit**

```bash
git add backend/migrations/versions/037_cluster_argocd_placement.py backend/app/db/models/custom_k8s_cluster.py
git commit -m "feat(db): per-cluster ArgoCD host + destination server columns

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: `argocd_placement_for` resolver

**Files:**
- Modify: `backend/app/services/clusters.py`
- Create: `backend/tests/test_cluster_placement.py`

**Interfaces:**
- Consumes: Task 1's ORM columns.
- Produces: `LOCAL_DEST_SERVER: str` and `async argocd_placement_for(db, cluster_id: uuid.UUID | str | None) -> tuple[K8sClient, str, str]` in `app.services.clusters` — Task 3 imports both. `argocd_namespace_for` and `k8s_for_cluster` keep working unchanged in this task (Task 3 deletes the former).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_cluster_placement.py`:

```python
"""argocd_placement_for — where a stack's Application CR goes."""

import types
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.clusters import LOCAL_DEST_SERVER, argocd_placement_for

HOST_ID = uuid.uuid4()
TARGET_ID = uuid.uuid4()


def _row(**kw):
    base = dict(
        id=TARGET_ID,
        context="ctx",
        kubeconfig_encrypted="enc",
        argocd_namespace="argocd",
        argocd_host_cluster_id=None,
        argocd_dest_server=None,
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def _db_returning(rows_by_id):
    """Fake async db: execute() resolves scalar_one_or_none by the id the
    select's ``.where(CustomK8sCluster.id == <id>)`` clause binds."""

    async def execute(stmt):
        wanted = stmt.whereclause.right.value
        r = MagicMock()
        r.scalar_one_or_none.return_value = rows_by_id.get(wanted)
        return r

    db = MagicMock()
    db.execute = AsyncMock(side_effect=execute)
    return db


def _patched():
    fake_client = MagicMock(name="K8sClient")
    p_client = patch("app.services.clusters.K8sClient", return_value=fake_client)
    p_crypto = patch("app.services.clusters.crypto.decrypt", return_value="apiVersion: v1")
    p_yaml = patch("app.services.clusters.yaml.safe_load", return_value={"kind": "Config"})
    return fake_client, p_client, p_crypto, p_yaml


async def test_null_cluster_all_local_defaults():
    fake_client, p_client, p_crypto, p_yaml = _patched()
    with p_client, p_crypto, p_yaml, patch("app.config.settings") as s:
        s.argocd_namespace = "argocd-global"
        k8s, ns, dest = await argocd_placement_for(_db_returning({}), None)
    assert k8s is fake_client
    assert ns == "argocd-global"
    assert dest == LOCAL_DEST_SERVER


async def test_self_managed_cluster_uses_own_row():
    target = _row(argocd_namespace="argo-sys")
    fake_client, p_client, p_crypto, p_yaml = _patched()
    with p_client, p_crypto, p_yaml:
        k8s, ns, dest = await argocd_placement_for(_db_returning({TARGET_ID: target}), TARGET_ID)
    assert k8s is fake_client
    assert ns == "argo-sys"
    assert dest == LOCAL_DEST_SERVER


async def test_dest_server_from_target_row():
    target = _row(argocd_dest_server="https://10.9.9.9:6443")
    _c, p_client, p_crypto, p_yaml = _patched()
    with p_client, p_crypto, p_yaml:
        _k8s, _ns, dest = await argocd_placement_for(_db_returning({TARGET_ID: target}), TARGET_ID)
    assert dest == "https://10.9.9.9:6443"


async def test_host_cluster_supplies_client_and_namespace():
    host = _row(id=HOST_ID, context="host-ctx", argocd_namespace="argo-central")
    target = _row(argocd_host_cluster_id=HOST_ID, argocd_dest_server="https://t:6443")
    _c, p_client, p_crypto, p_yaml = _patched()
    with p_client as client_cls, p_crypto, p_yaml:
        _k8s, ns, dest = await argocd_placement_for(
            _db_returning({TARGET_ID: target, HOST_ID: host}), str(TARGET_ID)
        )
    assert ns == "argo-central"
    assert dest == "https://t:6443"
    assert client_cls.call_args.kwargs["context"] == "host-ctx"


async def test_dangling_host_falls_back_to_target_itself():
    target = _row(argocd_host_cluster_id=HOST_ID, argocd_namespace="argo-t")
    _c, p_client, p_crypto, p_yaml = _patched()
    with p_client as client_cls, p_crypto, p_yaml:
        _k8s, ns, _dest = await argocd_placement_for(_db_returning({TARGET_ID: target}), TARGET_ID)
    assert ns == "argo-t"
    assert client_cls.call_args.kwargs["context"] == "ctx"


async def test_unknown_cluster_id_falls_back_to_defaults():
    _c, p_client, p_crypto, p_yaml = _patched()
    with p_client, p_crypto, p_yaml, patch("app.config.settings") as s:
        s.argocd_namespace = "argocd"
        _k8s, ns, dest = await argocd_placement_for(_db_returning({}), uuid.uuid4())
    assert ns == "argocd"
    assert dest == LOCAL_DEST_SERVER
```

Note: `patch("app.config.settings")` works because `argocd_placement_for` imports settings lazily inside the function (same style as `argocd_namespace_for` today). The two tests that don't patch settings never reach the settings fallback (their rows carry `argocd_namespace`).

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && .venv/bin/python -m pytest tests/test_cluster_placement.py -q
```

Expected: ImportError — `LOCAL_DEST_SERVER`/`argocd_placement_for` don't exist yet.

- [ ] **Step 3: Implement the resolver**

In `backend/app/services/clusters.py`, add after the imports:

```python
LOCAL_DEST_SERVER = "https://kubernetes.default.svc"
```

Refactor `k8s_for_cluster`'s row lookup/client construction into shared helpers and add the resolver — the file's full new content below `LOCAL_DEST_SERVER` (keep the module docstring and imports as-is):

```python
async def _cluster_row(db: AsyncSession, cluster_id: uuid.UUID) -> CustomK8sCluster | None:
    return (
        await db.execute(select(CustomK8sCluster).where(CustomK8sCluster.id == cluster_id))
    ).scalar_one_or_none()


def _client_for_row(row: CustomK8sCluster) -> K8sClient:
    kubeconfig = yaml.safe_load(crypto.decrypt(row.kubeconfig_encrypted))
    return K8sClient(kubeconfig=kubeconfig, context=row.context)


async def k8s_for_cluster(db: AsyncSession, cluster_id: uuid.UUID | str | None) -> K8sClient:
    """Return a K8sClient bound to the registered cluster.

    ``cluster_id`` of None uses the portal's mounted kubeconfig (the default,
    backward-compatible behaviour). A cluster_id that no longer resolves also
    falls back to the default rather than failing the reconciler.
    """
    if not cluster_id:
        return K8sClient()
    cid = cluster_id if isinstance(cluster_id, uuid.UUID) else uuid.UUID(str(cluster_id))
    row = await _cluster_row(db, cid)
    if row is None:
        return K8sClient()
    return _client_for_row(row)


async def argocd_namespace_for(db: AsyncSession, cluster_id: uuid.UUID | str | None) -> str:
    """The ArgoCD control-plane namespace for a stack's cluster.

    A registered cluster's ``argocd_namespace`` wins; a null cluster (portal
    default kubeconfig) falls back to the global ``settings.argocd_namespace``.
    """
    from app.config import settings

    if not cluster_id:
        return settings.argocd_namespace
    cid = cluster_id if isinstance(cluster_id, uuid.UUID) else uuid.UUID(str(cluster_id))
    row = await _cluster_row(db, cid)
    return (row.argocd_namespace if row and row.argocd_namespace else settings.argocd_namespace)


async def argocd_placement_for(
    db: AsyncSession, cluster_id: uuid.UUID | str | None
) -> tuple[K8sClient, str, str]:
    """Where a stack's Application CR goes and what its destination points at.

    Returns (K8s client to apply the CR with, ArgoCD control-plane namespace,
    ``spec.destination.server``). The target cluster's ``argocd_host_cluster_id``
    names the cluster whose ArgoCD manages it (one hop only; NULL = itself),
    and ``argocd_dest_server`` is the server URL that ArgoCD registers the
    target under (NULL = the in-cluster default). A null or unresolvable
    cluster keeps the portal-default, all-local behaviour; a dangling host id
    falls back to the target itself.
    """
    from app.config import settings

    if not cluster_id:
        return K8sClient(), settings.argocd_namespace, LOCAL_DEST_SERVER
    cid = cluster_id if isinstance(cluster_id, uuid.UUID) else uuid.UUID(str(cluster_id))
    target = await _cluster_row(db, cid)
    if target is None:
        return K8sClient(), settings.argocd_namespace, LOCAL_DEST_SERVER
    dest = target.argocd_dest_server or LOCAL_DEST_SERVER
    host = target
    if target.argocd_host_cluster_id:
        host = await _cluster_row(db, target.argocd_host_cluster_id) or target
    ns = host.argocd_namespace or settings.argocd_namespace
    return _client_for_row(host), ns, dest
```

(`argocd_namespace_for` is kept byte-compatible here; Task 3 deletes it.)

- [ ] **Step 4: Run tests to verify they pass, then gates**

```bash
cd backend && .venv/bin/python -m pytest tests/test_cluster_placement.py -q
cd backend && .venv/bin/python -m pytest tests/ -q 2>&1 | tail -3
cd backend && .venv/bin/ruff check app/ tests/ 2>&1 | tail -2
```

Expected: 6/6 pass; suite baseline unchanged (0 new failures); ruff 78 (0 new).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/clusters.py backend/tests/test_cluster_placement.py
git commit -m "feat(clusters): argocd_placement_for resolves host cluster + destination

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Manifest destination + llmd handlers on the resolver

**Files:**
- Modify: `backend/app/services/llmd_manifests.py` (build_argo_application, lines 88–126)
- Modify: `backend/app/api/llmd.py` (imports; `_application_for`; the five handler sites at lines 134–142, 201–204, 271–277, 322–328, 345–351)
- Modify: `backend/app/services/clusters.py` (delete `argocd_namespace_for`)
- Modify: `backend/tests/test_llmd_manifests.py`, `backend/tests/test_llmd.py`

**Interfaces:**
- Consumes: `argocd_placement_for` + `LOCAL_DEST_SERVER` from Task 2.
- Produces: `build_argo_application(stack, *, chart_repo, chart_name, chart_version, values, project, argocd_namespace, destination_server)` — new REQUIRED kwarg; `_application_for(stack, argocd_namespace, destination_server)`.

- [ ] **Step 1: Write the failing manifest test**

In `backend/tests/test_llmd_manifests.py`, add (and pass `destination_server="https://kubernetes.default.svc"` to every existing `build_argo_application(...)` call in the file so it keeps compiling):

```python
def test_application_destination_server_configurable():
    stack = _stack()  # reuse the file's existing stack fixture/helper
    app = build_argo_application(
        stack,
        chart_repo="oci://repo",
        chart_name="llmd",
        chart_version="1.0.0",
        values={},
        project="llm-d",
        argocd_namespace="argocd",
        destination_server="https://10.0.0.9:6443",
    )
    assert app["spec"]["destination"] == {
        "server": "https://10.0.0.9:6443",
        "namespace": stack.namespace,
    }
```

If the file constructs stacks inline rather than via a helper, mirror the construction used by its existing `build_argo_application` test verbatim.

- [ ] **Step 2: Run to verify it fails**

```bash
cd backend && .venv/bin/python -m pytest tests/test_llmd_manifests.py -q
```

Expected: TypeError — unexpected keyword `destination_server`.

- [ ] **Step 3: Implement**

1. `backend/app/services/llmd_manifests.py` — `build_argo_application` signature gains a required kwarg and the destination line uses it:

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
    destination_server: str,
) -> dict:
```

and

```python
            "destination": {"server": destination_server, "namespace": stack.namespace},
```

2. `backend/app/api/llmd.py`:

- Import change: `from app.services.clusters import argocd_placement_for` (drop `argocd_namespace_for` and `k8s_for_cluster` — after this task llmd.py uses neither; verify with grep).
- `_application_for` becomes:

```python
def _application_for(stack: CustomLlmdStack, argocd_namespace: str, destination_server: str) -> dict:
    return build_argo_application(
        stack,
        chart_repo=settings.llmd_chart_repo,
        chart_name=settings.llmd_chart_name,
        chart_version=settings.llmd_chart_version,
        values=stack.values_snapshot,
        project=settings.argo_project,
        argocd_namespace=argocd_namespace,
        destination_server=destination_server,
    )
```

- Each of the five handler sites replaces the two-line pair with the resolver. Read-only sites (`_live_status`, `applied_values`, `delete_stack`) ignore the third element:

```python
        k8s, argocd_ns, _dest = await argocd_placement_for(db, stack.cluster_id)
```

Apply sites (`create_stack`, `update_stack`):

```python
        k8s, argocd_ns, dest_server = await argocd_placement_for(db, stack.cluster_id)
        await k8s.apply_application(argocd_ns, _application_for(stack, argocd_ns, dest_server))
```

3. `backend/app/services/clusters.py` — delete the whole `argocd_namespace_for` function. First verify it has no remaining consumers:

```bash
grep -rn "argocd_namespace_for" backend/app backend/tests
```

Expected after the llmd.py edit: matches only in `clusters.py` (the definition) and possibly `tests/test_llmd.py` patch strings you are about to update in Step 4. Do not delete if any app-code consumer remains — report BLOCKED instead.

- [ ] **Step 4: Update test_llmd.py**

Every `with patch("app.api.llmd.k8s_for_cluster", AsyncMock(return_value=fake_k8s)), patch("app.api.llmd.argocd_namespace_for", AsyncMock(return_value="argocd")):` block becomes:

```python
    with patch(
        "app.api.llmd.argocd_placement_for",
        AsyncMock(return_value=(fake_k8s, "argocd", "https://kubernetes.default.svc")),
    ):
```

Add one new test asserting the destination flows into the applied manifest:

```python
async def test_create_stack_destination_server_from_placement(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_none_result())
    fake_k8s = MagicMock()
    fake_k8s.apply_application = AsyncMock()
    fake_k8s.get_application = AsyncMock(return_value=None)
    with patch(
        "app.api.llmd.argocd_placement_for",
        AsyncMock(return_value=(fake_k8s, "argo-central", "https://gpu-cluster:6443")),
    ):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/admin/llmd-stacks", json={
                "name": "demo", "target_model_name": "qwen", "cluster_id": None,
                "namespace": "team-a", "values_yaml": "",
            })
    assert resp.status_code == 201
    ns, manifest = fake_k8s.apply_application.await_args.args
    assert ns == "argo-central"
    assert manifest["metadata"]["namespace"] == "argo-central"
    assert manifest["spec"]["destination"]["server"] == "https://gpu-cluster:6443"
```

- [ ] **Step 5: Run tests and gates**

```bash
cd backend && .venv/bin/python -m pytest tests/test_llmd_manifests.py tests/test_llmd.py tests/test_cluster_placement.py -q
cd backend && .venv/bin/python -m pytest tests/ -q 2>&1 | tail -3
cd backend && .venv/bin/ruff check app/ tests/ 2>&1 | tail -2
grep -rn "argocd_namespace_for" backend/app backend/tests || echo "CLEAN"
```

Expected: targeted tests all pass; suite baseline unchanged; ruff 78; grep CLEAN.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/llmd_manifests.py backend/app/api/llmd.py backend/app/services/clusters.py backend/tests/test_llmd_manifests.py backend/tests/test_llmd.py
git commit -m "feat(llmd): Application placement + destination resolved per cluster

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Cluster CRUD API fields + validation

**Files:**
- Modify: `backend/app/api/k8s_clusters.py`
- Modify: `backend/tests/test_k8s_clusters.py`

**Interfaces:**
- Consumes: Task 1's ORM columns.
- Produces: `argocd_host_cluster_id` / `argocd_dest_server` accepted on create/update and returned by `_serialize` — Task 5's frontend relies on these exact JSON key names.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_k8s_clusters.py` (reuse the file's existing imports; `_serialize_cluster` is already imported there):

```python
def test_serialize_includes_argocd_placement_fields():
    host_id = uuid.uuid4()
    row = types.SimpleNamespace(
        id=uuid.uuid4(), name="edge", context="ctx", namespace="default",
        argocd_namespace="argocd", api_server=None, is_default=False,
        description=None, default_nfs_server=None, default_nfs_path=None,
        default_nfs_mount_path=None, kubeconfig_encrypted="x",
        argocd_host_cluster_id=host_id, argocd_dest_server="https://e:6443",
        created_by=None, created_at=None, updated_at=None,
    )
    out = _serialize_cluster(row)
    assert out["argocd_host_cluster_id"] == str(host_id)
    assert out["argocd_dest_server"] == "https://e:6443"


def test_serialize_argocd_placement_nulls():
    row = types.SimpleNamespace(
        id=uuid.uuid4(), name="edge", context="ctx", namespace="default",
        argocd_namespace="argocd", api_server=None, is_default=False,
        description=None, default_nfs_server=None, default_nfs_path=None,
        default_nfs_mount_path=None, kubeconfig_encrypted="x",
        argocd_host_cluster_id=None, argocd_dest_server=None,
        created_by=None, created_at=None, updated_at=None,
    )
    out = _serialize_cluster(row)
    assert out["argocd_host_cluster_id"] is None
    assert out["argocd_dest_server"] is None


async def test_validate_argocd_host_rejects_bad_uuid():
    from app.api.k8s_clusters import _validate_argocd_host

    with pytest.raises(HTTPException) as e:
        await _validate_argocd_host(MagicMock(), "not-a-uuid", None)
    assert e.value.status_code == 400


async def test_validate_argocd_host_rejects_self():
    from app.api.k8s_clusters import _validate_argocd_host

    own = uuid.uuid4()
    with pytest.raises(HTTPException) as e:
        await _validate_argocd_host(MagicMock(), str(own), own)
    assert e.value.status_code == 400


async def test_validate_argocd_host_rejects_unknown_cluster():
    from app.api.k8s_clusters import _validate_argocd_host

    r = MagicMock()
    r.scalar_one_or_none.return_value = None
    db = MagicMock()
    db.execute = AsyncMock(return_value=r)
    with pytest.raises(HTTPException) as e:
        await _validate_argocd_host(db, str(uuid.uuid4()), None)
    assert e.value.status_code == 400


async def test_validate_argocd_host_empty_clears():
    from app.api.k8s_clusters import _validate_argocd_host

    assert await _validate_argocd_host(MagicMock(), "", uuid.uuid4()) is None
    assert await _validate_argocd_host(MagicMock(), None, None) is None


async def test_validate_argocd_host_accepts_existing():
    from app.api.k8s_clusters import _validate_argocd_host

    host_id = uuid.uuid4()
    r = MagicMock()
    r.scalar_one_or_none.return_value = types.SimpleNamespace(id=host_id)
    db = MagicMock()
    db.execute = AsyncMock(return_value=r)
    assert await _validate_argocd_host(db, str(host_id), uuid.uuid4()) == host_id
```

Add `from unittest.mock import AsyncMock, MagicMock` to the file's imports if not present.

- [ ] **Step 2: Run to verify they fail**

```bash
cd backend && .venv/bin/python -m pytest tests/test_k8s_clusters.py -q
```

Expected: new tests fail — `_validate_argocd_host` missing, `_serialize` KeyError.

- [ ] **Step 3: Implement in `backend/app/api/k8s_clusters.py`**

1. Request models — add to `CreateClusterRequest` and `UpdateClusterRequest`:

```python
    argocd_host_cluster_id: str | None = None
    argocd_dest_server: str | None = None
```

2. Validation helper (place after `_parse_kubeconfig`):

```python
async def _validate_argocd_host(
    db: AsyncSession, host_raw: str | None, own_id: uuid.UUID | None
) -> uuid.UUID | None:
    """Resolve/validate an argocd_host_cluster_id form value ('' clears)."""
    if not host_raw:
        return None
    try:
        host_id = uuid.UUID(host_raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="argocd_host_cluster_id must be a cluster id")
    if own_id and host_id == own_id:
        raise HTTPException(
            status_code=400,
            detail="argocd_host_cluster_id cannot be the cluster itself — leave it empty for self-managed",
        )
    row = (
        await db.execute(select(CustomK8sCluster).where(CustomK8sCluster.id == host_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=400, detail="argocd_host_cluster_id does not match a registered cluster")
    return host_id
```

3. `_serialize` — add after the `argocd_namespace` entry:

```python
        "argocd_host_cluster_id": (
            str(c.argocd_host_cluster_id) if c.argocd_host_cluster_id else None
        ),
        "argocd_dest_server": c.argocd_dest_server,
```

4. `create_cluster` — before constructing the row add `argocd_host = await _validate_argocd_host(db, body.argocd_host_cluster_id, None)`, and pass to the constructor (next to `argocd_namespace=`):

```python
        argocd_host_cluster_id=argocd_host,
        argocd_dest_server=(body.argocd_dest_server or "").strip() or None,
```

5. `update_cluster` — with the other `if body.X is not None` blocks:

```python
    if body.argocd_host_cluster_id is not None:
        cluster.argocd_host_cluster_id = await _validate_argocd_host(
            db, body.argocd_host_cluster_id, cluster.id
        )
    if body.argocd_dest_server is not None:
        cluster.argocd_dest_server = body.argocd_dest_server.strip() or None
```

- [ ] **Step 4: Run tests and gates**

```bash
cd backend && .venv/bin/python -m pytest tests/test_k8s_clusters.py -q
cd backend && .venv/bin/python -m pytest tests/ -q 2>&1 | tail -3
cd backend && .venv/bin/ruff check app/ tests/ 2>&1 | tail -2
```

Expected: new tests pass; suite baseline unchanged; ruff 78 (0 new).

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/k8s_clusters.py backend/tests/test_k8s_clusters.py
git commit -m "feat(api): cluster ArgoCD host + destination fields with validation

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Settings dialog fields + i18n

**Files:**
- Modify: `frontend/src/types/index.ts` (K8sClusterSummary, ~line 625)
- Modify: `frontend/src/hooks/use-api.ts` (CreateK8sClusterBody, ~line 1280)
- Modify: `frontend/src/components/cluster-settings-tab.tsx`
- Modify: `frontend/messages/en.json`, `frontend/messages/ko.json` (inside `settings.clusters`)

**Interfaces:**
- Consumes: Task 4's JSON keys `argocd_host_cluster_id` / `argocd_dest_server` on the cluster summary and request bodies.
- Produces: final task, nothing downstream.

- [ ] **Step 1: Baseline lint**

```bash
cd frontend && npm run lint 2>&1 | tail -5
```

Record counts (baseline 4 errors / 13 warnings).

- [ ] **Step 2: Types + request body**

`frontend/src/types/index.ts` — inside `K8sClusterSummary`, after `argocd_namespace: string;`:

```ts
  argocd_host_cluster_id: string | null;
  argocd_dest_server: string | null;
```

`frontend/src/hooks/use-api.ts` — inside `CreateK8sClusterBody`, after `argocd_namespace?: string;`:

```ts
  argocd_host_cluster_id?: string | null;
  argocd_dest_server?: string | null;
```

- [ ] **Step 3: Dialog form state + fields**

In `frontend/src/components/cluster-settings-tab.tsx`:

1. `FormState` gains (after `argocd_namespace: string;`):

```ts
  argocd_host_cluster_id: string;
  argocd_dest_server: string;
```

2. `EMPTY` gains:

```ts
  argocd_host_cluster_id: "",
  argocd_dest_server: "",
```

3. `openEdit` gains (after the `argocd_namespace` line):

```ts
      argocd_host_cluster_id: c.argocd_host_cluster_id ?? "",
      argocd_dest_server: c.argocd_dest_server ?? "",
```

4. Both submit bodies (`update` body object and the `CreateK8sClusterBody`) gain:

```ts
        argocd_host_cluster_id: form.argocd_host_cluster_id,
        argocd_dest_server: form.argocd_dest_server,
```

(empty string clears on the backend — send unconditionally.)

5. Render — insert directly after the existing `argocdNamespaceHint` paragraph (`cluster-settings-tab.tsx:342`):

```tsx
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2">
                <Label htmlFor="cluster-argocd-host">{t("argocdHostLabel")}</Label>
                <select
                  id="cluster-argocd-host"
                  className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                  value={form.argocd_host_cluster_id}
                  onChange={(e) => setForm({ ...form, argocd_host_cluster_id: e.target.value })}
                >
                  <option value="">{t("argocdHostSelf")}</option>
                  {(clusters ?? [])
                    .filter((k) => k.id !== editing?.id)
                    .map((k) => (
                      <option key={k.id} value={k.id}>
                        {k.name}
                      </option>
                    ))}
                </select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="cluster-argocd-dest">{t("argocdDestLabel")}</Label>
                <Input
                  id="cluster-argocd-dest"
                  value={form.argocd_dest_server}
                  onChange={(e) => setForm({ ...form, argocd_dest_server: e.target.value })}
                  placeholder="https://kubernetes.default.svc"
                />
              </div>
            </div>
            <p className="text-xs text-muted-foreground -mt-2">{t("argocdPlacementHint")}</p>
```

- [ ] **Step 4: i18n keys**

Inside `settings.clusters` (locate the existing `argocdNamespaceHint` key) in `frontend/messages/en.json`:

```json
"argocdHostLabel": "Managing ArgoCD cluster",
"argocdHostSelf": "This cluster itself",
"argocdDestLabel": "ArgoCD destination server",
"argocdPlacementHint": "For a central ArgoCD: pick the cluster running ArgoCD, and set the destination to the server URL that ArgoCD registers this cluster under. Leave both empty when this cluster runs its own ArgoCD.",
```

and in `frontend/messages/ko.json`:

```json
"argocdHostLabel": "관리 ArgoCD 클러스터",
"argocdHostSelf": "이 클러스터 자신",
"argocdDestLabel": "ArgoCD destination server",
"argocdPlacementHint": "중앙 ArgoCD 구조라면 ArgoCD가 실행 중인 클러스터를 선택하고, destination에는 그 ArgoCD의 cluster secret에 등록된 이 클러스터의 server URL을 입력하세요. 이 클러스터에 자체 ArgoCD가 있으면 둘 다 비워 둡니다.",
```

- [ ] **Step 5: Gates**

```bash
cd frontend && npm run lint 2>&1 | tail -5
cd frontend && npm run build 2>&1 | tail -10
python3 -c "
import json
for lang in ('en','ko'):
    c = json.load(open(f'frontend/messages/{lang}.json'))['settings']['clusters']
    assert all(k in c for k in ('argocdHostLabel','argocdHostSelf','argocdDestLabel','argocdPlacementHint')), lang
print('i18n OK')
"
```

Expected: lint counts equal baseline (0 new), build succeeds, `i18n OK`.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/hooks/use-api.ts frontend/src/components/cluster-settings-tab.tsx frontend/messages/en.json frontend/messages/ko.json
git commit -m "feat(frontend): cluster ArgoCD host + destination settings

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```
