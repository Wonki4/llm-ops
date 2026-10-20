# Storage as a First-Class Object — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the free-text `pvc_name`/`pvc_mount_path` on recipes and deployments with admin-managed Storage objects that resolve to a PVC or NFS volume per cluster × namespace at deploy time.

**Architecture:** Two new tables (`custom_storage`, `custom_storage_binding`) and a `storages` JSONB list on recipes and deployments (and on P/D pool overrides). A pure module (`storage_mounts`) validates lists and renders volumes; a DB module (`storage_store`) resolves bindings and snapshots the result onto the deployment, so running workloads never change until redeploy. Benchmarks, the recipe importer and the cluster "default NFS" all move onto the same objects; a data migration converts existing rows without changing any rendered manifest.

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic (Postgres ≥ 15, `NULLS NOT DISTINCT`), kubernetes_asyncio, pytest (mock-DB style), Next.js App Router + TanStack Query + next-intl, Playwright for UI checks.

**Spec:** `docs/superpowers/specs/2026-10-19-cluster-storage-design.md`

## Global Constraints

- Work in worktree `.claude/worktrees/cluster-storage`, branch `feat/cluster-storage`. Backend Python: `../../../../backend/.venv/bin/python` from the worktree's `backend/` (call it `$PY`; `export PY=../../../../backend/.venv/bin/python`); always run with `PYTHONPATH=.` so the worktree's `app` is imported, not the main checkout's. All backend `Run:` lines below are run from the worktree's `backend/`.
- Lint: every backend task ends its run step with `$PY -m ruff check <changed files>` (config `backend/pyproject.toml`: `E,F,I,N,W,UP`, line length 120). It must report `All checks passed!`.
- Storage name: `^[a-z0-9]([a-z0-9-]{0,48}[a-z0-9])?$` (≤ 50 chars). Volume name for new entries: `st-<name>`. Migrated entries keep `model-weights`.
- PVC binding: `namespace` required. NFS binding: `namespace` optional (NULL = all namespaces of the cluster). `cluster_id` NULL = portal default cluster (mounted kubeconfig), exactly like `custom_model_deployment.cluster_id` and `custom_gpu_profile.cluster_id`.
- Resolution: exact `(cluster, namespace)` → `(cluster, NULL)` → unresolved. Unresolved blocks deploy (400). PVC `Pending`/missing only warns.
- Deployments store the resolved snapshot; binding edits never touch a running deployment. Every `PUT /api/model-deployments/{id}` is a redeploy and re-resolves.
- Legacy request fields `pvc_name` / `pvc_mount_path` → 422 with the hint `pvc_name/pvc_mount_path were replaced by storages: [{storage_id, mount_path, read_only}]`.
- `pvc_name`/`pvc_mount_path` columns stay (unread) — do not drop them in this branch.
- Storage + binding management: super user only (`require_super_user`).
- Air-gapped: no new runtime downloads, no new npm/pip dependencies.
- Baseline: backend suite has ~21 failures that need Postgres; compare failure lists before/after (`pytest -q | grep ^FAILED | sort`), don't chase them. Frontend has no unit runner: `npx tsc --noEmit`, `npx eslint <changed files>`, `npm run build`, Playwright on `next dev -p 3100` with mocked `/api/proxy/**` and a dummy `litellm_session` cookie; remove the `nextjs-portal` overlay before clicking.
- Commit after every task; message style `feat(storage): …` / `test(storage): …`, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Exception, accepted: the commits of Tasks 12–15 leave `npx tsc --noEmit` red (files not yet migrated off `pvc_*`/`default_nfs_*`); Task 16 makes it clean. Say so in the PR description — the frontend is not bisectable between those commits.
- Shared helpers live in one place: `storage_mounts.all_entries` (base + P/D pool lists), `storage_mounts.stale_entries`, `storage_store.null_safe_eq` (`col = v` / `col IS NULL`), `storage_store.pvc_phases` / `list_pvcs_timed` (the 5 s-bounded PVC lookup), and `pd_serving.ROLES` for the pool roles. Do not re-implement them in later tasks.

## Review Focus

1. **Migrated deployment re-applied after upgrade** — the next reconcile/update must render byte-identical volumes (no rollout). Pinned by the golden test in Task 5 and the migration check in Task 11.
2. **Two storages whose mount paths nest** (`/models` and `/models/cache`) — must be rejected at save time, not fail at pod start. Pinned in Task 2 (`test_validate_rejects_nested_mounts`).
3. **Namespace-specific binding added after a deployment was resolved through the cluster-wide NFS binding** — the deployment must show as stale, not silently keep "current". Pinned in Task 3 (`test_stale_when_more_specific_binding_appears`).
4. **Benchmark run created before the upgrade, reconciled after** — its snapshot still has `pvc_name`; the job must keep mounting it. Pinned in Task 9 (`test_reconcile_legacy_snapshot_still_mounts_pvc`).
5. **Deleting a storage still used only by a P/D pool override or a cluster's bench default** — must 409, not orphan the reference. Pinned in Task 4 (`test_delete_storage_in_use_by_pool_or_bench_409`).

---

## File Structure

Backend (new)
- `backend/app/db/models/custom_storage.py` — `CustomStorage`, `CustomStorageBinding`.
- `backend/migrations/versions/051_storages.py` — schema.
- `backend/migrations/versions/052_storages_backfill.py` — data move + drop `default_nfs_*`.
- `backend/app/services/storage_mounts.py` — pure: name/list validation, binding pick, snapshot, volume rendering, legacy helpers.
- `backend/app/services/storage_store.py` — DB: load, resolve, stale detection, usage index, bench volumes, claim matching, timed PVC lookup.
- `backend/app/services/storage_fields.py` — request-model helpers shared by the recipe and deployment APIs.
- `backend/app/api/storages.py` — storage/binding CRUD, status, resolve, cluster PVC listing.
- Tests: `backend/tests/_storage.py` (helpers), `test_storage_schema.py`, `test_storage_mounts.py`, `test_storage_store.py`, `test_k8s_pvcs.py`, `test_storages_api.py`, `test_storage_manifests.py`, `test_storage_recipes_deployments.py`, `test_storage_benchmarks.py`, `test_storage_import.py`, `test_storage_backfill.py`.

Backend (modified)
- `backend/app/db/models/custom_serving_recipe.py`, `custom_model_deployment.py`, `custom_k8s_cluster.py`, `__init__.py`
- `backend/app/clients/k8s.py` (`list_pvcs`)
- `backend/app/services/model_deployment_manifests.py` (`_volumes`), `pd_serving.py` (pool `storages`)
- `backend/app/api/serving_recipes.py`, `model_deployments.py`, `k8s_clusters.py`, `benchmarks.py`
- `backend/app/services/benchmark_manifests.py`, `benchmark_serving.py`, `recipe_import.py`
- `backend/app/jobs/reconcile_benchmarks.py`
- `backend/app/main.py` (router)

Frontend (new)
- `frontend/src/components/storage-mounts-editor.tsx` — rows of storage/mount/read-only.
- `frontend/src/components/storage-binding-dialog.tsx` — add/edit binding with PVC dropdown.
- `frontend/src/components/storage-resolution-preview.tsx` — deploy-dialog preview.
- `frontend/src/components/storage-bound-on.tsx` — "bound on" chips (storage list, recipe storage rows).
- `frontend/src/app/(app)/admin/storages/page.tsx`, `frontend/src/app/(app)/admin/storages/[id]/page.tsx`

Frontend (modified)
- `frontend/src/types/index.ts`, `frontend/src/hooks/use-api.ts`
- `frontend/src/components/pool-config-cards.tsx`, `frontend/src/lib/pd-serving.ts`, `frontend/src/lib/recipe-drafts.ts`, `frontend/src/components/serving-recipe-form.tsx`
- `frontend/src/components/deploy-from-recipe-dialog.tsx`, `frontend/src/app/(app)/admin/deployments/[id]/page.tsx`
- `frontend/src/components/cluster-settings-tab.tsx`, `frontend/src/components/app-sidebar.tsx`
- `frontend/messages/ko.json`, `frontend/messages/en.json`

---

### Task 1: Schema — models and migration 051

**Files:**
- Create: `backend/app/db/models/custom_storage.py`
- Create: `backend/migrations/versions/051_storages.py`
- Modify: `backend/app/db/models/custom_serving_recipe.py`, `backend/app/db/models/custom_model_deployment.py`, `backend/app/db/models/custom_k8s_cluster.py`, `backend/app/db/models/__init__.py`
- Test: `backend/tests/test_storage_schema.py`

**Interfaces:**
- Produces: `CustomStorage(id, name, description, kind, created_by, updated_by, created_at, updated_at)`; `CustomStorageBinding(id, storage_id, cluster_id, namespace, type, claim_name, nfs_server, nfs_path, sub_path, …audit)`; `storages: list` (JSONB, not null, default `[]`) on `CustomServingRecipe` and `CustomModelDeployment`; `bench_storage_id`, `bench_mount_path` on `CustomK8sCluster` (the `default_nfs_*` attributes stay until Task 11).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_storage_schema.py
"""Storage tables + storages columns."""

from app.db.models.custom_k8s_cluster import CustomK8sCluster
from app.db.models.custom_model_deployment import CustomModelDeployment
from app.db.models.custom_serving_recipe import CustomServingRecipe
from app.db.models.custom_storage import CustomStorage, CustomStorageBinding


def test_storage_columns():
    cols = CustomStorage.__table__.columns
    assert cols["name"].unique is True
    assert cols["kind"].server_default.arg == "weights"


def test_binding_scope_unique_nulls_not_distinct():
    uq = next(c for c in CustomStorageBinding.__table__.constraints if c.name == "uq_storage_binding_scope")
    assert [c.name for c in uq.columns] == ["storage_id", "cluster_id", "namespace"]
    assert uq.dialect_options["postgresql"]["nulls_not_distinct"] is True
    ck = next(c for c in CustomStorageBinding.__table__.constraints if c.name == "ck_storage_binding_target")
    assert "claim_name IS NOT NULL" in str(ck.sqltext)
    cols = CustomStorageBinding.__table__.columns
    assert cols["cluster_id"].nullable is True
    assert next(iter(cols["storage_id"].foreign_keys)).ondelete == "CASCADE"


def test_storages_json_columns():
    for model in (CustomServingRecipe, CustomModelDeployment):
        col = model.__table__.columns["storages"]
        assert col.nullable is False
        assert col.server_default.arg == "[]"


def test_cluster_bench_storage():
    cols = CustomK8sCluster.__table__.columns
    assert next(iter(cols["bench_storage_id"].foreign_keys)).ondelete == "SET NULL"
    assert cols["bench_mount_path"].nullable is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. $PY -m pytest tests/test_storage_schema.py -q`
Expected: FAIL — `ModuleNotFoundError: app.db.models.custom_storage`

- [ ] **Step 3: Write the models**

```python
# backend/app/db/models/custom_storage.py
"""Storage: a named piece of data (weights, cache, datasets) workloads mount.

A storage is cluster-agnostic. ``CustomStorageBinding`` says what it physically
is on one cluster × namespace: an existing PVC, or an NFS export. Recipes and
deployments list storages (``storages`` JSONB); the concrete volume is resolved
from the deploy target at deploy time (see services/storage_store).
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import CustomBase


class CustomStorage(CustomBase):
    __tablename__ = "custom_storage"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # weights | cache | data | other — display/filter only.
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="weights", server_default="weights")
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class CustomStorageBinding(CustomBase):
    __tablename__ = "custom_storage_binding"
    __table_args__ = (
        UniqueConstraint(
            "storage_id", "cluster_id", "namespace", name="uq_storage_binding_scope", postgresql_nulls_not_distinct=True
        ),
        CheckConstraint(
            "(type = 'pvc' AND claim_name IS NOT NULL AND namespace IS NOT NULL"
            " AND nfs_server IS NULL AND nfs_path IS NULL)"
            " OR (type = 'nfs' AND nfs_server IS NOT NULL AND nfs_path IS NOT NULL AND claim_name IS NULL)",
            name="ck_storage_binding_target",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    storage_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("custom_storage.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Null = the portal default cluster (mounted kubeconfig), mirroring deployments.
    cluster_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("custom_k8s_cluster.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # Null = every namespace of the cluster (NFS only; PVCs are namespaced).
    namespace: Mapped[str | None] = mapped_column(String(63), nullable=True)
    type: Mapped[str] = mapped_column(String(8), nullable=False)  # pvc | nfs
    claim_name: Mapped[str | None] = mapped_column(String(253), nullable=True)
    nfs_server: Mapped[str | None] = mapped_column(String(255), nullable=True)
    nfs_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    sub_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
```

In `custom_serving_recipe.py` and `custom_model_deployment.py`, directly under `pvc_mount_path`, add (and mark the PVC columns as deprecated):

```python
    # Deprecated: unread since migration 052 (see storages); dropped in a follow-up release.
    pvc_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    pvc_mount_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # [{"storage_id", "mount_path", "read_only"}]; deployments also carry
    # "volume_name" + "resolved" (the binding snapshot). See services/storage_mounts.
    storages: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
```

In `custom_k8s_cluster.py`, below the `default_nfs_*` block:

```python
    # Storage mounted read-only into benchmarks against a raw model_name (tokenizer
    # / weights), resolved for the job's namespace. Replaces default_nfs_* (052).
    bench_storage_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("custom_storage.id", ondelete="SET NULL"), nullable=True
    )
    bench_mount_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
```

In `__init__.py` add `from app.db.models.custom_storage import CustomStorage, CustomStorageBinding` and both names to `__all__`.

- [ ] **Step 4: Write migration 051**

```python
# backend/migrations/versions/051_storages.py
"""Storage objects + per-cluster/namespace bindings.

- custom_storage           (name unique, kind)
- custom_storage_binding   (storage × cluster × namespace → pvc | nfs)
- storages jsonb not null default '[]' on custom_serving_recipe / custom_model_deployment
- custom_k8s_cluster.bench_storage_id (FK SET NULL) + bench_mount_path

Data is moved by 052_storages_backfill.

Revision ID: 051_storages
Revises: 050_pd_serving
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "051_storages"
down_revision = "050_pd_serving"
branch_labels = None
depends_on = None

_TABLES = ("custom_serving_recipe", "custom_model_deployment")


def _audit() -> list[sa.Column]:
    return [
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("updated_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "custom_storage",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("kind", sa.String(16), nullable=False, server_default="weights"),
        *_audit(),
    )
    op.create_index("ix_custom_storage_name", "custom_storage", ["name"], unique=True)
    op.create_table(
        "custom_storage_binding",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "storage_id", UUID(as_uuid=True), sa.ForeignKey("custom_storage.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "cluster_id", UUID(as_uuid=True), sa.ForeignKey("custom_k8s_cluster.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column("namespace", sa.String(63), nullable=True),
        sa.Column("type", sa.String(8), nullable=False),
        sa.Column("claim_name", sa.String(253), nullable=True),
        sa.Column("nfs_server", sa.String(255), nullable=True),
        sa.Column("nfs_path", sa.String(1024), nullable=True),
        sa.Column("sub_path", sa.String(1024), nullable=True),
        *_audit(),
        sa.UniqueConstraint(
            "storage_id", "cluster_id", "namespace", name="uq_storage_binding_scope", postgresql_nulls_not_distinct=True
        ),
        sa.CheckConstraint(
            "(type = 'pvc' AND claim_name IS NOT NULL AND namespace IS NOT NULL"
            " AND nfs_server IS NULL AND nfs_path IS NULL)"
            " OR (type = 'nfs' AND nfs_server IS NOT NULL AND nfs_path IS NOT NULL AND claim_name IS NULL)",
            name="ck_storage_binding_target",
        ),
    )
    op.create_index("ix_custom_storage_binding_storage_id", "custom_storage_binding", ["storage_id"])
    op.create_index("ix_custom_storage_binding_cluster_id", "custom_storage_binding", ["cluster_id"])
    for table in _TABLES:
        op.add_column(table, sa.Column("storages", JSONB, nullable=False, server_default="[]"))
    op.add_column(
        "custom_k8s_cluster",
        sa.Column(
            "bench_storage_id",
            UUID(as_uuid=True),
            sa.ForeignKey("custom_storage.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("custom_k8s_cluster", sa.Column("bench_mount_path", sa.String(512), nullable=True))


def downgrade() -> None:
    op.drop_column("custom_k8s_cluster", "bench_mount_path")
    op.drop_column("custom_k8s_cluster", "bench_storage_id")
    for table in _TABLES:
        op.drop_column(table, "storages")
    op.drop_index("ix_custom_storage_binding_cluster_id", table_name="custom_storage_binding")
    op.drop_index("ix_custom_storage_binding_storage_id", table_name="custom_storage_binding")
    op.drop_table("custom_storage_binding")
    op.drop_index("ix_custom_storage_name", table_name="custom_storage")
    op.drop_table("custom_storage")
```

Before writing, check `ls backend/migrations/versions | tail -2`: if the base branch has gained a revision after `050_pd_serving`, set `down_revision` to the newest one.

- [ ] **Step 5: Run tests**

Run:
```bash
PYTHONPATH=. $PY -m pytest tests/test_storage_schema.py tests/test_gpu_profiles.py -q
$PY -m ruff check app/db/models migrations/versions/051_storages.py tests/test_storage_schema.py
```
Expected: PASS; `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add backend/app/db/models backend/migrations/versions/051_storages.py backend/tests/test_storage_schema.py
git commit -m "feat(storage): storage + binding tables, storages columns (051)"
```

---

### Task 2: Pure storage mounts module

**Files:**
- Create: `backend/app/services/storage_mounts.py`
- Create: `backend/tests/_storage.py`
- Test: `backend/tests/test_storage_mounts.py`

**Interfaces:**
- Produces (all pure):
  - `LEGACY_VOLUME_NAME = "model-weights"`, `STORAGE_KINDS = ("weights", "cache", "data", "other")`
  - `validate_storage_name(name: str) -> str` (raises `ValueError`)
  - `default_volume_name(storage_name: str) -> str` → `"st-<name>"`
  - `validate_entries(raw: list | None) -> list[dict]` → `[{"storage_id": str, "mount_path": str, "read_only": bool}]` (drops `volume_name`/`resolved`; raises `ValueError`)
  - `model_path_warnings(model_path: str | None, entries: list[dict]) -> list[str]`
  - `pick_binding(bindings: list, storage_id: str, cluster_id: uuid.UUID | None, namespace: str) -> binding | None`
  - `snapshot(binding) -> dict` → `{"binding_id", "type", "claim_name", "namespace", "nfs_server", "nfs_path", "sub_path"}`
  - `to_volumes(entries: list[dict] | None, *, force_read_only: bool = False) -> tuple[list[dict], list[dict]]`
  - `all_entries(row) -> list[dict]` — the base `storages` list followed by every `pd_config.<role>.storages` list (roles from `pd_serving.ROLES`); works on ORM rows, pydantic bodies and `SimpleNamespace`
  - `entry_storage_ids(row) -> set[str]` (ids of `all_entries(row)`)
  - `stale_entries(entries: list[dict], bindings: list, cluster_id, namespace: str) -> list[str]` — storage ids whose current `pick_binding` differs from the entry's `resolved` snapshot (binding deleted, edited, or shadowed by a more specific one), in first-seen order, unique
  - `check_volume_names(entries: list[dict]) -> None` — raises `ValueError` when a rendered volume name repeats or is reserved (`shm`)
  - `legacy_pvc_volumes(claim: str | None, mount_path: str | None) -> tuple[list, list]` (read-only, `model-weights`)
  - `nfs_override_volumes(params: dict | None) -> tuple[list, list] | None`
- Test helper `tests/_storage.py`: `binding(**kw) -> SimpleNamespace`, `legacy_entry(claim, mount, *, storage_id=SID, namespace="default") -> dict`; constants `SID`, `SID2`, `CLUSTER`.

- [ ] **Step 1: Write the test helper**

```python
# backend/tests/_storage.py
"""Shared builders for storage tests."""

import types
import uuid

SID = "11111111-1111-1111-1111-111111111111"
SID2 = "22222222-2222-2222-2222-222222222222"
CLUSTER = uuid.UUID("33333333-3333-3333-3333-333333333333")


def binding(**kw):
    base = dict(
        id=uuid.uuid4(), storage_id=uuid.UUID(SID), cluster_id=None, namespace="default", type="pvc",
        claim_name="models-pvc", nfs_server=None, nfs_path=None, sub_path=None,
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def legacy_entry(claim: str, mount: str, *, storage_id: str = SID, namespace: str = "default") -> dict:
    """A deployment entry exactly as migration 052 writes it (renders like the old pvc_* columns)."""
    return {
        "storage_id": storage_id,
        "mount_path": mount,
        "read_only": False,
        "volume_name": "model-weights",
        "resolved": {
            "binding_id": "44444444-4444-4444-4444-444444444444", "type": "pvc", "claim_name": claim,
            "namespace": namespace, "nfs_server": None, "nfs_path": None, "sub_path": None,
        },
    }
```

- [ ] **Step 2: Write the failing tests**

```python
# backend/tests/test_storage_mounts.py
"""Pure storage helpers: validation, resolution order, volume rendering."""

import types

import pytest

from app.services.storage_mounts import (
    all_entries,
    check_volume_names,
    default_volume_name,
    entry_storage_ids,
    legacy_pvc_volumes,
    model_path_warnings,
    nfs_override_volumes,
    pick_binding,
    snapshot,
    stale_entries,
    to_volumes,
    validate_entries,
    validate_storage_name,
)
from tests._storage import CLUSTER, SID, SID2, binding, legacy_entry

# ─── names ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["llama-weights", "a", "hf-cache-2", "x" * 50])
def test_storage_name_ok(name):
    assert validate_storage_name(name) == name


@pytest.mark.parametrize("name", ["", "Llama", "-a", "a-", "a_b", "x" * 51, "a b"])
def test_storage_name_rejected(name):
    with pytest.raises(ValueError):
        validate_storage_name(name)


def test_default_volume_name_fits_dns_label():
    assert default_volume_name("x" * 50) == "st-" + "x" * 50
    assert len(default_volume_name("x" * 50)) <= 63


# ─── entries ──────────────────────────────────────────────────────────────────


def test_validate_normalises_and_strips_resolution():
    out = validate_entries(
        [{"storage_id": SID, "mount_path": "/models/", "volume_name": "x", "resolved": {"type": "pvc"}}]
    )
    assert out == [{"storage_id": SID, "mount_path": "/models", "read_only": True}]


def test_validate_keeps_explicit_read_write():
    assert validate_entries([{"storage_id": SID, "mount_path": "/data", "read_only": False}])[0]["read_only"] is False


@pytest.mark.parametrize(
    "raw",
    [
        [{"storage_id": "nope", "mount_path": "/m"}],
        [{"storage_id": SID, "mount_path": "relative"}],
        [{"storage_id": SID, "mount_path": "/"}],
        [{"storage_id": SID, "mount_path": "/dev/shm"}],
        [{"storage_id": SID, "mount_path": "/dev"}],
        [{"storage_id": SID, "mount_path": "/a"}, {"storage_id": SID, "mount_path": "/b"}],
        ["not-an-object"],
    ],
)
def test_validate_rejects(raw):
    with pytest.raises(ValueError):
        validate_entries(raw)


def test_validate_rejects_nested_mounts():
    nested = [{"storage_id": SID, "mount_path": "/models"}, {"storage_id": SID2, "mount_path": "/models/cache"}]
    with pytest.raises(ValueError, match="overlap"):
        validate_entries(nested)
    # siblings sharing a prefix string are fine
    siblings = [{"storage_id": SID, "mount_path": "/models"}, {"storage_id": SID2, "mount_path": "/models2"}]
    assert len(validate_entries(siblings)) == 2


def test_validate_empty():
    assert validate_entries(None) == [] and validate_entries([]) == []


def test_model_path_warnings():
    entries = [{"storage_id": SID, "mount_path": "/models", "read_only": True}]
    assert model_path_warnings("/models/llama", entries) == []
    assert model_path_warnings("/models", entries) == []
    assert model_path_warnings("/data/llama", entries) == ["model_path_outside_storages"]
    assert model_path_warnings("meta-llama/Llama-3-8B", entries) == []  # HF repo id
    assert model_path_warnings("/models2/x", entries) == ["model_path_outside_storages"]


# ─── binding pick ─────────────────────────────────────────────────────────────


def test_pick_exact_namespace_wins_over_cluster_wide():
    wide = binding(cluster_id=CLUSTER, namespace=None, type="nfs", claim_name=None, nfs_server="nfs", nfs_path="/x")
    exact = binding(cluster_id=CLUSTER, namespace="prod")
    assert pick_binding([wide, exact], SID, CLUSTER, "prod") is exact
    assert pick_binding([wide, exact], SID, CLUSTER, "dev") is wide


def test_pick_none_when_cluster_or_storage_differs():
    b = binding(cluster_id=CLUSTER, namespace="prod")
    assert pick_binding([b], SID, None, "prod") is None  # portal default ≠ registered cluster
    assert pick_binding([b], SID2, CLUSTER, "prod") is None
    assert pick_binding([binding(cluster_id=None)], SID, None, "default") is not None


def test_snapshot_shape():
    b = binding()
    assert snapshot(b) == {
        "binding_id": str(b.id), "type": "pvc", "claim_name": "models-pvc", "namespace": "default",
        "nfs_server": None, "nfs_path": None, "sub_path": None,
    }


# ─── rendering ────────────────────────────────────────────────────────────────


def test_legacy_entry_renders_like_old_pvc_columns():
    vols, mounts = to_volumes([legacy_entry("models-pvc", "/models")])
    assert vols == [{"name": "model-weights", "persistentVolumeClaim": {"claimName": "models-pvc"}}]
    assert mounts == [{"name": "model-weights", "mountPath": "/models"}]


def test_read_only_nfs_with_sub_path():
    e = {
        "storage_id": SID, "mount_path": "/cache", "read_only": True, "volume_name": "st-hf-cache",
        "resolved": {"binding_id": "b", "type": "nfs", "claim_name": None, "namespace": None,
                     "nfs_server": "10.0.0.5", "nfs_path": "/export", "sub_path": "hf"},
    }
    vols, mounts = to_volumes([e])
    assert vols == [{"name": "st-hf-cache", "nfs": {"server": "10.0.0.5", "path": "/export", "readOnly": True}}]
    assert mounts == [{"name": "st-hf-cache", "mountPath": "/cache", "readOnly": True, "subPath": "hf"}]


def test_force_read_only_matches_old_bench_mount():
    vols, mounts = to_volumes([legacy_entry("models-pvc", "/models")], force_read_only=True)
    assert vols == [{"name": "model-weights", "persistentVolumeClaim": {"claimName": "models-pvc", "readOnly": True}}]
    assert mounts == [{"name": "model-weights", "mountPath": "/models", "readOnly": True}]


def test_unresolved_entries_are_not_rendered():
    assert to_volumes([{"storage_id": SID, "mount_path": "/m", "read_only": True}]) == ([], [])


def test_legacy_and_nfs_override_helpers():
    assert legacy_pvc_volumes("c", "/m") == (
        [{"name": "model-weights", "persistentVolumeClaim": {"claimName": "c", "readOnly": True}}],
        [{"name": "model-weights", "mountPath": "/m", "readOnly": True}],
    )
    assert legacy_pvc_volumes(None, "/m") == ([], [])
    assert nfs_override_volumes({"nfs_server": "s", "nfs_path": "/p", "nfs_mount_path": "/m"}) == (
        [{"name": "model-weights", "nfs": {"server": "s", "path": "/p", "readOnly": True}}],
        [{"name": "model-weights", "mountPath": "/m", "readOnly": True}],
    )
    assert nfs_override_volumes({"nfs_server": "s"}) is None
    assert nfs_override_volumes(None) is None


def test_all_entries_and_ids_include_pool_overrides():
    base = {"storage_id": SID, "mount_path": "/m"}
    pool = {"storage_id": SID2, "mount_path": "/m"}
    row = types.SimpleNamespace(storages=[base], pd_config={"prefill": {"storages": [pool]}, "decode": {}})
    assert all_entries(row) == [base, pool]
    assert entry_storage_ids(row) == {SID, SID2}
    empty = types.SimpleNamespace(storages=None, pd_config=None)
    assert all_entries(empty) == [] and entry_storage_ids(empty) == set()


def test_stale_entries():
    wide = binding(cluster_id=CLUSTER, namespace=None, type="nfs", claim_name=None, nfs_server="s", nfs_path="/p")
    entry = {"storage_id": SID, "mount_path": "/m", "read_only": True, "volume_name": "st-x",
             "resolved": snapshot(wide)}
    assert stale_entries([entry], [wide], CLUSTER, "prod") == []
    exact = binding(cluster_id=CLUSTER, namespace="prod")
    assert stale_entries([entry, entry], [wide, exact], CLUSTER, "prod") == [SID]  # shadowed, reported once
    assert stale_entries([entry], [], CLUSTER, "prod") == [SID]  # binding deleted


def test_check_volume_names():
    check_volume_names([legacy_entry("a", "/a"), {**legacy_entry("b", "/b", storage_id=SID2), "volume_name": "st-b"}])
    with pytest.raises(ValueError, match="reserved"):
        check_volume_names([{**legacy_entry("a", "/a"), "volume_name": "shm"}])
    with pytest.raises(ValueError, match="twice"):
        check_volume_names([legacy_entry("a", "/a"), legacy_entry("b", "/b", storage_id=SID2)])
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `PYTHONPATH=. $PY -m pytest tests/test_storage_mounts.py -q`
Expected: FAIL — `ModuleNotFoundError: app.services.storage_mounts`

- [ ] **Step 4: Implement**

```python
# backend/app/services/storage_mounts.py
"""Storage lists on recipes/deployments: validation, binding pick, volume rendering.

An entry is ``{"storage_id", "mount_path", "read_only"}``. Deployments add
``"volume_name"`` and ``"resolved"`` (the binding snapshot taken at deploy
time, see storage_store.resolve_entries); only resolved entries render.
Pure functions — no DB, no K8s.
"""

from __future__ import annotations

import posixpath
import re
import uuid

# pd_serving must not import this module at load time (its pool validator imports
# validate_entries lazily) — otherwise the two modules would import each other.
from app.services.pd_serving import ROLES

LEGACY_VOLUME_NAME = "model-weights"
STORAGE_KINDS = ("weights", "cache", "data", "other")
# Volume names the manifest builders add themselves (model_deployment_manifests: /dev/shm).
_RESERVED_VOLUME_NAMES = frozenset({"shm"})
_RESERVED_MOUNTS = ("/dev/shm",)
_NAME = re.compile(r"^[a-z0-9]([a-z0-9-]{0,48}[a-z0-9])?$")


def validate_storage_name(name: str) -> str:
    name = (name or "").strip()
    if not _NAME.match(name):
        raise ValueError("storage name must match ^[a-z0-9][a-z0-9-]*[a-z0-9]$ (max 50 chars)")
    return name


def default_volume_name(storage_name: str) -> str:
    return f"st-{storage_name}"


def _overlaps(a: str, b: str) -> bool:
    return a == b or a.startswith(b.rstrip("/") + "/") or b.startswith(a.rstrip("/") + "/")


def _mount_path(raw) -> str:
    if not isinstance(raw, str) or not raw.strip().startswith("/"):
        raise ValueError(f"mount_path must be an absolute path: {raw!r}")
    path = posixpath.normpath(raw.strip())
    if path == "/":
        raise ValueError("mount_path cannot be /")
    for reserved in _RESERVED_MOUNTS:
        if _overlaps(path, reserved):
            raise ValueError(f"mount_path {path} collides with the reserved {reserved}")
    return path


def validate_entries(raw: list | None) -> list[dict]:
    """Normalise a storages list; resolution keys are dropped (the server recomputes them)."""
    out: list[dict] = []
    for item in raw or []:
        if not isinstance(item, dict):
            raise ValueError("each storage must be an object {storage_id, mount_path, read_only}")
        try:
            sid = str(uuid.UUID(str(item.get("storage_id"))))
        except ValueError:
            raise ValueError(f"invalid storage_id: {item.get('storage_id')!r}")
        if any(e["storage_id"] == sid for e in out):
            raise ValueError(f"storage {sid} is listed twice")
        path = _mount_path(item.get("mount_path"))
        for e in out:
            if _overlaps(path, e["mount_path"]):
                raise ValueError(f"mount paths overlap: {e['mount_path']} and {path}")
        out.append({"storage_id": sid, "mount_path": path, "read_only": bool(item.get("read_only", True))})
    return out


def model_path_warnings(model_path: str | None, entries: list[dict]) -> list[str]:
    """Warn when an absolute model path is not under any mounted storage (HF repo ids pass)."""
    if not model_path or not model_path.startswith("/"):
        return []
    for e in entries:
        mount = e["mount_path"]
        if model_path == mount or model_path.startswith(mount.rstrip("/") + "/"):
            return []
    return ["model_path_outside_storages"]


def pick_binding(bindings: list, storage_id: str, cluster_id: uuid.UUID | None, namespace: str):
    """Exact (cluster, namespace) binding, else the cluster-wide one, else None."""
    exact = wide = None
    for b in bindings:
        if str(b.storage_id) != str(storage_id) or b.cluster_id != cluster_id:
            continue
        if b.namespace == namespace:
            exact = b
        elif b.namespace is None:
            wide = b
    return exact or wide


def snapshot(binding) -> dict:
    return {
        "binding_id": str(binding.id),
        "type": binding.type,
        "claim_name": binding.claim_name,
        "namespace": binding.namespace,
        "nfs_server": binding.nfs_server,
        "nfs_path": binding.nfs_path,
        "sub_path": binding.sub_path,
    }


def stale_entries(entries: list[dict], bindings: list, cluster_id: uuid.UUID | None, namespace: str) -> list[str]:
    """Storage ids whose current binding differs from the entry's snapshot (deleted, edited or shadowed)."""
    stale: list[str] = []
    for e in entries:
        sid = str(e["storage_id"])
        b = pick_binding(bindings, sid, cluster_id, namespace)
        if (b is None or snapshot(b) != e.get("resolved")) and sid not in stale:
            stale.append(sid)
    return stale


def check_volume_names(entries: list[dict]) -> None:
    """Rendered volume names must be unique and must not shadow a reserved one (``shm``)."""
    seen: set[str] = set()
    for e in entries:
        name = e.get("volume_name") or LEGACY_VOLUME_NAME
        if name in _RESERVED_VOLUME_NAMES:
            raise ValueError(f"volume name {name!r} is reserved")
        if name in seen:
            raise ValueError(f"volume name {name!r} is used twice")
        seen.add(name)


def to_volumes(entries: list[dict] | None, *, force_read_only: bool = False) -> tuple[list[dict], list[dict]]:
    """(volumes, volumeMounts) for resolved entries. ``readOnly`` keys appear only when read-only,
    so a migrated read-write entry renders exactly like the old pvc_* columns."""
    volumes: list[dict] = []
    mounts: list[dict] = []
    for e in entries or []:
        r = e.get("resolved")
        if not r:
            continue
        name = e.get("volume_name") or LEGACY_VOLUME_NAME
        ro = force_read_only or bool(e.get("read_only"))
        if r["type"] == "pvc":
            src: dict = {"claimName": r["claim_name"]}
            key = "persistentVolumeClaim"
        else:
            src = {"server": r["nfs_server"], "path": r["nfs_path"]}
            key = "nfs"
        if ro:
            src["readOnly"] = True
        volumes.append({"name": name, key: src})
        mount: dict = {"name": name, "mountPath": e["mount_path"]}
        if ro:
            mount["readOnly"] = True
        if r.get("sub_path"):
            mount["subPath"] = r["sub_path"]
        mounts.append(mount)
    return volumes, mounts


def all_entries(row) -> list[dict]:
    """The base storages list, then each P/D pool's own list (row, request body or namespace)."""
    entries = list(getattr(row, "storages", None) or [])
    cfg = getattr(row, "pd_config", None) or {}
    for role in ROLES:
        entries += list((cfg.get(role) or {}).get("storages") or [])
    return entries


def entry_storage_ids(row) -> set[str]:
    """Every storage a recipe/deployment references, P/D pool overrides included."""
    return {str(e.get("storage_id")) for e in all_entries(row)}


def _weights(volume: dict, mount_path: str) -> tuple[list, list]:
    return [{"name": LEGACY_VOLUME_NAME, **volume}], [
        {"name": LEGACY_VOLUME_NAME, "mountPath": mount_path, "readOnly": True}
    ]


def legacy_pvc_volumes(claim: str | None, mount_path: str | None) -> tuple[list, list]:
    """Read-only weights PVC from a pre-052 benchmark snapshot (``pvc_name`` / ``pvc_mount_path``)."""
    if not claim or not mount_path:
        return [], []
    return _weights({"persistentVolumeClaim": {"claimName": claim, "readOnly": True}}, mount_path)


def nfs_override_volumes(params: dict | None) -> tuple[list, list] | None:
    """A benchmark run's explicit NFS override (``params.nfs_*``), or None when not given."""
    p = params or {}
    server, path, mount = p.get("nfs_server"), p.get("nfs_path"), p.get("nfs_mount_path")
    if not (server and path and mount):
        return None
    return _weights({"nfs": {"server": server, "path": path, "readOnly": True}}, mount)
```

- [ ] **Step 5: Run tests**

Run:
```bash
PYTHONPATH=. $PY -m pytest tests/test_storage_mounts.py -q
$PY -m ruff check app/services/storage_mounts.py tests/_storage.py tests/test_storage_mounts.py
```
Expected: PASS; `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/storage_mounts.py backend/tests/_storage.py backend/tests/test_storage_mounts.py
git commit -m "feat(storage): pure storage-list validation, binding pick and volume rendering"
```

---

### Task 3: DB storage store (resolve, stale, usage, bench, claims)

**Files:**
- Create: `backend/app/services/storage_store.py`
- Test: `backend/tests/test_storage_store.py`

**Interfaces:**
- Consumes: Task 1 models; Task 2 `validate_entries`, `pick_binding`, `snapshot`, `default_volume_name`, `to_volumes`, `all_entries`, `entry_storage_ids`, `stale_entries`, `check_volume_names`, `nfs_override_volumes`, `LEGACY_VOLUME_NAME`; `pd_serving.ROLES`; `k8s_for_cluster` from `app.services.clusters`; `K8sClient.list_pvcs` (added in Task 4 — only the mocks in this task's tests call it).
- Produces:
  - `class UnknownStorageError(ValueError)` (`.ids: list[str]`), `class UnresolvedStorageError(ValueError)` (`.names: list[str]`)
  - `null_safe_eq(column, value)` — SQL `column = value`, or `column IS NULL` when `value` is None
  - `PVC_LOOKUP_TIMEOUT = 5.0`; `async list_pvcs_timed(db, cluster_id, namespace) -> list[dict]` (raises, incl. `TimeoutError`); `async pvc_phases(db, cluster_id, namespace) -> dict[str, str] | None` (claim → phase, phase `None` → `"Pending"`; `None` when the lookup fails)
  - `async load_storages(db, ids: list[str]) -> dict[str, CustomStorage]`
  - `async bindings_for(db, storage_ids: list[str], cluster_id: uuid.UUID | None) -> list[CustomStorageBinding]`
  - `async resolve_entries(db, entries: list | None, cluster_id, namespace: str, *, previous: list | None = None) -> list[dict]` — every error it raises is a `ValueError` (`UnknownStorageError`, `UnresolvedStorageError`, invalid list, reserved/duplicate volume name); API callers map `ValueError` → 400
  - `async resolve_pool_storages(db, pd_config: dict | None, cluster_id, namespace, *, previous: dict | None = None) -> dict | None`
  - `async check_known(db, entries: list[dict]) -> None` (raises `UnknownStorageError`)
  - `async stale_storage_ids(db, dep) -> list[str]`
  - `async usage_index(db) -> dict[str, dict]` → `{sid: {"recipes": [{"id","name"}], "deployments": [{"id","model_name","binding_ids": set[str], "cluster_id": uuid|None, "namespace": str, "entries": [entry…]}], "clusters": [{"id","name"}]}}` (`entries` = that deployment's entries for `sid`, base + pools)
  - `async bench_volumes(db, cluster_id, namespace: str, params: dict | None) -> tuple[list, list]`
  - `async match_claims(db, cluster_id, namespace: str, claims: list[str]) -> dict[str, str]` (claim → storage_id)

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_storage_store.py
"""storage_store: resolution with snapshots, stale detection, bench volumes, claim matching."""

import types
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import storage_store
from app.services.storage_store import UnknownStorageError, UnresolvedStorageError, resolve_entries
from tests._storage import CLUSTER, SID, SID2, binding, legacy_entry


def _storage(sid=SID, name="llama-weights"):
    return types.SimpleNamespace(id=uuid.UUID(sid), name=name)


def _patch(storages, bindings):
    return (
        patch.object(storage_store, "load_storages", AsyncMock(return_value={str(s.id): s for s in storages})),
        patch.object(storage_store, "bindings_for", AsyncMock(return_value=bindings)),
    )


async def test_resolve_snapshots_and_names_volumes():
    b = binding(cluster_id=CLUSTER, namespace="prod")
    p1, p2 = _patch([_storage()], [b])
    with p1, p2:
        out = await resolve_entries(None, [{"storage_id": SID, "mount_path": "/models"}], CLUSTER, "prod")
    assert out == [{
        "storage_id": SID, "mount_path": "/models", "read_only": True,
        "volume_name": "st-llama-weights", "resolved": {**storage_store.snapshot(b)},
    }]


async def test_resolve_keeps_previous_volume_name():
    b = binding(cluster_id=None, namespace="default")
    p1, p2 = _patch([_storage()], [b])
    with p1, p2:
        out = await resolve_entries(
            None, [{"storage_id": SID, "mount_path": "/models", "read_only": False}], None, "default",
            previous=[legacy_entry("models-pvc", "/models")],
        )
    assert out[0]["volume_name"] == "model-weights"


async def test_resolve_unresolved_names_every_missing_storage():
    p1, p2 = _patch([_storage(), _storage(SID2, "hf-cache")], [])
    with p1, p2, pytest.raises(UnresolvedStorageError) as e:
        await resolve_entries(
            None, [{"storage_id": SID, "mount_path": "/a"}, {"storage_id": SID2, "mount_path": "/b"}], CLUSTER, "prod"
        )
    assert e.value.names == ["llama-weights", "hf-cache"]


async def test_resolve_unknown_storage():
    p1, p2 = _patch([], [])
    with p1, p2, pytest.raises(UnknownStorageError):
        await resolve_entries(None, [{"storage_id": SID, "mount_path": "/a"}], CLUSTER, "prod")


async def test_resolve_rejects_reserved_previous_volume_name():
    p1, p2 = _patch([_storage()], [binding(cluster_id=None, namespace="default")])
    with p1, p2, pytest.raises(ValueError, match="reserved"):
        await resolve_entries(
            None, [{"storage_id": SID, "mount_path": "/models"}], None, "default",
            previous=[{**legacy_entry("models-pvc", "/models"), "volume_name": "shm"}],
        )


async def test_resolve_empty_list_skips_db():
    with patch.object(storage_store, "load_storages", AsyncMock()) as load:
        assert await resolve_entries(None, [], CLUSTER, "prod") == []
    load.assert_not_awaited()


async def test_resolve_pool_storages_only_touches_roles_that_set_a_list():
    b = binding(cluster_id=None, namespace="default")
    p1, p2 = _patch([_storage()], [b])
    cfg = {"prefill": {"storages": [{"storage_id": SID, "mount_path": "/m"}]}, "decode": {"replicas": 1}}
    with p1, p2:
        out = await storage_store.resolve_pool_storages(None, cfg, None, "default")
    assert out["prefill"]["storages"][0]["resolved"]["binding_id"] == str(b.id)
    assert "storages" not in out["decode"]
    assert await storage_store.resolve_pool_storages(None, None, None, "default") is None


def _dep(entries, pd_config=None, cluster_id=CLUSTER, namespace="prod"):
    return types.SimpleNamespace(storages=entries, pd_config=pd_config, cluster_id=cluster_id, namespace=namespace)


async def test_not_stale_when_binding_unchanged():
    b = binding(cluster_id=CLUSTER, namespace="prod")
    entry = {"storage_id": SID, "mount_path": "/m", "read_only": True, "volume_name": "st-x",
             "resolved": storage_store.snapshot(b)}
    with patch.object(storage_store, "bindings_for", AsyncMock(return_value=[b])):
        assert await storage_store.stale_storage_ids(None, _dep([entry])) == []


async def test_stale_when_binding_edited_or_deleted():
    b = binding(cluster_id=CLUSTER, namespace="prod")
    entry = {"storage_id": SID, "mount_path": "/m", "read_only": True, "volume_name": "st-x",
             "resolved": storage_store.snapshot(b)}
    edited = binding(id=b.id, cluster_id=CLUSTER, namespace="prod", claim_name="other")
    with patch.object(storage_store, "bindings_for", AsyncMock(return_value=[edited])):
        assert await storage_store.stale_storage_ids(None, _dep([entry])) == [SID]
    with patch.object(storage_store, "bindings_for", AsyncMock(return_value=[])):
        assert await storage_store.stale_storage_ids(None, _dep([entry])) == [SID]


async def test_stale_when_more_specific_binding_appears():
    wide = binding(cluster_id=CLUSTER, namespace=None, type="nfs", claim_name=None, nfs_server="s", nfs_path="/p")
    entry = {"storage_id": SID, "mount_path": "/m", "read_only": True, "volume_name": "st-x",
             "resolved": storage_store.snapshot(wide)}
    exact = binding(cluster_id=CLUSTER, namespace="prod")
    with patch.object(storage_store, "bindings_for", AsyncMock(return_value=[wide, exact])):
        assert await storage_store.stale_storage_ids(None, _dep([entry])) == [SID]


async def test_stale_covers_pool_overrides():
    b = binding(cluster_id=CLUSTER, namespace="prod")
    entry = {"storage_id": SID2, "mount_path": "/m", "read_only": True, "volume_name": "st-x",
             "resolved": storage_store.snapshot(binding(storage_id=uuid.UUID(SID2)))}
    dep = _dep([], pd_config={"prefill": {"storages": [entry]}})
    with patch.object(storage_store, "bindings_for", AsyncMock(return_value=[b])):
        assert await storage_store.stale_storage_ids(None, dep) == [SID2]


async def test_bench_volumes_prefers_params_override():
    vols, mounts = await storage_store.bench_volumes(
        None, CLUSTER, "bench", {"nfs_server": "s", "nfs_path": "/p", "nfs_mount_path": "/m"}
    )
    assert vols[0]["nfs"]["server"] == "s" and mounts[0]["mountPath"] == "/m"


async def test_bench_volumes_from_cluster_bench_storage():
    cluster = types.SimpleNamespace(bench_storage_id=uuid.UUID(SID), bench_mount_path="/models")
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=cluster)))
    b = binding(cluster_id=CLUSTER, namespace=None, type="nfs", claim_name=None, nfs_server="s", nfs_path="/w")
    p1, p2 = _patch([_storage()], [b])
    with p1, p2:
        vols, mounts = await storage_store.bench_volumes(db, CLUSTER, "bench", {})
    assert vols == [{"name": "model-weights", "nfs": {"server": "s", "path": "/w", "readOnly": True}}]
    assert mounts == [{"name": "model-weights", "mountPath": "/models", "readOnly": True}]


async def test_bench_volumes_none_for_default_cluster_or_unresolved():
    assert await storage_store.bench_volumes(None, None, "bench", {}) == ([], [])
    cluster = types.SimpleNamespace(bench_storage_id=uuid.UUID(SID), bench_mount_path="/models")
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=cluster)))
    p1, p2 = _patch([_storage()], [])
    with p1, p2:
        assert await storage_store.bench_volumes(db, CLUSTER, "bench", {}) == ([], [])


def test_null_safe_eq():
    from app.db.models.custom_storage import CustomStorageBinding

    assert "IS NULL" in str(storage_store.null_safe_eq(CustomStorageBinding.cluster_id, None))
    assert "= :cluster_id" in str(storage_store.null_safe_eq(CustomStorageBinding.cluster_id, CLUSTER))


async def test_pvc_phases_maps_and_tolerates_failures():
    k8s = MagicMock(list_pvcs=AsyncMock(return_value=[{"name": "a", "phase": "Bound"}, {"name": "b", "phase": None}]))
    with patch.object(storage_store, "k8s_for_cluster", AsyncMock(return_value=k8s)):
        assert await storage_store.pvc_phases(None, CLUSTER, "prod") == {"a": "Bound", "b": "Pending"}
    k8s.list_pvcs.assert_awaited_once_with("prod")
    broken = MagicMock(list_pvcs=AsyncMock(side_effect=RuntimeError("forbidden")))
    with patch.object(storage_store, "k8s_for_cluster", AsyncMock(return_value=broken)):
        assert await storage_store.pvc_phases(None, CLUSTER, "prod") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. $PY -m pytest tests/test_storage_store.py -q`
Expected: FAIL — `ModuleNotFoundError: app.services.storage_store`

- [ ] **Step 3: Implement**

```python
# backend/app/services/storage_store.py
"""DB side of storages: resolve a list for a deploy target, detect stale
deployments, find usages, the benchmark default mount, importer matching and
the bounded live PVC lookup."""

from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.custom_k8s_cluster import CustomK8sCluster
from app.db.models.custom_model_deployment import CustomModelDeployment
from app.db.models.custom_serving_recipe import CustomServingRecipe
from app.db.models.custom_storage import CustomStorage, CustomStorageBinding
from app.services.clusters import k8s_for_cluster
from app.services.pd_serving import ROLES
from app.services.storage_mounts import (
    LEGACY_VOLUME_NAME,
    all_entries,
    check_volume_names,
    default_volume_name,
    entry_storage_ids,
    nfs_override_volumes,
    pick_binding,
    snapshot,
    stale_entries,
    to_volumes,
    validate_entries,
)

logger = logging.getLogger(__name__)

PVC_LOOKUP_TIMEOUT = 5.0


class UnknownStorageError(ValueError):
    def __init__(self, ids: list[str]):
        self.ids = ids
        super().__init__(f"unknown storage: {', '.join(ids)}")


class UnresolvedStorageError(ValueError):
    def __init__(self, names: list[str]):
        self.names = names
        super().__init__(
            f"no binding for storage {', '.join(names)} on this cluster/namespace; add one under Admin → Storage"
        )


def null_safe_eq(column, value):
    """``column = value``, or ``column IS NULL`` when ``value`` is None (NULL cluster = portal default)."""
    return column.is_(None) if value is None else column == value


async def load_storages(db: AsyncSession, ids: list[str]) -> dict[str, CustomStorage]:
    if not ids:
        return {}
    stmt = select(CustomStorage).where(CustomStorage.id.in_([uuid.UUID(i) for i in ids]))
    return {str(r.id): r for r in (await db.execute(stmt)).scalars().all()}


async def bindings_for(db: AsyncSession, storage_ids: list[str], cluster_id: uuid.UUID | None) -> list:
    if not storage_ids:
        return []
    stmt = select(CustomStorageBinding).where(
        CustomStorageBinding.storage_id.in_([uuid.UUID(i) for i in storage_ids]),
        null_safe_eq(CustomStorageBinding.cluster_id, cluster_id),
    )
    return list((await db.execute(stmt)).scalars().all())


async def check_known(db: AsyncSession, entries: list[dict]) -> None:
    ids = [e["storage_id"] for e in entries]
    known = await load_storages(db, ids)
    missing = [i for i in ids if i not in known]
    if missing:
        raise UnknownStorageError(missing)


async def resolve_entries(
    db: AsyncSession,
    entries: list | None,
    cluster_id: uuid.UUID | None,
    namespace: str,
    *,
    previous: list | None = None,
) -> list[dict]:
    """Validate ``entries`` and attach ``volume_name`` + ``resolved`` for the target.

    ``previous`` (the deployment's current list) keeps an existing volume name per
    storage — migrated entries stay ``model-weights`` so manifests don't change.
    Raises ``UnknownStorageError`` / ``UnresolvedStorageError``, or a plain
    ``ValueError`` for an invalid list or a reserved/duplicate volume name.
    """
    clean = validate_entries(entries)
    if not clean:
        return []
    ids = [e["storage_id"] for e in clean]
    storages = await load_storages(db, ids)
    missing = [i for i in ids if i not in storages]
    if missing:
        raise UnknownStorageError(missing)
    bindings = await bindings_for(db, ids, cluster_id)
    kept = {str(p.get("storage_id")): p.get("volume_name") for p in previous or [] if p.get("volume_name")}
    out: list[dict] = []
    unresolved: list[str] = []
    for e in clean:
        b = pick_binding(bindings, e["storage_id"], cluster_id, namespace)
        if b is None:
            unresolved.append(storages[e["storage_id"]].name)
            continue
        name = kept.get(e["storage_id"]) or default_volume_name(storages[e["storage_id"]].name)
        out.append({**e, "volume_name": name, "resolved": snapshot(b)})
    if unresolved:
        raise UnresolvedStorageError(unresolved)
    check_volume_names(out)
    return out


async def resolve_pool_storages(
    db: AsyncSession,
    pd_config: dict | None,
    cluster_id: uuid.UUID | None,
    namespace: str,
    *,
    previous: dict | None = None,
) -> dict | None:
    """Resolve each P/D pool's own ``storages`` override (pools without one inherit the base list)."""
    if pd_config is None:
        return None
    cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in pd_config.items()}
    for role in ROLES:
        pool = cfg.get(role)
        if not isinstance(pool, dict) or pool.get("storages") is None:
            continue
        prev = ((previous or {}).get(role) or {}).get("storages")
        pool["storages"] = await resolve_entries(db, pool["storages"], cluster_id, namespace, previous=prev)
    return cfg


async def stale_storage_ids(db: AsyncSession, dep) -> list[str]:
    """Storages whose current resolution differs from the deployment's snapshot."""
    entries = all_entries(dep)
    if not entries:
        return []
    ids = sorted({str(e["storage_id"]) for e in entries})
    bindings = await bindings_for(db, ids, dep.cluster_id)
    return stale_entries(entries, bindings, dep.cluster_id, dep.namespace)


async def usage_index(db: AsyncSession) -> dict[str, dict]:
    """Who references each storage (recipes, deployments incl. pools, cluster bench defaults).

    Deployment items carry ``binding_ids`` (bindings their snapshots point at) and
    ``cluster_id`` / ``namespace`` / ``entries`` so callers can compute staleness.
    """
    index: dict[str, dict] = {}

    def slot(sid: str) -> dict:
        return index.setdefault(sid, {"recipes": [], "deployments": [], "clusters": []})

    for r in (await db.execute(select(CustomServingRecipe))).scalars().all():
        for sid in entry_storage_ids(r):
            slot(sid)["recipes"].append({"id": str(r.id), "name": r.name})
    for d in (await db.execute(select(CustomModelDeployment))).scalars().all():
        per_storage: dict[str, list[dict]] = {}
        for e in all_entries(d):
            per_storage.setdefault(str(e["storage_id"]), []).append(e)
        for sid, entries in per_storage.items():
            slot(sid)["deployments"].append(
                {
                    "id": str(d.id),
                    "model_name": d.model_name,
                    "binding_ids": {str((e.get("resolved") or {}).get("binding_id")) for e in entries},
                    "cluster_id": d.cluster_id,
                    "namespace": d.namespace,
                    "entries": entries,
                }
            )
    for c in (await db.execute(select(CustomK8sCluster))).scalars().all():
        if c.bench_storage_id:
            slot(str(c.bench_storage_id))["clusters"].append({"id": str(c.id), "name": c.name})
    return index


async def bench_volumes(
    db: AsyncSession, cluster_id: uuid.UUID | None, namespace: str, params: dict | None
) -> tuple[list, list]:
    """Weights mount for a benchmark against a raw model_name.

    Run override (``params.nfs_*``) → the cluster's bench storage resolved for
    ``namespace`` (read-only, ``model-weights``) → nothing.
    """
    override = nfs_override_volumes(params)
    if override is not None:
        return override
    if cluster_id is None:
        return [], []
    cluster = (
        await db.execute(select(CustomK8sCluster).where(CustomK8sCluster.id == cluster_id))
    ).scalar_one_or_none()
    if cluster is None or not cluster.bench_storage_id or not cluster.bench_mount_path:
        return [], []
    entry = {"storage_id": str(cluster.bench_storage_id), "mount_path": cluster.bench_mount_path, "read_only": True}
    try:
        resolved = await resolve_entries(
            db, [entry], cluster_id, namespace, previous=[{**entry, "volume_name": LEGACY_VOLUME_NAME}]
        )
    except ValueError as e:  # unresolved / unknown storage: run without the mount
        logger.warning("bench storage for cluster %s not mounted: %s", cluster_id, e)
        return [], []
    return to_volumes(resolved, force_read_only=True)


async def match_claims(
    db: AsyncSession, cluster_id: uuid.UUID | None, namespace: str, claims: list[str]
) -> dict[str, str]:
    """claim name → storage id, for PVC bindings of this cluster/namespace (recipe import)."""
    if not claims:
        return {}
    stmt = select(CustomStorageBinding).where(
        CustomStorageBinding.type == "pvc",
        CustomStorageBinding.namespace == namespace,
        CustomStorageBinding.claim_name.in_(claims),
        null_safe_eq(CustomStorageBinding.cluster_id, cluster_id),
    )
    return {b.claim_name: str(b.storage_id) for b in (await db.execute(stmt)).scalars().all()}


async def list_pvcs_timed(db: AsyncSession, cluster_id: uuid.UUID | None, namespace: str) -> list[dict]:
    """``K8sClient.list_pvcs`` for the cluster, bounded by ``PVC_LOOKUP_TIMEOUT`` (raises on failure)."""
    k8s = await k8s_for_cluster(db, cluster_id)
    return await asyncio.wait_for(k8s.list_pvcs(namespace), timeout=PVC_LOOKUP_TIMEOUT)


async def pvc_phases(db: AsyncSession, cluster_id: uuid.UUID | None, namespace: str) -> dict[str, str] | None:
    """claim → phase for one namespace, or None when the cluster can't be read (best-effort)."""
    try:
        pvcs = await list_pvcs_timed(db, cluster_id, namespace)
    except Exception as e:  # noqa: BLE001 - status/warnings only
        logger.warning("PVC lookup failed for cluster %s/%s: %s", cluster_id, namespace, e)
        return None
    return {p["name"]: p.get("phase") or "Pending" for p in pvcs}
```

- [ ] **Step 4: Run tests**

Run:
```bash
PYTHONPATH=. $PY -m pytest tests/test_storage_store.py tests/test_storage_mounts.py -q
$PY -m ruff check app/services/storage_store.py tests/test_storage_store.py
```
Expected: PASS; `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/storage_store.py backend/tests/test_storage_store.py
git commit -m "feat(storage): resolve storages per cluster/namespace with snapshots and stale detection"
```

---

### Task 4: K8s PVC listing + Storage API

**Files:**
- Modify: `backend/app/clients/k8s.py` (add `list_pvcs` after `list_gpu_nodes`)
- Create: `backend/app/api/storages.py`
- Modify: `backend/app/main.py` (import + `app.include_router(storages.router)` after `gpu_profiles`)
- Test: `backend/tests/test_k8s_pvcs.py`, `backend/tests/test_storages_api.py`

**Interfaces:**
- Consumes: Task 3 `usage_index`, `load_storages`, `bindings_for`, `null_safe_eq`, `pvc_phases`, `list_pvcs_timed`, `PVC_LOOKUP_TIMEOUT`; Task 2 `validate_storage_name`, `snapshot`, `pick_binding`, `stale_entries`; `parse_cluster_id` from `app.services.gpu_profile_store`. (The K8s client is reached only through `storage_store`, so tests patch `app.services.storage_store.k8s_for_cluster`.)
- Produces:
  - `K8sClient.list_pvcs(namespace: str) -> list[{"name", "phase", "capacity", "storage_class", "access_modes"}]` (sorted by name)
  - HTTP (all `require_super_user`, prefix `/api/admin`):
    - `GET /storages?kind=&q=` → `{"storages": [{id, name, description, kind, created_by, updated_at, binding_count, bound_on: [{cluster_id, cluster_name, namespace}], recipe_count, deployment_count}]}` (`bound_on` sorted by cluster name, then namespace; `namespace` null = all namespaces)
    - `POST /storages` (201) / `GET /storages/{id}` / `PUT /storages/{id}` / `DELETE /storages/{id}` (409 when used)
    - `POST /storages/{id}/bindings` (201, 409 dup) / `PUT /storages/{id}/bindings/{bid}` / `DELETE /storages/{id}/bindings/{bid}?dry_run=true|false` → `{"deleted": bool, "affected_deployments": [{id, model_name}]}`
    - `GET /storages/{id}/bindings/status` → `{"status": {binding_id: "Bound"|"Pending"|"Lost"|"missing"|"error"|null}}`
    - `POST /storages/resolve` `{storage_ids, cluster_id, namespace}` → `{"results": [{storage_id, name, binding: snapshot|null, pvc_phase}]}`
    - `GET /k8s-clusters/{cluster_id}/pvcs?namespace=` → `{"pvcs": [...], "error": str|null}` (`cluster_id` may be `default`)
  - Storage detail shape: `{id, name, description, kind, created_by, updated_at, bindings: [binding…], usage: {recipes, deployments: [{id, model_name, stale: bool}], clusters}}` (`stale` = the deployment's snapshot for this storage no longer matches the binding it would resolve to now); binding shape: `{id, storage_id, cluster_id, cluster_name, namespace, type, claim_name, nfs_server, nfs_path, sub_path, updated_at}`.

- [ ] **Step 1: Write the failing K8s test**

```python
# backend/tests/test_k8s_pvcs.py
"""K8sClient.list_pvcs."""

from unittest.mock import AsyncMock, MagicMock, patch

from app.clients.k8s import K8sClient


def _pvc(name, phase, capacity="100Gi", sc="nfs-client", modes=("ReadWriteMany",)):
    p = MagicMock()
    p.metadata.name = name
    p.status.phase = phase
    p.status.capacity = {"storage": capacity}
    p.spec.storage_class_name = sc
    p.spec.access_modes = list(modes)
    return p


async def test_list_pvcs_sorted_with_phase_and_capacity():
    fake_api = MagicMock(close=AsyncMock())
    core = MagicMock()
    core.list_namespaced_persistent_volume_claim = AsyncMock(
        return_value=MagicMock(items=[_pvc("zeta", "Pending", None), _pvc("alpha", "Bound")])
    )
    with (
        patch.object(K8sClient, "_api_client", AsyncMock(return_value=fake_api)),
        patch("app.clients.k8s.client.CoreV1Api", return_value=core),
    ):
        out = await K8sClient().list_pvcs("serving")
    core.list_namespaced_persistent_volume_claim.assert_awaited_once_with(namespace="serving")
    assert [p["name"] for p in out] == ["alpha", "zeta"]
    assert out[0] == {
        "name": "alpha", "phase": "Bound", "capacity": "100Gi", "storage_class": "nfs-client",
        "access_modes": ["ReadWriteMany"],
    }
    assert out[1]["capacity"] is None
    fake_api.close.assert_awaited()
```

- [ ] **Step 2: Run to verify it fails, then implement `list_pvcs`**

Run: `PYTHONPATH=. $PY -m pytest tests/test_k8s_pvcs.py -q` → FAIL (`AttributeError: list_pvcs`).

Add to `K8sClient` (after `list_gpu_nodes`):

```python
    async def list_pvcs(self, namespace: str) -> list[dict]:
        """PVCs in ``namespace`` with phase / capacity, for storage bindings."""
        api_client = await self._api_client()
        try:
            core = client.CoreV1Api(api_client)
            res = await core.list_namespaced_persistent_volume_claim(namespace=namespace)
            out = []
            for p in res.items:
                status = p.status
                spec = p.spec
                out.append(
                    {
                        "name": p.metadata.name,
                        "phase": status.phase if status else None,
                        "capacity": (status.capacity or {}).get("storage") if status else None,
                        "storage_class": spec.storage_class_name if spec else None,
                        "access_modes": list(spec.access_modes or []) if spec else [],
                    }
                )
            return sorted(out, key=lambda x: x["name"])
        finally:
            await api_client.close()
```

Run again → PASS.

- [ ] **Step 3: Write the failing API tests**

```python
# backend/tests/test_storages_api.py
"""Storage + binding CRUD, delete protection, status, resolve, cluster PVC listing."""

import types
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.exc import IntegrityError

from app.services.storage_mounts import snapshot
from tests._storage import CLUSTER, SID, binding

NOW = datetime(2026, 10, 19, tzinfo=UTC)


def _result(scalar=None, all_rows=None):
    r = MagicMock()
    r.scalar_one_or_none.return_value = scalar
    r.scalars.return_value.all.return_value = all_rows or []
    return r


def _storage(**kw):
    base = dict(id=uuid.UUID(SID), name="llama-weights", description=None, kind="weights",
                created_by="ADMIN001", updated_by=None, created_at=NOW, updated_at=NOW)
    base.update(kw)
    return types.SimpleNamespace(**base)


def _usage(**kw):
    return patch("app.api.storages.usage_index", AsyncMock(return_value=kw))


def _k8s_patch(k8s):
    return patch("app.services.storage_store.k8s_for_cluster", AsyncMock(return_value=k8s))


def _cluster_names():
    names = MagicMock()
    names.scalars.return_value.all.return_value = [types.SimpleNamespace(id=CLUSTER, name="gpu-a")]
    return names


async def test_list_storages_with_counts_and_bound_on(client_for_user, super_user, mock_db):
    rows = [_storage(), _storage(id=uuid.uuid4(), name="hf-cache", kind="cache")]
    bindings = [binding(cluster_id=CLUSTER, namespace="prod"), binding()]
    mock_db.execute = AsyncMock(side_effect=[_result(all_rows=rows), _result(all_rows=bindings), _cluster_names()])
    usage = {SID: {"recipes": [{"id": "r", "name": "r"}], "deployments": [], "clusters": []}}
    with _usage(**usage):
        async with client_for_user(super_user) as client:
            resp = await client.get("/api/admin/storages", params={"kind": "weights"})
    assert resp.status_code == 200, resp.text
    items = resp.json()["storages"]
    assert [s["name"] for s in items] == ["llama-weights"]
    assert items[0]["binding_count"] == 2 and items[0]["recipe_count"] == 1 and items[0]["deployment_count"] == 0
    assert items[0]["bound_on"] == [
        {"cluster_id": None, "cluster_name": "default", "namespace": "default"},
        {"cluster_id": str(CLUSTER), "cluster_name": "gpu-a", "namespace": "prod"},
    ]


async def test_get_storage_usage_flags_stale_deployments(client_for_user, super_user, mock_db):
    b = binding(cluster_id=CLUSTER, namespace="prod")
    current = {"storage_id": SID, "mount_path": "/m", "read_only": True, "volume_name": "st-x", "resolved": snapshot(b)}
    edited = {**current, "resolved": {**snapshot(b), "claim_name": "old-pvc"}}
    mock_db.execute = AsyncMock(side_effect=[_result(scalar=_storage()), _result(all_rows=[b]), _cluster_names()])

    def dep(dep_id, name, entry):
        return {"id": dep_id, "model_name": name, "binding_ids": {str(b.id)}, "cluster_id": CLUSTER,
                "namespace": "prod", "entries": [entry]}

    deployments = [dep("d1", "fresh", current), dep("d2", "old", edited)]
    usage = {SID: {"recipes": [], "clusters": [], "deployments": deployments}}
    with _usage(**usage):
        async with client_for_user(super_user) as client:
            resp = await client.get(f"/api/admin/storages/{SID}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["usage"]["deployments"] == [
        {"id": "d1", "model_name": "fresh", "stale": False},
        {"id": "d2", "model_name": "old", "stale": True},
    ]
    assert resp.json()["bindings"][0]["cluster_name"] == "gpu-a"


async def test_create_storage_validates_name_and_409(client_for_user, super_user, mock_db):
    async with client_for_user(super_user) as client:
        assert (await client.post("/api/admin/storages", json={"name": "Bad Name"})).status_code == 422
    mock_db.execute = AsyncMock(return_value=_result(scalar=None))
    async with client_for_user(super_user) as client:
        resp = await client.post("/api/admin/storages", json={"name": "llama-weights", "kind": "weights"})
    assert resp.status_code == 201, resp.text
    mock_db.add.assert_called_once()
    mock_db.execute = AsyncMock(return_value=_result(scalar=_storage()))
    async with client_for_user(super_user) as client:
        assert (await client.post("/api/admin/storages", json={"name": "llama-weights"})).status_code == 409


async def test_delete_storage_in_use_by_pool_or_bench_409(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(scalar=_storage()))
    for usage in (
        {SID: {"recipes": [], "deployments": [{"id": "d", "model_name": "m", "binding_ids": {"b"}}], "clusters": []}},
        {SID: {"recipes": [], "deployments": [], "clusters": [{"id": "c", "name": "gpu-a"}]}},
    ):
        with _usage(**usage):
            async with client_for_user(super_user) as client:
                resp = await client.delete(f"/api/admin/storages/{SID}")
        assert resp.status_code == 409
    with _usage():
        async with client_for_user(super_user) as client:
            resp = await client.delete(f"/api/admin/storages/{SID}")
    assert resp.status_code == 200
    mock_db.delete.assert_awaited_once()


async def test_binding_validation(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(scalar=_storage()))
    url = f"/api/admin/storages/{SID}/bindings"
    bad = [
        {"type": "pvc", "claim_name": "c"},  # pvc needs namespace
        {"type": "pvc", "namespace": "prod"},  # pvc needs claim
        {"type": "nfs", "nfs_server": "s"},  # nfs needs path
        {"type": "nfs", "nfs_server": "s", "nfs_path": "/p", "claim_name": "c"},
        {"type": "nfs", "nfs_server": "s", "nfs_path": "/p", "namespace": "Bad_NS"},
        {"type": "nfs", "nfs_server": "s", "nfs_path": "/p", "sub_path": "/abs"},
        {"type": "nfs", "nfs_server": "s", "nfs_path": "/p", "sub_path": "../up"},
    ]
    async with client_for_user(super_user) as client:
        for body in bad:
            assert (await client.post(url, json=body)).status_code == 422, body


async def test_create_binding_201_and_duplicate_scope_409(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(side_effect=[_result(scalar=_storage()), _result(scalar=None), _cluster_names()])
    body = {"cluster_id": str(CLUSTER), "namespace": "prod", "type": "pvc", "claim_name": "models-pvc"}
    async with client_for_user(super_user) as client:
        resp = await client.post(f"/api/admin/storages/{SID}/bindings", json=body)
    assert resp.status_code == 201, resp.text
    assert resp.json()["claim_name"] == "models-pvc" and resp.json()["cluster_id"] == str(CLUSTER)
    assert resp.json()["cluster_name"] == "gpu-a"

    mock_db.execute = AsyncMock(side_effect=[_result(scalar=_storage()), _result(scalar=binding())])
    async with client_for_user(super_user) as client:
        assert (await client.post(f"/api/admin/storages/{SID}/bindings", json=body)).status_code == 409

    mock_db.execute = AsyncMock(side_effect=[_result(scalar=_storage()), _result(scalar=None)])
    mock_db.flush = AsyncMock(side_effect=IntegrityError("x", {}, Exception()))
    async with client_for_user(super_user) as client:
        assert (await client.post(f"/api/admin/storages/{SID}/bindings", json=body)).status_code == 409


async def test_delete_binding_dry_run_lists_affected(client_for_user, super_user, mock_db):
    b = binding()
    mock_db.execute = AsyncMock(return_value=_result(scalar=b))
    deployments = [
        {"id": "d1", "model_name": "llama", "binding_ids": {str(b.id)}},
        {"id": "d2", "model_name": "other", "binding_ids": {"x"}},
    ]
    usage = {SID: {"recipes": [], "deployments": deployments, "clusters": []}}
    with _usage(**usage):
        async with client_for_user(super_user) as client:
            resp = await client.delete(f"/api/admin/storages/{SID}/bindings/{b.id}", params={"dry_run": "true"})
    assert resp.json() == {"deleted": False, "affected_deployments": [{"id": "d1", "model_name": "llama"}]}
    mock_db.delete.assert_not_awaited()
    with _usage(**usage):
        async with client_for_user(super_user) as client:
            resp = await client.delete(f"/api/admin/storages/{SID}/bindings/{b.id}")
    assert resp.json()["deleted"] is True
    mock_db.delete.assert_awaited_once()


async def test_binding_status_groups_lookups_and_reports_missing(client_for_user, super_user, mock_db):
    b1 = binding(claim_name="models-pvc", namespace="prod")
    b2 = binding(claim_name="gone", namespace="prod")
    b3 = binding(type="nfs", claim_name=None, namespace=None, nfs_server="s", nfs_path="/p")
    mock_db.execute = AsyncMock(side_effect=[_result(scalar=_storage()), _result(all_rows=[b1, b2, b3])])
    k8s = MagicMock()
    k8s.list_pvcs = AsyncMock(return_value=[{"name": "models-pvc", "phase": "Bound"}])
    with _k8s_patch(k8s):
        async with client_for_user(super_user) as client:
            resp = await client.get(f"/api/admin/storages/{SID}/bindings/status")
    assert resp.json()["status"] == {str(b1.id): "Bound", str(b2.id): "missing", str(b3.id): None}
    k8s.list_pvcs.assert_awaited_once_with("prod")


async def test_resolve_endpoint(client_for_user, super_user, mock_db):
    b = binding(cluster_id=CLUSTER, namespace="prod")
    k8s = MagicMock(list_pvcs=AsyncMock(return_value=[{"name": "models-pvc", "phase": "Pending"}]))
    with (
        patch("app.api.storages.load_storages", AsyncMock(return_value={SID: _storage()})),
        patch("app.api.storages.bindings_for", AsyncMock(return_value=[b])),
        _k8s_patch(k8s),
    ):
        async with client_for_user(super_user) as client:
            resp = await client.post(
                "/api/admin/storages/resolve",
                json={"storage_ids": [SID], "cluster_id": str(CLUSTER), "namespace": "prod"},
            )
    assert resp.status_code == 200, resp.text
    [r] = resp.json()["results"]
    assert r["name"] == "llama-weights" and r["binding"]["claim_name"] == "models-pvc" and r["pvc_phase"] == "Pending"


async def test_cluster_pvcs_reports_errors_without_failing(client_for_user, super_user, mock_db):
    k8s = MagicMock(list_pvcs=AsyncMock(side_effect=RuntimeError("forbidden")))
    with _k8s_patch(k8s):
        async with client_for_user(super_user) as client:
            resp = await client.get("/api/admin/k8s-clusters/default/pvcs", params={"namespace": "prod"})
    assert resp.status_code == 200
    assert resp.json() == {"pvcs": [], "error": "forbidden"}


async def test_storage_api_is_super_user_only(client_for_user, regular_user, mock_db):
    async with client_for_user(regular_user) as client:
        assert (await client.get("/api/admin/storages")).status_code == 403
        assert (await client.post("/api/admin/storages", json={"name": "x"})).status_code == 403
```

- [ ] **Step 4: Run to verify they fail**

Run: `PYTHONPATH=. $PY -m pytest tests/test_storages_api.py -q`
Expected: FAIL — 404s (router not registered)

- [ ] **Step 5: Implement the API**

```python
# backend/app/api/storages.py
"""Storage objects + per-cluster/namespace bindings (Super User only).

A storage is mounted by recipes/deployments by id; its binding for the deploy
target decides the concrete PVC or NFS volume (see services/storage_store).
"""

import re
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_super_user
from app.db.models.custom_k8s_cluster import CustomK8sCluster
from app.db.models.custom_storage import CustomStorage, CustomStorageBinding
from app.db.models.custom_user import CustomUser
from app.db.session import get_db
from app.services.gpu_profile_store import parse_cluster_id
from app.services.storage_mounts import pick_binding, snapshot, stale_entries, validate_storage_name
from app.services.storage_store import (
    PVC_LOOKUP_TIMEOUT,
    bindings_for,
    list_pvcs_timed,
    load_storages,
    null_safe_eq,
    pvc_phases,
    usage_index,
)

router = APIRouter(prefix="/api/admin", tags=["storages"])

_DNS_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")


class StorageBody(BaseModel):
    name: str
    description: str | None = None
    kind: Literal["weights", "cache", "data", "other"] = "weights"

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return validate_storage_name(v)


class BindingBody(BaseModel):
    cluster_id: str | None = None  # None / "default" = portal default cluster
    namespace: str | None = None  # None = whole cluster (NFS only)
    type: Literal["pvc", "nfs"]
    claim_name: str | None = None
    nfs_server: str | None = None
    nfs_path: str | None = None
    sub_path: str | None = None

    @field_validator("namespace", "claim_name", "nfs_server", "nfs_path", "sub_path")
    @classmethod
    def _blank_is_none(cls, v: str | None) -> str | None:
        v = (v or "").strip()
        return v or None

    @model_validator(mode="after")
    def _target(self):
        if self.namespace and not _DNS_LABEL.match(self.namespace):
            raise ValueError("namespace must be a DNS label")
        if self.type == "pvc":
            if not self.claim_name or not self.namespace:
                raise ValueError("a pvc binding needs claim_name and namespace")
            if self.nfs_server or self.nfs_path:
                raise ValueError("a pvc binding takes no nfs_server/nfs_path")
        else:
            if not self.nfs_server or not self.nfs_path:
                raise ValueError("an nfs binding needs nfs_server and nfs_path")
            if self.claim_name:
                raise ValueError("an nfs binding takes no claim_name")
        if self.sub_path and (self.sub_path.startswith("/") or ".." in self.sub_path.split("/")):
            raise ValueError("sub_path must be relative and must not contain ..")
        return self


class ResolveBody(BaseModel):
    storage_ids: list[str]
    cluster_id: str | None = None
    namespace: str = "default"


def _cid(raw: str | None) -> uuid.UUID | None:
    try:
        return parse_cluster_id(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="cluster_id must be a UUID or 'default'")


def _uuid_or_404(raw: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"{what} not found")


def _serialize(s: CustomStorage) -> dict:
    return {
        "id": str(s.id),
        "name": s.name,
        "description": s.description,
        "kind": s.kind,
        "created_by": s.created_by,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


def _cluster_label(cluster_id: uuid.UUID | None, cluster_names: dict) -> str:
    return cluster_names.get(cluster_id, "default") if cluster_id else "default"


def _serialize_binding(b: CustomStorageBinding, cluster_names: dict) -> dict:
    return {
        "id": str(b.id),
        "storage_id": str(b.storage_id),
        "cluster_id": str(b.cluster_id) if b.cluster_id else None,
        "cluster_name": _cluster_label(b.cluster_id, cluster_names),
        "namespace": b.namespace,
        "type": b.type,
        "claim_name": b.claim_name,
        "nfs_server": b.nfs_server,
        "nfs_path": b.nfs_path,
        "sub_path": b.sub_path,
        "updated_at": b.updated_at.isoformat() if getattr(b, "updated_at", None) else None,
    }


async def _storage_or_404(db: AsyncSession, storage_id: str) -> CustomStorage:
    sid = _uuid_or_404(storage_id, "Storage")
    row = (await db.execute(select(CustomStorage).where(CustomStorage.id == sid))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Storage not found")
    return row


async def _cluster_names(db: AsyncSession) -> dict:
    return {c.id: c.name for c in (await db.execute(select(CustomK8sCluster))).scalars().all()}


def _public_usage(u: dict | None, bindings: list | None = None) -> dict:
    """Usage for responses. With ``bindings`` (all of this storage's) each deployment gets ``stale``."""
    u = u or {"recipes": [], "deployments": [], "clusters": []}
    deployments = []
    for d in u["deployments"]:
        item = {"id": d["id"], "model_name": d["model_name"]}
        if bindings is not None:
            item["stale"] = bool(stale_entries(d["entries"], bindings, d["cluster_id"], d["namespace"]))
        deployments.append(item)
    return {"recipes": u["recipes"], "deployments": deployments, "clusters": u["clusters"]}


@router.get("/storages")
async def list_storages(
    kind: str | None = None,
    q: str | None = None,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    rows = list((await db.execute(select(CustomStorage).order_by(CustomStorage.name))).scalars().all())
    bindings = list((await db.execute(select(CustomStorageBinding))).scalars().all())
    names = await _cluster_names(db)
    usage = await usage_index(db)
    bound_on: dict[str, list[dict]] = {}
    for b in bindings:
        bound_on.setdefault(str(b.storage_id), []).append(
            {
                "cluster_id": str(b.cluster_id) if b.cluster_id else None,
                "cluster_name": _cluster_label(b.cluster_id, names),
                "namespace": b.namespace,
            }
        )
    needle = (q or "").strip().lower()
    out = []
    for s in rows:
        if kind and s.kind != kind:
            continue
        if needle and needle not in s.name and needle not in (s.description or "").lower():
            continue
        u = usage.get(str(s.id)) or {"recipes": [], "deployments": []}
        mine = sorted(bound_on.get(str(s.id), []), key=lambda x: (x["cluster_name"], x["namespace"] or ""))
        out.append(
            {
                **_serialize(s),
                "binding_count": len(mine),
                "bound_on": mine,
                "recipe_count": len(u["recipes"]),
                "deployment_count": len(u["deployments"]),
            }
        )
    return {"storages": out}


@router.post("/storages", status_code=status.HTTP_201_CREATED)
async def create_storage(
    body: StorageBody,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    clash = (await db.execute(select(CustomStorage).where(CustomStorage.name == body.name))).scalar_one_or_none()
    if clash is not None:
        raise HTTPException(status_code=409, detail=f"Storage '{body.name}' already exists")
    row = CustomStorage(id=uuid.uuid4(), created_by=user.user_id, updated_by=user.user_id, **body.model_dump())
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        raise HTTPException(status_code=409, detail=f"Storage '{body.name}' already exists")
    return _serialize(row)


@router.post("/storages/resolve")
async def resolve_storages(
    body: ResolveBody,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Deploy-dialog preview: the binding each storage resolves to, plus the PVC phase."""
    cid = _cid(body.cluster_id)
    ids = []
    for raw in body.storage_ids:
        try:
            ids.append(str(uuid.UUID(raw)))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"invalid storage id: {raw}")
    storages = await load_storages(db, ids)
    bindings = await bindings_for(db, ids, cid)
    phases: dict[str, str] | None = None
    looked_up = False
    results = []
    for sid in ids:
        b = pick_binding(bindings, sid, cid, body.namespace)
        phase = None
        if b is not None and b.type == "pvc":
            if not looked_up:
                phases, looked_up = await pvc_phases(db, cid, body.namespace), True
            phase = phases.get(b.claim_name, "missing") if phases is not None else "error"
        results.append(
            {
                "storage_id": sid,
                "name": storages[sid].name if sid in storages else None,
                "binding": snapshot(b) if b is not None else None,
                "pvc_phase": phase,
            }
        )
    return {"results": results}


@router.get("/storages/{storage_id}")
async def get_storage(
    storage_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    row = await _storage_or_404(db, storage_id)
    bindings = list(
        (await db.execute(select(CustomStorageBinding).where(CustomStorageBinding.storage_id == row.id)))
        .scalars()
        .all()
    )
    names = await _cluster_names(db)
    usage = (await usage_index(db)).get(str(row.id))
    return {
        **_serialize(row),
        "bindings": [_serialize_binding(b, names) for b in bindings],
        "usage": _public_usage(usage, bindings),
    }


@router.put("/storages/{storage_id}")
async def update_storage(
    storage_id: str,
    body: StorageBody,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    row = await _storage_or_404(db, storage_id)
    clash = (
        await db.execute(select(CustomStorage).where(CustomStorage.name == body.name, CustomStorage.id != row.id))
    ).scalar_one_or_none()
    if clash is not None:
        raise HTTPException(status_code=409, detail=f"Storage '{body.name}' already exists")
    for k, v in body.model_dump().items():
        setattr(row, k, v)
    row.updated_by = user.user_id
    await db.flush()
    await db.refresh(row)
    return _serialize(row)


@router.delete("/storages/{storage_id}")
async def delete_storage(
    storage_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    row = await _storage_or_404(db, storage_id)
    usage = _public_usage((await usage_index(db)).get(str(row.id)))
    if usage["recipes"] or usage["deployments"] or usage["clusters"]:
        raise HTTPException(status_code=409, detail={"message": "Storage is in use", "usage": usage})
    await db.delete(row)
    await db.flush()
    return {"deleted": True, "id": storage_id}


async def _binding_clash(db: AsyncSession, sid: uuid.UUID, body: BindingBody, cid, exclude: uuid.UUID | None):
    stmt = select(CustomStorageBinding).where(
        CustomStorageBinding.storage_id == sid,
        null_safe_eq(CustomStorageBinding.cluster_id, cid),
        null_safe_eq(CustomStorageBinding.namespace, body.namespace),
    )
    if exclude is not None:
        stmt = stmt.where(CustomStorageBinding.id != exclude)
    return (await db.execute(stmt)).scalar_one_or_none()


_SCOPE_TAKEN = "A binding for this cluster/namespace already exists"


@router.post("/storages/{storage_id}/bindings", status_code=status.HTTP_201_CREATED)
async def create_binding(
    storage_id: str,
    body: BindingBody,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    storage = await _storage_or_404(db, storage_id)
    cid = _cid(body.cluster_id)
    if await _binding_clash(db, storage.id, body, cid, None) is not None:
        raise HTTPException(status_code=409, detail=_SCOPE_TAKEN)
    row = CustomStorageBinding(
        id=uuid.uuid4(),
        storage_id=storage.id,
        cluster_id=cid,
        created_by=user.user_id,
        updated_by=user.user_id,
        **body.model_dump(exclude={"cluster_id"}),
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        raise HTTPException(status_code=409, detail=_SCOPE_TAKEN)
    return _serialize_binding(row, await _cluster_names(db))


async def _binding_or_404(db: AsyncSession, storage_id: str, binding_id: str) -> CustomStorageBinding:
    sid = _uuid_or_404(storage_id, "Storage")
    bid = _uuid_or_404(binding_id, "Binding")
    row = (
        await db.execute(
            select(CustomStorageBinding).where(CustomStorageBinding.id == bid, CustomStorageBinding.storage_id == sid)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Binding not found")
    return row


@router.put("/storages/{storage_id}/bindings/{binding_id}")
async def update_binding(
    storage_id: str,
    binding_id: str,
    body: BindingBody,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    row = await _binding_or_404(db, storage_id, binding_id)
    cid = _cid(body.cluster_id)
    if await _binding_clash(db, row.storage_id, body, cid, row.id) is not None:
        raise HTTPException(status_code=409, detail=_SCOPE_TAKEN)
    for k, v in body.model_dump(exclude={"cluster_id"}).items():
        setattr(row, k, v)
    row.cluster_id = cid
    row.updated_by = user.user_id
    await db.flush()
    await db.refresh(row)
    return _serialize_binding(row, await _cluster_names(db))


@router.delete("/storages/{storage_id}/bindings/{binding_id}")
async def delete_binding(
    storage_id: str,
    binding_id: str,
    dry_run: bool = False,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Running deployments keep their snapshot; ``affected_deployments`` are the
    ones resolved through this binding (they turn stale and fail to redeploy)."""
    row = await _binding_or_404(db, storage_id, binding_id)
    usage = (await usage_index(db)).get(str(row.storage_id)) or {"deployments": []}
    affected = [
        {"id": d["id"], "model_name": d["model_name"]} for d in usage["deployments"] if str(row.id) in d["binding_ids"]
    ]
    if dry_run:
        return {"deleted": False, "affected_deployments": affected}
    await db.delete(row)
    await db.flush()
    return {"deleted": True, "affected_deployments": affected}


@router.get("/storages/{storage_id}/bindings/status")
async def binding_status(
    storage_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Live PVC phase per pvc binding (one lookup per cluster × namespace); nfs → null."""
    row = await _storage_or_404(db, storage_id)
    bindings = (
        await db.execute(select(CustomStorageBinding).where(CustomStorageBinding.storage_id == row.id))
    ).scalars().all()
    groups: dict[tuple, dict | None] = {}
    out: dict[str, str | None] = {}
    for b in bindings:
        if b.type != "pvc":
            out[str(b.id)] = None
            continue
        key = (b.cluster_id, b.namespace)
        if key not in groups:
            groups[key] = await pvc_phases(db, b.cluster_id, b.namespace)
        phases = groups[key]
        out[str(b.id)] = "error" if phases is None else phases.get(b.claim_name, "missing")
    return {"status": out}


@router.get("/k8s-clusters/{cluster_id}/pvcs")
async def list_cluster_pvcs(
    cluster_id: str,
    namespace: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """PVCs of one namespace for the binding dialog; failures come back in ``error``."""
    cid = _cid(cluster_id)
    try:
        pvcs = await list_pvcs_timed(db, cid, namespace)
    except TimeoutError:
        return {"pvcs": [], "error": f"PVC lookup timed out after {PVC_LOOKUP_TIMEOUT:g}s"}
    except Exception as e:  # noqa: BLE001 - the dialog falls back to free text
        return {"pvcs": [], "error": str(e) or type(e).__name__}
    return {"pvcs": pvcs, "error": None}
```

Register it in `backend/app/main.py`: add `storages` to the `from app.api import …` list and `app.include_router(storages.router)` right after `app.include_router(gpu_profiles.router)`.

- [ ] **Step 6: Run tests**

Run:
```bash
PYTHONPATH=. $PY -m pytest tests/test_storages_api.py tests/test_k8s_pvcs.py tests/test_storage_store.py -q
$PY -m ruff check app/clients/k8s.py app/api/storages.py app/main.py tests/test_k8s_pvcs.py tests/test_storages_api.py
```
Expected: PASS; `All checks passed!`. If `test_list_storages_with_counts_and_bound_on` fails because `usage_index` was awaited with the real DB, check the patch target is `app.api.storages.usage_index` (imported name). K8s lookups are patched at `app.services.storage_store.k8s_for_cluster` (the API reaches K8s only through `pvc_phases` / `list_pvcs_timed`).

- [ ] **Step 7: Commit**

```bash
git add backend/app/clients/k8s.py backend/app/api/storages.py backend/app/main.py backend/tests/test_k8s_pvcs.py backend/tests/test_storages_api.py
git commit -m "feat(storage): storage/binding API, live PVC status, resolve preview, cluster PVC listing"
```

---

### Task 5: Manifests render storages (aggregated + P/D pools)

**Files:**
- Modify: `backend/app/services/model_deployment_manifests.py:103-113` (`_volumes`)
- Modify: `backend/app/services/pd_serving.py` (`RoleOverride` fields + `_blank_is_none` list, `POOL_FIELDS`)
- Test: `backend/tests/test_storage_manifests.py`; update existing tests that pass `pvc_name=`/`pvc_mount_path=` into manifest builders (`grep -ln "pvc_name" backend/tests`)

**Interfaces:**
- Consumes: Task 2 `to_volumes`, `validate_entries`.
- Produces: `RoleOverride.storages: list[dict] | None` (the pydantic pool model at `pd_serving.py:74`, validated by `validate_entries`; `None` = inherit); `"storages"` in `POOL_FIELDS` (so `pool_row` falls back to the base list). `RoleSpec` (the frozen dataclass at `pd_serving.py:218`) is not touched.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_storage_manifests.py
"""Deployment manifests mount the resolved storages; migrated rows render unchanged."""

import pytest

from app.db.models.custom_model_deployment import CustomModelDeployment
from app.services import pd_serving
from app.services.model_deployment_manifests import build_all
from tests._storage import SID, SID2, legacy_entry


def _dep(**kw) -> CustomModelDeployment:
    base = dict(
        model_name="llama", namespace="prod", image="vllm/vllm-openai:v0.9", replicas=1, gpu_count=1,
        gpu_resource_key="nvidia.com/gpu", model_path="/models/llama", ingress_host="llama.local",
        ingress_path="/", ingress_class="nginx", engine="vllm", serving_mode="aggregated", storages=[],
    )
    base.update(kw)
    return CustomModelDeployment(**base)


def _pod(manifests, kind="Deployment"):
    return next(m for m in manifests if m["kind"] == kind)["spec"]["template"]["spec"]


def test_migrated_deployment_renders_exactly_the_old_pvc_volume():
    pod = _pod(build_all(_dep(storages=[legacy_entry("models-pvc", "/models")])))
    assert pod["volumes"] == [{"name": "model-weights", "persistentVolumeClaim": {"claimName": "models-pvc"}}]
    assert pod["containers"][0]["volumeMounts"] == [{"name": "model-weights", "mountPath": "/models"}]


def test_multiple_storages_and_shm():
    nfs = {
        "storage_id": SID2, "mount_path": "/root/.cache/huggingface", "read_only": False, "volume_name": "st-hf-cache",
        "resolved": {"binding_id": "b", "type": "nfs", "claim_name": None, "namespace": None,
                     "nfs_server": "10.0.0.5", "nfs_path": "/cache", "sub_path": None},
    }
    dep = _dep(storages=[legacy_entry("models-pvc", "/models"), nfs], runtime={"shm_size_gi": 16})
    pod = _pod(build_all(dep))
    assert [v["name"] for v in pod["volumes"]] == ["model-weights", "st-hf-cache", "shm"]
    assert [m["mountPath"] for m in pod["containers"][0]["volumeMounts"]] == [
        "/models", "/root/.cache/huggingface", "/dev/shm",
    ]


def test_no_storages_no_volumes():
    pod = _pod(build_all(_dep()))
    assert pod["volumes"] == [] and pod["containers"][0]["volumeMounts"] == []


def test_pool_override_replaces_base_list():
    base = legacy_entry("models-pvc", "/models")
    prefill_only = {**legacy_entry("prefill-pvc", "/models", storage_id=SID2), "volume_name": "st-prefill"}
    dep = _dep(
        serving_mode="pd", storages=[base],
        pd_config={"prefill": {"replicas": 1, "storages": [prefill_only]}, "decode": {"replicas": 1}},
    )
    pools = {d["metadata"]["labels"][pd_serving.LABEL_PD_ROLE]: d for d in build_all(dep) if d["kind"] == "Deployment"}
    pre = pools["prefill"]["spec"]["template"]["spec"]
    dec = pools["decode"]["spec"]["template"]["spec"]
    assert pre["volumes"][0]["persistentVolumeClaim"]["claimName"] == "prefill-pvc"
    assert dec["volumes"][0]["persistentVolumeClaim"]["claimName"] == "models-pvc"


def test_role_override_validates_storages():
    assert pd_serving.RoleOverride(storages=None).storages is None
    assert pd_serving.RoleOverride(storages=[{"storage_id": SID, "mount_path": "/m/"}]).storages == [
        {"storage_id": SID, "mount_path": "/m", "read_only": True}
    ]
    with pytest.raises(ValueError):
        pd_serving.RoleOverride(storages=[{"storage_id": SID, "mount_path": "rel"}])
    assert "storages" in pd_serving.POOL_FIELDS
    assert "pvc_name" not in pd_serving.POOL_FIELDS
    assert "pvc_name" not in pd_serving.RoleOverride.model_fields
```

Note: check the pool Deployment labels key in `test_pd_serving.py` if `LABEL_PD_ROLE` is not on `metadata.labels`. (`pytest.raises(ValueError)` catches pydantic's `ValidationError`, a `ValueError` subclass.)

Note: role entries in `pd_config` are validated by `RoleOverride` *without* their `resolved` key, so the resolution in Task 7 writes resolved role lists **after** `validate_pd_config`. The manifest test builds the row directly and bypasses validation.

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=. $PY -m pytest tests/test_storage_manifests.py -q`
Expected: FAIL (volumes empty for the migrated row; `RoleOverride` has no `storages`)

- [ ] **Step 3: Implement**

`model_deployment_manifests.py` — replace `_volumes`:

```python
def _volumes(dep: CustomModelDeployment) -> tuple[list, list]:
    """(volumes, volumeMounts): the resolved storages, then ``/dev/shm`` when runtime asks."""
    volumes, mounts = to_volumes(getattr(dep, "storages", None) or [])
    shm = _runtime(dep).get("shm_size_gi")
    if shm:
        volumes.append({"name": "shm", "emptyDir": {"medium": "Memory", "sizeLimit": f"{int(shm)}Gi"}})
        mounts.append({"name": "shm", "mountPath": "/dev/shm"})
    return volumes, mounts
```

and add `from app.services.storage_mounts import to_volumes` to the imports.

`pd_serving.py`:
- In `class RoleOverride(BaseModel)` (line ~74) replace `pvc_name: str | None = None` / `pvc_mount_path: str | None = None` with `storages: list[dict] | None = None`.
- Remove `"pvc_name", "pvc_mount_path"` from its `_blank_is_none` field list and add to the same class:

```python
    @field_validator("storages")
    @classmethod
    def _storages(cls, v):
        # Imported here: storage_mounts imports ROLES from this module at load time.
        from app.services.storage_mounts import validate_entries

        return None if v is None else validate_entries(v)
```

- In `POOL_FIELDS` replace `"pvc_name", "pvc_mount_path"` with `"storages"`.
- Do **not** add a module-level `storage_mounts` import to `pd_serving.py` (circular import: `storage_mounts` → `pd_serving.ROLES`).

`pool_row` needs no change: it copies `ov["storages"]` when not `None`, else `dep.storages`.

- [ ] **Step 4: Update the existing tests that used PVC columns**

Run `grep -n "pvc_name\|pvc_mount_path" backend/tests/*.py`. For each test that builds a deployment/recipe and asserts a `model-weights` volume, replace `pvc_name="X", pvc_mount_path="/Y"` with `storages=[legacy_entry("X", "/Y")]` (import from `tests._storage`). Tests in `test_recipe_import.py`, `test_bench_external_clone.py`, `test_benchmarks_ephemeral.py`, `test_serving_recipes.py`, `test_model_deployments_engine.py` are handled in Tasks 7–10; leave them for now if they still pass.

- [ ] **Step 5: Run tests**

Run:
```bash
PYTHONPATH=. $PY -m pytest tests/test_storage_manifests.py tests/test_pd_serving.py tests/test_serving_engines.py tests/test_serving_probes.py tests/test_reconcile_pd.py tests/test_llmd_links.py tests/test_storage_mounts.py -q
$PY -m ruff check app/services/model_deployment_manifests.py app/services/pd_serving.py tests/test_storage_manifests.py $(git diff --name-only -- tests | sed 's|^backend/||')
```
Expected: PASS; `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/model_deployment_manifests.py backend/app/services/pd_serving.py backend/tests
git commit -m "feat(storage): manifests mount resolved storages; P/D pools may override the list"
```

---

### Task 6: Recipe API takes `storages`

**Files:**
- Modify: `backend/app/api/serving_recipes.py`
- Create: `backend/app/services/storage_fields.py` (shared request-model helpers for recipes and deployments)
- Test: `backend/tests/test_storage_recipes_deployments.py` (recipe part); update `backend/tests/test_serving_recipes.py`

**Interfaces:**
- Consumes: Task 2 `validate_entries`, `model_path_warnings`, `all_entries`; Task 3 `check_known`, `UnknownStorageError`; `pd_serving.ROLES`.
- Produces:
  - `storage_fields.reject_legacy_pvc(data: Any) -> Any` (pydantic `mode="before"` helper, raises `ValueError` with the Global Constraints hint)
  - `storage_fields.recipe_warnings(model_path: str | None, storages: list[dict], pd_config: dict | None) -> list[str]`
  - Recipe responses: `"storages": [...]` instead of `pvc_*`; create/update responses add `"warnings": [...]`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_storage_recipes_deployments.py
"""Recipe and deployment APIs: storages in, legacy pvc fields out, resolution at deploy."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from tests._storage import SID

NOW = datetime(2026, 10, 19, tzinfo=UTC)
RECIPE = {"name": "llama", "model_path": "/models/llama", "image": "vllm/vllm-openai:v0.9"}


def _result(scalar=None):
    r = MagicMock()
    r.scalar_one_or_none.return_value = scalar
    return r


async def _refresh(row):
    row.created_at = row.updated_at = NOW


async def test_recipe_rejects_legacy_pvc_fields(client_for_user, super_user, mock_db):
    async with client_for_user(super_user) as client:
        resp = await client.post("/api/admin/serving-recipes", json={**RECIPE, "pvc_name": "models-pvc"})
    assert resp.status_code == 422
    assert "replaced by storages" in resp.text


async def test_recipe_create_with_storages_and_warning(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(None))
    mock_db.refresh = AsyncMock(side_effect=_refresh)
    body = {**RECIPE, "model_path": "/data/llama", "storages": [{"storage_id": SID, "mount_path": "/models/"}]}
    with patch("app.api.serving_recipes.check_known", AsyncMock()):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/admin/serving-recipes", json=body)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["storages"] == [{"storage_id": SID, "mount_path": "/models", "read_only": True}]
    assert data["warnings"] == ["model_path_outside_storages"]
    assert "pvc_name" not in data


async def test_recipe_unknown_storage_400(client_for_user, super_user, mock_db):
    from app.services.storage_store import UnknownStorageError

    mock_db.execute = AsyncMock(return_value=_result(None))
    body = {**RECIPE, "storages": [{"storage_id": SID, "mount_path": "/models"}]}
    with patch("app.api.serving_recipes.check_known", AsyncMock(side_effect=UnknownStorageError([SID]))):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/admin/serving-recipes", json=body)
    assert resp.status_code == 400 and SID in resp.text


async def test_recipe_rejects_overlapping_mounts(client_for_user, super_user, mock_db):
    body = {**RECIPE, "storages": [{"storage_id": SID, "mount_path": "/models"},
                                   {"storage_id": str(uuid.uuid4()), "mount_path": "/models/x"}]}
    async with client_for_user(super_user) as client:
        assert (await client.post("/api/admin/serving-recipes", json=body)).status_code == 422
```

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=. $PY -m pytest tests/test_storage_recipes_deployments.py -q`
Expected: FAIL (legacy field accepted; no `storages` in response)

- [ ] **Step 3: Implement the shared helpers**

```python
# backend/app/services/storage_fields.py
"""Request-model helpers shared by the recipe and deployment APIs."""

from __future__ import annotations

from typing import Any

from app.services.pd_serving import ROLES
from app.services.storage_mounts import model_path_warnings

LEGACY_PVC_HINT = "pvc_name/pvc_mount_path were replaced by storages: [{storage_id, mount_path, read_only}]"


def reject_legacy_pvc(data: Any) -> Any:
    """``mode="before"`` validator body: refuse the removed fields (also inside P/D pools)."""
    if not isinstance(data, dict):
        return data
    pools = [data] + [p for r in ROLES if isinstance(p := (data.get("pd_config") or {}).get(r), dict)]
    for block in pools:
        if block.get("pvc_name") is not None or block.get("pvc_mount_path") is not None:
            raise ValueError(LEGACY_PVC_HINT)
        block.pop("pvc_name", None)
        block.pop("pvc_mount_path", None)
    return data


def recipe_warnings(model_path: str | None, storages: list[dict], pd_config: dict | None) -> list[str]:
    """``model_path_outside_storages`` for the base row or any pool, once."""
    out = set(model_path_warnings(model_path, storages))
    for role in ROLES:
        pool = (pd_config or {}).get(role) or {}
        out |= set(model_path_warnings(pool.get("model_path") or model_path, pool.get("storages") or storages))
    return sorted(out)
```

- [ ] **Step 4: Change `serving_recipes.py`**

- Imports:

```python
from app.services.storage_fields import recipe_warnings, reject_legacy_pvc
from app.services.storage_mounts import all_entries, validate_entries
from app.services.storage_store import UnknownStorageError, check_known
```

(merge them into the module's sorted `app.services` import block so `ruff check` (isort) stays clean.)

- In `RecipeBody`: delete `pvc_name` / `pvc_mount_path`, add `storages: list[dict] = Field(default_factory=list)` and

```python
    @model_validator(mode="before")
    @classmethod
    def _no_legacy_pvc(cls, data):
        return reject_legacy_pvc(data)

    @field_validator("storages")
    @classmethod
    def _check_storages(cls, v: list[dict]) -> list[dict]:
        return validate_entries(v)
```

- `_serialize`: replace the two `pvc_*` lines with `"storages": list(r.storages or []),`.
- Add a helper and call it as `await _check_known_storages(db, body)` in `create_recipe` (before `db.add`) and `update_recipe` (before the setattr loop):

```python
async def _check_known_storages(db: AsyncSession, body: RecipeBody) -> None:
    """Every storage the recipe lists (base + P/D pools) must exist."""
    try:
        await check_known(db, all_entries(body))
    except UnknownStorageError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
```

- Return values: `create_recipe` → `{**_serialize(recipe), "linked_deployment": linked, "warnings": recipe_warnings(recipe.model_path, recipe.storages, recipe.pd_config)}`; `update_recipe` → `{**_serialize(recipe), "warnings": recipe_warnings(...)}`.

- [ ] **Step 5: Update `test_serving_recipes.py`**

Replace any `pvc_name`/`pvc_mount_path` in request bodies and expected responses with `storages` (empty list where they were `None`). Keep the rest unchanged.

- [ ] **Step 6: Run tests**

Run:
```bash
PYTHONPATH=. $PY -m pytest tests/test_storage_recipes_deployments.py tests/test_serving_recipes.py tests/test_pd_api.py -q
$PY -m ruff check app/services/storage_fields.py app/api/serving_recipes.py tests/test_storage_recipes_deployments.py tests/test_serving_recipes.py
```
Expected: PASS; `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/storage_fields.py backend/app/api/serving_recipes.py backend/tests
git commit -m "feat(storage): recipes reference storages; legacy pvc fields rejected"
```

---

### Task 7: Deployment API resolves and snapshots storages

**Files:**
- Modify: `backend/app/api/model_deployments.py` (request models, `_serialize`, `get_deployment`, `create_deployment`, `update_deployment`)
- Test: extend `backend/tests/test_storage_recipes_deployments.py`; update `backend/tests/test_model_deployments_engine.py`, `backend/tests/test_pd_api.py`

**Interfaces:**
- Consumes: Task 3 `resolve_entries`, `resolve_pool_storages`, `stale_storage_ids`, `pvc_phases` (5 s-bounded, `None` on failure); Task 2 `validate_entries`, `all_entries`; Task 6 `reject_legacy_pvc`, `recipe_warnings`. Every resolution error is a `ValueError` (`UnresolvedStorageError`, `UnknownStorageError`, reserved/duplicate volume name) → 400.
- Produces:
  - `CreateDeploymentRequest.storages: list[dict]` (default `[]`), `UpdateDeploymentRequest.storages: list[dict] | None`
  - Deployment JSON: `"storages"` (with `volume_name` + `resolved`); `GET /{id}` adds `"storage_stale": [storage_id…]`; create/update add `"warnings": [...]` with codes `model_path_outside_storages`, `pvc_pending:<claim>`, `pvc_missing:<claim>`.

- [ ] **Step 1: Write the failing tests** (append to `test_storage_recipes_deployments.py`)

```python
DEPLOY = {
    "model_name": "llama", "namespace": "prod", "model_path": "/models/llama", "ingress_host": "llama.local",
    "storages": [{"storage_id": SID, "mount_path": "/models"}],
}


def _k8s():
    k8s = MagicMock()
    k8s.create_or_patch = AsyncMock()
    return k8s


def _resolved(db, entries, *a, **kw):
    """Stand-in for storage_store.resolve_entries(db, entries, cluster_id, namespace, *, previous)."""
    snap = {"binding_id": "b", "type": "pvc", "claim_name": "models-pvc", "namespace": "prod",
            "nfs_server": None, "nfs_path": None, "sub_path": None}
    return [{**e, "volume_name": "st-llama-weights", "resolved": snap} for e in entries]


def _phases(phases):
    return patch("app.api.model_deployments.pvc_phases", AsyncMock(return_value=phases))


async def test_deploy_snapshots_resolution_and_warns_pending_pvc(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(None))
    mock_db.refresh = AsyncMock(side_effect=_refresh)
    k8s = _k8s()
    with (
        patch("app.api.model_deployments.k8s_for_cluster", AsyncMock(return_value=k8s)),
        patch("app.api.model_deployments.resolve_entries", AsyncMock(side_effect=_resolved)) as res,
        _phases({"models-pvc": "Pending"}) as phases,
    ):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/model-deployments", json=DEPLOY)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["storages"][0]["resolved"]["claim_name"] == "models-pvc"
    assert data["warnings"] == ["pvc_pending:models-pvc"]
    assert res.await_args.args[2:] == (None, "prod")
    assert phases.await_args.args[1:] == (None, "prod")
    pod = next(m for m in k8s.create_or_patch.await_args.args[1] if m["kind"] == "Deployment")
    assert pod["spec"]["template"]["spec"]["volumes"][0]["persistentVolumeClaim"]["claimName"] == "models-pvc"


async def test_deploy_unresolved_storage_400_and_nothing_applied(client_for_user, super_user, mock_db):
    from app.services.storage_store import UnresolvedStorageError

    mock_db.execute = AsyncMock(return_value=_result(None))
    k8s = _k8s()
    unresolved = AsyncMock(side_effect=UnresolvedStorageError(["llama-weights"]))
    with (
        patch("app.api.model_deployments.k8s_for_cluster", AsyncMock(return_value=k8s)),
        patch("app.api.model_deployments.resolve_entries", unresolved),
    ):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/model-deployments", json=DEPLOY)
    assert resp.status_code == 400 and "llama-weights" in resp.text
    k8s.create_or_patch.assert_not_awaited()
    mock_db.add.assert_not_called()


async def test_deploy_rejects_legacy_pvc(client_for_user, super_user, mock_db):
    async with client_for_user(super_user) as client:
        resp = await client.post("/api/model-deployments", json={**DEPLOY, "pvc_name": "x", "pvc_mount_path": "/m"})
    assert resp.status_code == 422


def _row(**kw):
    from app.db.models.custom_model_deployment import CustomModelDeployment

    base = dict(
        id=uuid.uuid4(), model_name="llama", namespace="prod", cluster_id=None, image="img", replicas=1,
        gpu_count=1, gpu_resource_key="nvidia.com/gpu", model_path="/models/llama", ingress_host="h",
        ingress_path="/", ingress_class="nginx", engine="vllm", serving_mode="aggregated", status="Running",
        ready_replicas=1, created_at=NOW, updated_at=NOW,
        storages=[{"storage_id": SID, "mount_path": "/models", "read_only": False, "volume_name": "model-weights",
                   "resolved": {"binding_id": "old"}}],
    )
    base.update(kw)
    return CustomModelDeployment(**base)


async def test_update_re_resolves_and_keeps_volume_name(client_for_user, super_user, mock_db):
    row = _row()
    mock_db.execute = AsyncMock(return_value=_result(row))
    mock_db.refresh = AsyncMock(side_effect=_refresh)
    with (
        patch("app.api.model_deployments.k8s_for_cluster", AsyncMock(return_value=_k8s())),
        patch("app.api.model_deployments.resolve_entries", AsyncMock(side_effect=_resolved)) as res,
        _phases({"models-pvc": "Bound"}),
    ):
        async with client_for_user(super_user) as client:
            resp = await client.put(f"/api/model-deployments/{row.id}", json={"replicas": 2})
    assert resp.status_code == 200, resp.text
    entries, cluster, ns = res.await_args.args[1:4]
    assert entries[0]["storage_id"] == SID and (cluster, ns) == (None, "prod")
    assert res.await_args.kwargs["previous"][0]["volume_name"] == "model-weights"
    assert resp.json()["warnings"] == []


async def test_get_deployment_reports_stale(client_for_user, super_user, mock_db):
    row = _row()
    mock_db.execute = AsyncMock(return_value=_result(row))
    with patch("app.api.model_deployments.stale_storage_ids", AsyncMock(return_value=[SID])):
        async with client_for_user(super_user) as client:
            resp = await client.get(f"/api/model-deployments/{row.id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["storage_stale"] == [SID]
```

`_resolved` has `resolve_entries`' positional shape `(db, entries, cluster_id, namespace)` and writes `volume_name: "st-llama-weights"` regardless of `previous`; the update test asserts what was **passed** (`previous`), which is the contract. `get_deployment` may run more queries than one (recipe names, events, router); if the test fails on a second `execute`, make `mock_db.execute` return `_result(row)` for every call (it already does via `return_value`) and patch any other helper `get_deployment` awaits.

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=. $PY -m pytest tests/test_storage_recipes_deployments.py -q`
Expected: the five new tests FAIL.

- [ ] **Step 3: Implement in `model_deployments.py`**

Imports:

```python
from app.services.storage_fields import recipe_warnings, reject_legacy_pvc
from app.services.storage_mounts import all_entries, validate_entries
from app.services.storage_store import pvc_phases, resolve_entries, resolve_pool_storages, stale_storage_ids
```

(merged into the module's sorted `app.services` imports.)

`CreateDeploymentRequest`: remove `pvc_name`/`pvc_mount_path`; add `storages: list[dict] = Field(default_factory=list)`; add

```python
    @model_validator(mode="before")
    @classmethod
    def _no_legacy_pvc(cls, data):
        return reject_legacy_pvc(data)

    @field_validator("storages")
    @classmethod
    def _check_storages(cls, v: list[dict]) -> list[dict]:
        return validate_entries(v)
```

`UpdateDeploymentRequest`: same, with `storages: list[dict] | None = None` and the validator returning `None if v is None else validate_entries(v)`.

`_serialize`: replace the `pvc_*` lines with `"storages": list(d.storages or []),`.

Add helpers near `_apply_pool_profiles`:

```python
async def _resolve_storages(
    db: AsyncSession, dep: CustomModelDeployment, entries: list, *, previous: list | None, previous_pd: dict | None
) -> None:
    """Snapshot the binding of every storage (base + P/D pools) for the row's cluster/namespace."""
    try:
        dep.storages = await resolve_entries(db, entries, dep.cluster_id, dep.namespace, previous=previous)
        if pd_serving.is_pd(dep):
            dep.pd_config = await resolve_pool_storages(
                db, dep.pd_config, dep.cluster_id, dep.namespace, previous=previous_pd
            )
    except ValueError as e:  # unresolved / unknown storage, reserved or duplicate volume name
        raise HTTPException(status_code=400, detail=str(e))


async def _deploy_warnings(db: AsyncSession, dep: CustomModelDeployment) -> list[str]:
    """model_path outside every storage; PVCs that are not Bound (bounded, best-effort lookup)."""
    out = recipe_warnings(dep.model_path, dep.storages or [], dep.pd_config)
    resolved = [e.get("resolved") or {} for e in all_entries(dep)]
    claims = sorted({r["claim_name"] for r in resolved if r.get("type") == "pvc"})
    if not claims:
        return out
    phases = await pvc_phases(db, dep.cluster_id, dep.namespace)
    if phases is None:  # cluster unreadable: no PVC warnings
        return out
    for claim in claims:
        if claim not in phases:
            out.append(f"pvc_missing:{claim}")
        elif phases[claim] != "Bound":
            out.append(f"pvc_pending:{claim}")
    return out
```

`create_deployment`: in the `CustomModelDeployment(...)` constructor drop `pvc_name=…`/`pvc_mount_path=…`. Right after constructing `dep` (before `_apply_pool_profiles`) add:

```python
    await _resolve_storages(db, dep, body.storages, previous=None, previous_pd=None)
```

and change the final `return _serialize(dep)` to `return {**_serialize(dep), "warnings": await _deploy_warnings(db, dep)}`.

`update_deployment`: before the `for field, value in updates.items()` loop capture

```python
    previous = list(dep.storages or [])
    previous_pd = dict(dep.pd_config or {}) if dep.pd_config else None
```

After the existing `pd_config` validation block (`validate_pd_config` strips resolution keys from pool lists) add:

```python
    entries = updates["storages"] if "storages" in updates else previous
    await _resolve_storages(db, dep, entries, previous=previous, previous_pd=previous_pd)
```

and return `{**_serialize(dep), "warnings": await _deploy_warnings(db, dep)}`.

`get_deployment`: add `"storage_stale": await stale_storage_ids(db, dep)` to the returned dict (open the function and add it next to the other extras it already returns).

- [ ] **Step 4: Update the deployment tests that used PVC fields**

In `test_model_deployments_engine.py` and `test_pd_api.py`, replace request `pvc_*` fields with `storages`, and patch `app.api.model_deployments.resolve_entries` with `AsyncMock(side_effect=lambda db, e, *a, **k: [])` where the tests don't care about storage. Existing assertions on responses drop `pvc_*` keys.

- [ ] **Step 5: Run tests**

Run:
```bash
PYTHONPATH=. $PY -m pytest tests/test_storage_recipes_deployments.py tests/test_model_deployments_engine.py tests/test_pd_api.py tests/test_gpu_profiles_api.py -q
$PY -m ruff check app/api/model_deployments.py tests/test_storage_recipes_deployments.py tests/test_model_deployments_engine.py tests/test_pd_api.py
```
Expected: PASS; `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/model_deployments.py backend/tests
git commit -m "feat(storage): deployments resolve storages per cluster/namespace, snapshot, stale flag"
```

---

### Task 8: Cluster bench storage replaces the default NFS fields (API)

**Files:**
- Modify: `backend/app/api/k8s_clusters.py`
- Test: update `backend/tests/test_k8s_clusters.py`

**Interfaces:**
- Consumes: Task 1 `bench_storage_id`/`bench_mount_path`; Task 3 `load_storages`.
- Produces: cluster bodies/JSON carry `bench_storage_id: str | null`, `bench_mount_path: str | null` instead of `default_nfs_*`. Both set or both empty (400); malformed or unknown storage id (400, never 500); mount path absolute (400); legacy `default_nfs_*` in a body → 422. Update semantics as before: field omitted / `null` = unchanged, `""` = clear.

- [ ] **Step 1: Write the failing tests** (append to `test_k8s_clusters.py`)

Add to the file's imports (keep them sorted; `patch` joins the existing `unittest.mock` import, `SID` comes after the `app.*` imports):

```python
from unittest.mock import AsyncMock, MagicMock, patch

from tests._storage import SID
```

Then append:

```python
# ─── bench storage (replaces default_nfs_*) ───────────────────────────────────

BASE = {"name": "c1", "context": "prod", "kubeconfig": SAMPLE_KUBECONFIG}


def _result(scalar=None):
    r = MagicMock()
    r.scalar_one_or_none.return_value = scalar
    return r


async def test_cluster_bench_storage_both_or_neither(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(None))  # no name clash
    async with client_for_user(super_user) as client:
        resp = await client.post("/api/admin/k8s-clusters", json={**BASE, "bench_storage_id": SID})
    assert resp.status_code == 400 and "together" in resp.text


async def test_cluster_bench_storage_must_exist(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(None))
    body = {**BASE, "bench_storage_id": SID, "bench_mount_path": "/models"}
    with patch("app.api.k8s_clusters.load_storages", AsyncMock(return_value={})):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/admin/k8s-clusters", json=body)
    assert resp.status_code == 400 and "unknown storage" in resp.text


async def test_cluster_bench_storage_malformed_id_400(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(None))
    async with client_for_user(super_user) as client:
        resp = await client.post(
            "/api/admin/k8s-clusters", json={**BASE, "bench_storage_id": "nope", "bench_mount_path": "/models"}
        )
    assert resp.status_code == 400 and "unknown storage" in resp.text


async def test_cluster_create_with_bench_storage(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(None))
    body = {**BASE, "bench_storage_id": SID, "bench_mount_path": "/models"}
    with patch("app.api.k8s_clusters.load_storages", AsyncMock(return_value={SID: object()})):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/admin/k8s-clusters", json=body)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["bench_storage_id"] == SID and data["bench_mount_path"] == "/models"
    assert "default_nfs_server" not in data


async def test_cluster_update_malformed_bench_storage_id_400(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(MagicMock(bench_storage_id=None, bench_mount_path=None)))
    async with client_for_user(super_user) as client:
        resp = await client.put(
            f"/api/admin/k8s-clusters/{uuid.uuid4()}", json={"bench_storage_id": "nope", "bench_mount_path": "/m"}
        )
    assert resp.status_code == 400 and "unknown storage" in resp.text


async def test_cluster_rejects_legacy_nfs_fields(client_for_user, super_user, mock_db):
    async with client_for_user(super_user) as client:
        resp = await client.post("/api/admin/k8s-clusters", json={**BASE, "default_nfs_server": "s"})
    assert resp.status_code == 422
```

Also update the three serializer tests that build `SimpleNamespace` cluster rows (`test_cluster_serialize_includes_argocd_namespace`, `test_serialize_includes_argocd_placement_fields`, `test_serialize_argocd_placement_nulls`): replace `default_nfs_server=None, default_nfs_path=None, default_nfs_mount_path=None` with `bench_storage_id=None, bench_mount_path=None` (the serializer now reads `c.bench_storage_id`). Leave `test_nfs_fields_incomplete` (the helper stays for `benchmarks.py`) and the `resolve_bench_nfs` / `build_vllm_bench_job` tests (Task 9 rewrites them).

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=. $PY -m pytest tests/test_k8s_clusters.py -q` → the new tests FAIL (unknown fields ignored → 201 / no 400).

- [ ] **Step 3: Implement**

- Both request models: remove `default_nfs_*`, add `bench_storage_id: str | None = None`, `bench_mount_path: str | None = None`, and (no `extra="forbid"` elsewhere in this module, so reject the old names explicitly) add to both:

```python
    @model_validator(mode="before")
    @classmethod
    def _no_legacy_nfs(cls, data):
        if isinstance(data, dict) and any(data.get(k) is not None for k in _LEGACY_NFS):
            raise ValueError("default_nfs_* were replaced by bench_storage_id + bench_mount_path")
        return data
```

with `_LEGACY_NFS = ("default_nfs_server", "default_nfs_path", "default_nfs_mount_path")` at module level (above the request models).

- Add (below `_validate_argocd_host`):

```python
def _bench_storage_uuid(raw: str | None) -> uuid.UUID | None:
    """Form value → storage id; '' / None = none. Malformed ids are a 400, not a 500."""
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"unknown storage: {raw}")


async def _check_bench_storage(db: AsyncSession, storage_id: uuid.UUID | None, mount_path: str | None) -> None:
    if bool(storage_id) != bool(mount_path):
        raise HTTPException(status_code=400, detail="bench_storage_id and bench_mount_path must be set together")
    if storage_id is None:
        return
    if not mount_path.startswith("/"):
        raise HTTPException(status_code=400, detail="bench_mount_path must be an absolute path")
    if not await load_storages(db, [str(storage_id)]):
        raise HTTPException(status_code=400, detail=f"unknown storage: {storage_id}")
```

- Create: replace the `nfs_fields_incomplete` block (right after the duplicate-name check) with

```python
    bench_storage_id = _bench_storage_uuid(body.bench_storage_id)
    bench_mount_path = body.bench_mount_path or None
    await _check_bench_storage(db, bench_storage_id, bench_mount_path)
```

  and in the `CustomK8sCluster(...)` constructor replace the three `default_nfs_*=` lines with `bench_storage_id=bench_storage_id, bench_mount_path=bench_mount_path,`.
- Update: delete the three `default_nfs_*` `if … is not None` assignments **and** the post-assignment `nfs_fields_incomplete` check; in their place (before the kubeconfig re-validation) compute and validate the new values first, then assign:

```python
    bench_storage_id = (
        cluster.bench_storage_id if body.bench_storage_id is None else _bench_storage_uuid(body.bench_storage_id)
    )
    bench_mount_path = cluster.bench_mount_path if body.bench_mount_path is None else (body.bench_mount_path or None)
    await _check_bench_storage(db, bench_storage_id, bench_mount_path)
    cluster.bench_storage_id = bench_storage_id
    cluster.bench_mount_path = bench_mount_path
```

- Serializer: replace the `default_nfs_*` keys with
  `"bench_storage_id": str(c.bench_storage_id) if c.bench_storage_id else None, "bench_mount_path": c.bench_mount_path,`.
- Imports: drop `nfs_fields_incomplete`; add `from app.services.storage_store import load_storages` and `model_validator` to the `pydantic` import.

- [ ] **Step 4: Run tests**

Run:
```bash
PYTHONPATH=. $PY -m pytest tests/test_k8s_clusters.py tests/test_cluster_placement.py -q
$PY -m ruff check app/api/k8s_clusters.py tests/test_k8s_clusters.py
```
Expected: PASS; `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/k8s_clusters.py backend/tests/test_k8s_clusters.py
git commit -m "feat(storage): clusters pick a benchmark default storage instead of raw NFS fields"
```

---

### Task 9: Benchmarks mount storages

**Files:**
- Modify: `backend/app/services/benchmark_manifests.py` (`build_vllm_bench_job` signature; delete `resolve_bench_nfs`)
- Modify: `backend/app/api/benchmarks.py` (`_serving_snapshot`, `_build_bench_job`, delete `_cluster_nfs_defaults`, every call site, ephemeral create + preview)
- Modify: `backend/app/services/benchmark_serving.py` (`build_ephemeral_deployment`)
- Modify: `backend/app/jobs/reconcile_benchmarks.py:116-130`
- Test: `backend/tests/test_storage_benchmarks.py`; update `test_k8s_clusters.py` (holds the `resolve_bench_nfs` / builder tests), `test_benchmark_manifests.py`, `test_benchmarks_ephemeral.py`, `test_benchmark_endpoint.py`, `test_reconcile_benchmarks.py`, `test_self_serving_bench.py`

**Interfaces:**
- Consumes: Task 2 `to_volumes`, `legacy_pvc_volumes`, `nfs_override_volumes`; Task 3 `bench_volumes`, `resolve_entries` (every error is a `ValueError`).
- Produces:
  - `benchmarks._ephemeral_storages(db, base, cluster_id, namespace) -> list[dict]` — the template's own snapshot (copied) when the run targets the template's cluster **and** namespace; otherwise `resolve_entries` for the run's target (PVC bindings are namespace-scoped, so a copied claim would not exist there). Raises `ValueError` when a storage has no binding there.
  - `build_vllm_bench_job(run, *, image, target_base_url, api_key, served_model, tokenizer=None, volumes=None, volume_mounts=None, backoff_limit=0, ttl_seconds_after_finished=…)`
  - `_build_bench_job(run, *, deployment, target_base, api_key, image_override, bench_vols: tuple[list, list] = ([], []))`
  - Serving snapshots carry `"storages"` (resolved list) instead of `pvc_name`/`pvc_mount_path` (external-clone snapshots unchanged).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_storage_benchmarks.py
"""Benchmark jobs: deployment storages read-only, bench default storage, legacy snapshots."""

import types
import uuid
from unittest.mock import AsyncMock, patch

from app.db.models.custom_model_deployment import CustomModelDeployment
from app.jobs import reconcile_benchmarks
from app.services.benchmark_manifests import build_vllm_bench_job
from app.services.benchmark_serving import build_ephemeral_deployment
from tests._storage import CLUSTER, legacy_entry


def _run(**kw):
    base = dict(id=uuid.uuid4(), tool="vllm_serving", kind="performance", params={}, k8s_namespace="bench")
    base.update(kw)
    return types.SimpleNamespace(**base)


def _job_volumes(manifest):
    pod = manifest["spec"]["template"]["spec"]
    return pod["volumes"], pod["containers"][0]["volumeMounts"]


def test_bench_job_takes_prebuilt_volumes():
    vols = [{"name": "model-weights", "nfs": {"server": "s", "path": "/p", "readOnly": True}}]
    mounts = [{"name": "model-weights", "mountPath": "/models", "readOnly": True}]
    m = build_vllm_bench_job(_run(), image="i", target_base_url="http://t", api_key="k", served_model="m",
                             volumes=vols, volume_mounts=mounts)
    assert _job_volumes(m) == (vols, mounts)
    m = build_vllm_bench_job(_run(), image="i", target_base_url="http://t", api_key="k", served_model="m")
    assert _job_volumes(m) == ([], [])


def test_deployment_target_mounts_its_storages_read_only():
    from app.api.benchmarks import _build_bench_job

    storages = [legacy_entry("models-pvc", "/models")]
    dep = types.SimpleNamespace(model_path="/models/llama", image="vllm:1", storages=storages)
    m = _build_bench_job(_run(), deployment=dep, target_base="http://t", api_key="k", image_override=None)
    vols, mounts = _job_volumes(m)
    assert vols == [{"name": "model-weights", "persistentVolumeClaim": {"claimName": "models-pvc", "readOnly": True}}]
    assert mounts == [{"name": "model-weights", "mountPath": "/models", "readOnly": True}]


def test_raw_target_uses_bench_vols():
    from app.api.benchmarks import _build_bench_job

    bench = ([{"name": "model-weights", "nfs": {"server": "s", "path": "/p", "readOnly": True}}],
             [{"name": "model-weights", "mountPath": "/m", "readOnly": True}])
    m = _build_bench_job(_run(model_name="llama"), deployment=None, target_base="http://t", api_key="k",
                         image_override=None, bench_vols=bench)
    assert _job_volumes(m) == bench


def test_ephemeral_copies_storages():
    base = CustomModelDeployment(
        model_name="llama", namespace="prod", image="i", replicas=1, gpu_count=1, gpu_resource_key="nvidia.com/gpu",
        model_path="/models/llama", ingress_host="h", ingress_path="/", ingress_class="nginx", engine="vllm",
        storages=[legacy_entry("models-pvc", "/models")],
    )
    eph = build_ephemeral_deployment(base, name="bench-x", namespace="bench", overrides=None)
    assert eph.storages == base.storages and eph.storages is not base.storages


async def test_ephemeral_storages_keep_snapshot_on_the_template_target():
    from app.api.benchmarks import _ephemeral_storages

    base = types.SimpleNamespace(
        cluster_id=CLUSTER, namespace="prod", storages=[legacy_entry("models-pvc", "/models", namespace="prod")]
    )
    with patch("app.api.benchmarks.resolve_entries", AsyncMock()) as res:
        out = await _ephemeral_storages(None, base, CLUSTER, "prod")
    assert out == base.storages and out[0] is not base.storages[0]
    res.assert_not_awaited()


async def test_ephemeral_storages_re_resolve_for_another_namespace():
    from app.api.benchmarks import _ephemeral_storages

    base = types.SimpleNamespace(cluster_id=CLUSTER, namespace="prod", storages=[legacy_entry("models-pvc", "/models")])
    with patch("app.api.benchmarks.resolve_entries", AsyncMock(return_value=["resolved"])) as res:
        assert await _ephemeral_storages(None, base, CLUSTER, "bench") == ["resolved"]
    assert res.await_args.args[1:] == (base.storages, CLUSTER, "bench")
    assert res.await_args.kwargs["previous"] is base.storages


async def test_reconcile_legacy_snapshot_still_mounts_pvc():
    snap = {
        "model_path": "/models/llama", "pvc_name": "models-pvc", "pvc_mount_path": "/models",
        "vllm_extra_args": [], "env": {},
    }
    vols, mounts = reconcile_benchmarks._snapshot_volumes(snap)
    assert vols[0]["persistentVolumeClaim"] == {"claimName": "models-pvc", "readOnly": True}
    snap2 = {"storages": [legacy_entry("new-pvc", "/models")]}
    assert reconcile_benchmarks._snapshot_volumes(snap2)[0][0]["persistentVolumeClaim"]["claimName"] == "new-pvc"
    assert reconcile_benchmarks._snapshot_volumes({}) == ([], [])
```

Add API-level tests (same file) for the two callers that need the DB:

```python
async def test_endpoint_benchmark_uses_bench_volumes(client_for_user, super_user, mock_db):
    bench = ([{"name": "model-weights", "nfs": {"server": "s", "path": "/p", "readOnly": True}}],
             [{"name": "model-weights", "mountPath": "/m", "readOnly": True}])
    k8s = types.SimpleNamespace(create_job=AsyncMock())
    with (
        patch("app.api.benchmarks.k8s_for_cluster", AsyncMock(return_value=k8s)),
        patch("app.api.benchmarks.bench_volumes", AsyncMock(return_value=bench)) as bv,
    ):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/benchmarks", json={
                "tool": "vllm_serving", "base_url": "http://x:8000", "model_name": "llama", "namespace": "bench",
            })
    assert resp.status_code in (200, 201), resp.text
    assert bv.await_args.args[1:] == (None, "bench", {})
    manifest = k8s.create_job.await_args.args[1]
    assert _job_volumes(manifest) == bench
```

Check the benchmarks router prefix and the create route path with `grep -n "router = APIRouter\|@router.post" backend/app/api/benchmarks.py` and adjust `/api/benchmarks` if it differs; mirror `test_benchmark_endpoint.py` for the request body and DB mocks.

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=. $PY -m pytest tests/test_storage_benchmarks.py -q`
Expected: FAIL (unexpected `volumes` kwarg; `_snapshot_volumes` missing)

- [ ] **Step 3: `benchmark_manifests.py`**

- Delete `resolve_bench_nfs` (keep `nfs_fields_incomplete` and the `nfs_*` entries in `_NON_CLI_PARAMS`).
- In `build_vllm_bench_job` replace the six `pvc_*`/`nfs_*` keyword parameters with `volumes: list | None = None, volume_mounts: list | None = None`, and replace the "Model-weights volume" block with:

```python
    # Model-weights mount, prebuilt by the caller: a deployment target's own
    # storages (read-only) or, for a raw model_name, the cluster's bench storage.
    volumes = list(volumes or [])
    volume_mounts = list(volume_mounts or [])
```

  Update the docstring's last sentence to "…or the mounted weights when the caller passes volumes."

- [ ] **Step 4: `benchmark_serving.py`**

In `build_ephemeral_deployment` replace `pvc_name=base.pvc_name, pvc_mount_path=base.pvc_mount_path,` with `storages=[dict(e) for e in (base.storages or [])],`. (The API keeps them when the run targets the template's cluster + namespace and re-resolves them otherwise — `_ephemeral_storages`, Step 5.)

- [ ] **Step 5: `benchmarks.py`**

- Imports: drop `resolve_bench_nfs` (and `CustomK8sCluster` if `_cluster_nfs_defaults` was its only user — `grep -n CustomK8sCluster backend/app/api/benchmarks.py`); add, in sorted position,
  `from app.services.storage_mounts import to_volumes` and
  `from app.services.storage_store import bench_volumes, resolve_entries`.
- `_serving_snapshot`: replace the two `pvc_*` keys with `"storages": [dict(e) for e in (dep.storages or [])],`.
- `_build_bench_job`: replace the three `cluster_default_nfs_*` params with `bench_vols: tuple[list, list] = ([], [])`; replace the mount-selection block with

```python
    params = run.params or {}
    if deployment is not None:
        served_model = deployment.model_path
        perf_default_image = deployment.image
        vols, mounts = to_volumes(deployment.storages, force_read_only=True)
    else:
        served_model = run.model_name
        perf_default_image = settings.vllm_bench_image
        vols, mounts = bench_vols
```

  and pass `volumes=vols, volume_mounts=mounts` to `build_vllm_bench_job` (remove the `pvc_*`/`nfs_*` kwargs).
- Delete `_cluster_nfs_defaults`.
- Every former `_cluster_nfs_defaults(...)` + `resolve_bench_nfs(...)` pair (endpoint-URL create, deployment/raw create, endpoint-URL preview, deployment/raw preview — `grep -n "_cluster_nfs_defaults\|resolve_bench_nfs\|cluster_default_nfs" backend/app/api/benchmarks.py`) becomes one call with the run's cluster and **job namespace**:

```python
        bench_vols = await bench_volumes(db, cluster_uuid, namespace, body.params)
```

  In the preview handler the namespace variable is the one used for `run.k8s_namespace`; the cluster is `uuid.UUID(body.cluster_id) if body.cluster_id else None`. Pass `volumes=bench_vols[0], volume_mounts=bench_vols[1]` to the direct `build_vllm_bench_job` calls and `bench_vols=bench_vols` to `_build_bench_job`.
- Add next to `_serving_snapshot`:

```python
async def _ephemeral_storages(
    db: AsyncSession, base: CustomModelDeployment, cluster_id: uuid.UUID | None, namespace: str
) -> list[dict]:
    """Storages for an ephemeral clone of ``base``.

    Same cluster and namespace as the template → copy its snapshot as-is (what it
    runs with today). Anywhere else → resolve for that target: PVC bindings are
    namespace-scoped, so a copied claim would not exist there and the pod would
    hang Pending. Raises ValueError when a storage has no binding there.
    """
    if (cluster_id, namespace) == (base.cluster_id, base.namespace):
        return [dict(e) for e in (base.storages or [])]
    return await resolve_entries(db, base.storages, cluster_id, namespace, previous=base.storages)
```

- Ephemeral create (right after `eph = build_ephemeral_deployment(...)` and its `except`, before `_serving_snapshot(eph)`):

```python
        try:
            eph.storages = await _ephemeral_storages(db, base, cluster_uuid, namespace)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
```

- Ephemeral preview (after its `build_ephemeral_deployment`):

```python
        try:
            eph.storages = await _ephemeral_storages(
                db, base, uuid.UUID(body.cluster_id) if body.cluster_id else None, namespace
            )
        except ValueError:
            return {"manifests": [], "note": "storage_unresolved"}
```

- [ ] **Step 6: `reconcile_benchmarks.py`**

Add near the top-level helpers:

```python
def _snapshot_volumes(snap: dict) -> tuple[list, list]:
    """Read-only weights for the bench Job: the snapshot's resolved storages, or
    a pre-storage snapshot's ``pvc_name``/``pvc_mount_path``."""
    if snap.get("storages") is not None:
        return to_volumes(snap["storages"], force_read_only=True)
    return legacy_pvc_volumes(snap.get("pvc_name"), snap.get("pvc_mount_path"))
```

(import `to_volumes`, `legacy_pvc_volumes` from `app.services.storage_mounts`), and in the performance branch replace `pvc_name=…, pvc_mount_path=…` with

```python
            vols, mounts = _snapshot_volumes(snap)
            manifest = build_vllm_bench_job(
                ...,
                volumes=vols,
                volume_mounts=mounts,
            )
```

- [ ] **Step 7: Update existing benchmark tests**

`grep -n "pvc_\|nfs_\|resolve_bench_nfs\|_cluster_nfs_defaults" backend/tests/*.py`:
- `tests/test_k8s_clusters.py` (lines ~14-21 and ~103-160 — the `resolve_bench_nfs` and old `build_vllm_bench_job` tests live here, not in `test_benchmark_manifests.py`):
  - change the `app.services.benchmark_manifests` import to `build_vllm_bench_job, nfs_fields_incomplete` (drop `resolve_bench_nfs`) and add `from app.services.storage_mounts import legacy_pvc_volumes, nfs_override_volumes`;
  - delete `test_resolve_nfs_run_override_beats_cluster_default`, `test_resolve_nfs_falls_back_to_cluster_default`, `test_resolve_nfs_none_when_nothing_set` (covered by `test_storage_mounts.py::test_legacy_and_nfs_override_helpers` and `test_storage_store.py::test_bench_volumes_*`);
  - replace the two mounting builder tests with (keep `test_vllm_bench_job_no_volume_when_unset` as is):

```python
def test_vllm_bench_job_mounts_nfs_when_set():
    vols, mounts = nfs_override_volumes(
        {"nfs_server": "nfs.local", "nfs_path": "/export/models", "nfs_mount_path": "/models"}
    )
    job = build_vllm_bench_job(
        _perf_run(), image="vllm:latest", target_base_url="http://t", api_key="k",
        served_model="m", tokenizer="/models/m", volumes=vols, volume_mounts=mounts,
    )
    spec = job["spec"]["template"]["spec"]
    vol = next(v for v in spec["volumes"] if v["name"] == "model-weights")
    assert vol["nfs"] == {"server": "nfs.local", "path": "/export/models", "readOnly": True}
    mount = spec["containers"][0]["volumeMounts"][0]
    assert mount["mountPath"] == "/models" and mount["readOnly"] is True


def test_vllm_bench_job_mounts_pvc_for_deployment_target():
    vols, mounts = legacy_pvc_volumes("weights", "/models")
    job = build_vllm_bench_job(
        _perf_run(), image="vllm:latest", target_base_url="http://t", api_key="k",
        served_model="m", volumes=vols, volume_mounts=mounts,
    )
    vol = job["spec"]["template"]["spec"]["volumes"][0]
    assert vol["persistentVolumeClaim"]["claimName"] == "weights"
```

- Tests that patched `_cluster_nfs_defaults` patch `app.api.benchmarks.bench_volumes` with `AsyncMock(return_value=([], []))` (or the NFS tuple they expect).
- Ephemeral tests (`test_benchmarks_ephemeral.py`): where a test's run namespace/cluster differs from the template's, patch `app.api.benchmarks.resolve_entries` with `AsyncMock(side_effect=lambda db, e, *a, **k: list(e or []))`; same-target runs need no patch (the snapshot is copied).
- Snapshot assertions: `pvc_name`/`pvc_mount_path` → `storages`.

- [ ] **Step 8: Run tests**

Run:
```bash
PYTHONPATH=. $PY -m pytest tests/test_storage_benchmarks.py tests/test_k8s_clusters.py tests/test_benchmark_manifests.py tests/test_benchmarks_ephemeral.py tests/test_benchmark_endpoint.py tests/test_reconcile_benchmarks.py tests/test_self_serving_bench.py tests/test_bench_external_clone.py tests/test_gpu_profiles.py -q
$PY -m ruff check app/services/benchmark_manifests.py app/services/benchmark_serving.py app/api/benchmarks.py app/jobs/reconcile_benchmarks.py tests/test_storage_benchmarks.py tests/test_k8s_clusters.py $(git diff --name-only -- tests | sed 's|^backend/||')
```
Expected: PASS; `All checks passed!` (`grep -rn "resolve_bench_nfs" app tests` → nothing).

- [ ] **Step 9: Commit**

```bash
git add backend/app/services/benchmark_manifests.py backend/app/services/benchmark_serving.py backend/app/api/benchmarks.py backend/app/jobs/reconcile_benchmarks.py backend/tests
git commit -m "feat(storage): benchmarks mount deployment storages and the cluster bench storage"
```

---

### Task 10: Recipe import attaches registered storages

**Files:**
- Modify: `backend/app/services/recipe_import.py` (`_volumes`; add `attach_storages`)
- Modify: `backend/app/api/model_deployments.py` (`external_recipe_draft`)
- Modify: `frontend/messages/ko.json`, `frontend/messages/en.json` (`servingRecipes.warn`: remove `multi_pvc`, add `unregistered_pvc`)
- Test: `backend/tests/test_storage_import.py`; update `backend/tests/test_recipe_import.py`, `backend/tests/test_recipe_draft_api.py`

**Interfaces:**
- Consumes: Task 3 `match_claims`.
- Produces:
  - `build_recipe_draft(spec)` drafts carry an internal `"pvc_mounts": [{"claim_name", "mount_path", "read_only"}]` instead of `pvc_name`/`pvc_mount_path`.
  - `attach_storages(draft: dict, warnings: list[dict], claim_to_storage: dict[str, str]) -> None` — pops `pvc_mounts`, sets `draft["storages"]`, warns `unregistered_pvc` per unmatched claim.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_storage_import.py
"""Recipe import: PVC volumes map to registered storages."""

from app.services.recipe_import import attach_storages, build_recipe_draft
from tests._storage import SID


def _spec(volumes, mounts):
    return {
        "container": {"image": "vllm/vllm-openai:v0.9", "command": ["vllm", "serve", "/models/llama"], "args": [],
                      "env": [], "resources": {}, "volume_mounts": mounts},
        "volumes": volumes, "replicas": 1, "node_selector": None, "tolerations": None,
    }


def test_draft_collects_every_pvc_mount():
    spec = _spec(
        [{"name": "w", "persistentVolumeClaim": {"claimName": "models-pvc"}},
         {"name": "c", "persistentVolumeClaim": {"claimName": "cache-pvc"}}],
        [{"name": "w", "mountPath": "/models", "readOnly": True}, {"name": "c", "mountPath": "/cache"}],
    )
    out = build_recipe_draft(spec)
    assert out["draft"]["pvc_mounts"] == [
        {"claim_name": "models-pvc", "mount_path": "/models", "read_only": True},
        {"claim_name": "cache-pvc", "mount_path": "/cache", "read_only": False},
    ]
    assert "pvc_name" not in out["draft"]
    assert all(w["code"] != "multi_pvc" for w in out["warnings"])


def test_attach_storages_matches_and_warns():
    draft = {"pvc_mounts": [{"claim_name": "models-pvc", "mount_path": "/models", "read_only": True},
                            {"claim_name": "cache-pvc", "mount_path": "/cache", "read_only": False}]}
    warnings: list[dict] = []
    attach_storages(draft, warnings, {"models-pvc": SID})
    assert draft == {"storages": [{"storage_id": SID, "mount_path": "/models", "read_only": True}]}
    assert [(w["code"], w.get("detail")) for w in warnings] == [("unregistered_pvc", "cache-pvc")]
```

Check the exact draft spec shape against `test_recipe_import.py` (how it builds `spec`) and the `_warn` dict keys (`code` / `detail`), and adapt `_spec` if the importer expects other keys.

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=. $PY -m pytest tests/test_storage_import.py -q` → FAIL (`ImportError: attach_storages`).

- [ ] **Step 3: Implement in `recipe_import.py`**

Replace the PVC part of `_volumes`:

```python
def _volumes(spec: dict, container: dict, out: dict, warnings: list[dict]) -> None:
    mounts = {m.get("name"): m for m in (container.get("volume_mounts") or container.get("volumeMounts") or [])}
    pvc_mounts: list[dict] = []
    unsupported: list[str] = []
    for vol in spec.get("volumes") or []:
        name = vol.get("name")
        pvc = vol.get("persistentVolumeClaim")
        if pvc and pvc.get("claimName"):
            mount = mounts.get(name) or {}
            if mount.get("subPath") or mount.get("subPathExpr"):
                _warn(warnings, "subpath_dropped", f"{name}: {mount.get('subPath') or mount.get('subPathExpr')}")
            if not mount.get("mountPath"):
                _warn(warnings, "pvc_unmounted", pvc["claimName"])
                continue
            pvc_mounts.append(
                {
                    "claim_name": pvc["claimName"],
                    "mount_path": mount["mountPath"],
                    "read_only": bool(mount.get("readOnly")),
                }
            )
            continue
        kinds = [k for k in vol if k != "name"]
        kind = kinds[0] if kinds else "unknown"
        if kind == "emptyDir" and (vol.get("emptyDir") or {}).get("medium") == "Memory":
            _warn(warnings, "shm_volume_dropped", name)
        else:
            unsupported.append(f"{name} ({kind})")
    out["pvc_mounts"] = pvc_mounts
    if unsupported:
        _warn(warnings, "volume_unsupported", ", ".join(unsupported))


def attach_storages(draft: dict, warnings: list[dict], claim_to_storage: dict[str, str]) -> None:
    """Turn the draft's PVC mounts into storage entries for claims registered as bindings."""
    storages = []
    for m in draft.pop("pvc_mounts", None) or []:
        sid = claim_to_storage.get(m["claim_name"])
        if sid is None:
            _warn(warnings, "unregistered_pvc", m["claim_name"])
            continue
        storages.append({"storage_id": sid, "mount_path": m["mount_path"], "read_only": m["read_only"]})
    draft["storages"] = storages
```

Also check `build_recipe_draft` for `out["pvc_name"]`/`pvc_mount_path` defaults (`grep -n pvc backend/app/services/recipe_import.py`) and remove them.

In `external_recipe_draft` (model_deployments.py), right after `out = build_recipe_draft(spec)` and `cid = …`:

```python
    claims = [m["claim_name"] for m in out["draft"].get("pvc_mounts") or []]
    attach_storages(out["draft"], out["warnings"], await match_claims(db, cid, namespace, claims))
```

(import `attach_storages` from `app.services.recipe_import`, `match_claims` from `app.services.storage_store`; move the `cid = …` line above if it comes later.)

Messages — under `servingRecipes.warn` in both files: delete `multi_pvc`; add
- ko: `"unregistered_pvc": "등록된 스토리지 바인딩이 없는 PVC라서 건너뛰었습니다. 스토리지 메뉴에서 바인딩을 추가한 뒤 다시 가져오세요:"`
- en: `"unregistered_pvc": "Skipped a PVC with no storage binding. Add a binding under Storage and import again:"`

- [ ] **Step 4: Update `test_recipe_import.py` / `test_recipe_draft_api.py`**

Replace `pvc_name`/`pvc_mount_path` expectations with `pvc_mounts` (unit tests) or `storages` (API test — patch `app.api.model_deployments.match_claims`); drop the `multi_pvc` test.

- [ ] **Step 5: Run tests**

Run:
```bash
PYTHONPATH=. $PY -m pytest tests/test_storage_import.py tests/test_recipe_import.py tests/test_recipe_draft_api.py tests/test_gpu_profiles_api.py -q
$PY -m ruff check app/services/recipe_import.py app/api/model_deployments.py tests/test_storage_import.py tests/test_recipe_import.py tests/test_recipe_draft_api.py
```
Expected: PASS; `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/recipe_import.py backend/app/api/model_deployments.py backend/tests frontend/messages
git commit -m "feat(storage): recipe import maps PVC volumes to registered storages"
```

---

### Task 11: Data migration 052 (backfill + drop default NFS)

**Files:**
- Create: `backend/migrations/versions/052_storages_backfill.py`
- Modify: `backend/app/db/models/custom_k8s_cluster.py` (remove the `default_nfs_*` attributes)
- Test: `backend/tests/test_storage_backfill.py` (pure helpers) + a live upgrade/downgrade check on a copy of the local portal DB

**Interfaces:**
- Consumes: tables from 051.
- Produces: no runtime interface; after upgrade every recipe/deployment that had both `pvc_name` and `pvc_mount_path` has the equivalent `storages`, deployments with snapshots and `volume_name: "model-weights"`. A P/D pool that set either pool key gets `pd_config.<role>.storages` built per key with fallback to the base `pvc_*` (old `pool_row` semantics) — `[]` when the pair is still incomplete; pools that set neither keep inheriting. Every rendered manifest is unchanged. Generated storage names satisfy `validate_storage_name` (≤ 50 chars). Downgrade deletes the `created_by = 'migration'` storages.

- [ ] **Step 1: Write the failing helper tests**

```python
# backend/tests/test_storage_backfill.py
"""Pure helpers of migration 052."""

import importlib.util
import pathlib

from app.services.storage_mounts import validate_storage_name

_path = pathlib.Path(__file__).parents[1] / "migrations/versions/052_storages_backfill.py"
_spec = importlib.util.spec_from_file_location("m052", _path)
m052 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m052)


def test_slug():
    assert m052._slug("Models_PVC.v2") == "models-pvc-v2"
    assert m052._slug("---") == "storage"
    assert len(m052._slug("x" * 80)) <= 40


def test_unique_name():
    taken = {"pvc-models"}
    assert m052._unique("pvc-models", taken) == "pvc-models-2"
    assert "pvc-models-2" in taken


def test_generated_names_pass_the_storage_name_rule():
    long = "c" * 128  # custom_k8s_cluster.name is String(128)
    assert len(m052._bench_name(long)) == 50
    validate_storage_name(m052._bench_name(long))
    validate_storage_name(m052._bench_name("GPU_Cluster.A"))
    validate_storage_name(m052._unique(f"pvc-{m052._slug('x' * 80)}", {f"pvc-{'x' * 40}"}))


def test_pool_pvcs_fall_back_per_field_to_the_base():
    # Old pool_row fell back per field, so a pool that set only one key mounted it
    # together with the base's other key — the migration must reproduce exactly that.
    cfg = {"prefill": {"pvc_name": "p", "pvc_mount_path": "/m", "replicas": 1}, "decode": {"pvc_name": "d"}}
    assert m052._pool_pvcs(cfg, "base", "/models") == {"prefill": ("p", "/m"), "decode": ("d", "/models")}
    assert m052._pool_pvcs({"decode": {"pvc_mount_path": "/x"}}, "base", "/models") == {"decode": ("base", "/x")}
    # No complete pair even with the fallback → the old renderer mounted nothing for that pool.
    assert m052._pool_pvcs({"decode": {"pvc_name": "d"}}, None, None) == {"decode": None}
    assert m052._pool_pvcs({"decode": {"replicas": 2}}, "base", "/models") == {}
    assert m052._pool_pvcs(None, "base", "/models") == {}


def test_strip_pool_pvcs():
    cfg = {"prefill": {"pvc_name": "p", "pvc_mount_path": "/m", "replicas": 1}, "decode": {"pvc_name": "d"}}
    assert m052._strip_pool_pvcs(cfg) == {"prefill": {"replicas": 1}, "decode": {}}
    assert m052._strip_pool_pvcs(None) is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=. $PY -m pytest tests/test_storage_backfill.py -q` → FAIL (file missing).

- [ ] **Step 3: Write the migration**

```python
# backend/migrations/versions/052_storages_backfill.py
"""Backfill storages from pvc_* / pool pvc_* / cluster default NFS; drop default_nfs_*.

- one storage per distinct claim name ("pvc-<claim>", kind weights)
- one pvc binding per (claim, deployment cluster, deployment namespace)
- recipes:     storages = [{storage_id, mount_path, read_only: false}]
- deployments: same + volume_name "model-weights" + resolved snapshot
  (renders byte-identical to the old pvc_* volume — no rollout)
- P/D pools:   pd_config.<role>.pvc_name/pvc_mount_path → pd_config.<role>.storages,
  each key falling back to the row's base pvc_* exactly like the old pool_row
  (a pool whose pair is still incomplete gets storages [] — it mounted nothing)
- clusters with default_nfs_*: storage "<cluster>-bench-weights" + cluster-wide
  nfs binding → bench_storage_id / bench_mount_path; default_nfs_* dropped
pvc_name / pvc_mount_path columns are left in place (unread).

Downgrade restores default_nfs_* and pool pvc_* keys, resets storages / bench_*,
and deletes the storages this migration created (created_by = 'migration'; their
bindings cascade) so a later upgrade starts clean. Storages created by admins
after the upgrade stay until 051's downgrade drops the tables.

Revision ID: 052_storages_backfill
Revises: 051_storages
"""

import json
import re
import uuid

import sqlalchemy as sa
from alembic import op

revision = "052_storages_backfill"
down_revision = "051_storages"
branch_labels = None
depends_on = None

_ROLES = ("prefill", "decode")  # migrations don't import app code (pd_serving.ROLES)
_VOLUME = "model-weights"
_BY = "migration"


def _slug(raw: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (raw or "").lower()).strip("-")[:40].strip("-")
    return s or "storage"


def _bench_name(cluster_name: str) -> str:
    """'<cluster>-bench-weights' within the 50-char storage name limit."""
    return f"{_slug(cluster_name)[:36].strip('-')}-bench-weights"


def _unique(base: str, taken: set[str]) -> str:
    name, n = base, 2
    while name in taken:
        name = f"{base[:46]}-{n}"
        n += 1
    taken.add(name)
    return name


def _pool_pvcs(cfg: dict | None, base_claim: str | None, base_mount: str | None) -> dict:
    """role → (claim, mount) for pools that set either pvc key, falling back per key to
    the base (old pool_row semantics); None when the pair is still incomplete."""
    out: dict[str, tuple[str, str] | None] = {}
    for role in _ROLES:
        pool = (cfg or {}).get(role) or {}
        if not (pool.get("pvc_name") or pool.get("pvc_mount_path")):
            continue
        claim = pool.get("pvc_name") or base_claim
        mount = pool.get("pvc_mount_path") or base_mount
        out[role] = (claim, mount) if claim and mount else None
    return out


def _strip_pool_pvcs(cfg: dict | None) -> dict | None:
    if cfg is None:
        return None
    out = dict(cfg)
    for role in _ROLES:
        if isinstance(out.get(role), dict):
            out[role] = {k: v for k, v in out[role].items() if k not in ("pvc_name", "pvc_mount_path")}
    return out


def _load(value):
    return json.loads(value) if isinstance(value, str) else value


def _insert_storage(bind, name: str, description: str) -> str:
    sid = str(uuid.uuid4())
    bind.execute(
        sa.text(
            "INSERT INTO custom_storage (id, name, description, kind, created_by, updated_by) "
            "VALUES (:id, :name, :desc, 'weights', :by, :by)"
        ),
        {"id": sid, "name": name, "desc": description, "by": _BY},
    )
    return sid


def upgrade() -> None:
    bind = op.get_bind()
    recipes = bind.execute(
        sa.text("SELECT id, pvc_name, pvc_mount_path, pd_config FROM custom_serving_recipe")
    ).mappings().all()
    deps = bind.execute(
        sa.text("SELECT id, cluster_id, namespace, pvc_name, pvc_mount_path, pd_config FROM custom_model_deployment")
    ).mappings().all()

    def pvcs_of(row) -> list[tuple[str, str, str | None]]:
        """[(claim, mount, role|None)] for the base pair and every renderable pool pair."""
        out = []
        if row["pvc_name"] and row["pvc_mount_path"]:
            out.append((row["pvc_name"], row["pvc_mount_path"], None))
        pools = _pool_pvcs(_load(row["pd_config"]), row["pvc_name"], row["pvc_mount_path"])
        for role, pair in pools.items():
            if pair is not None:
                out.append((pair[0], pair[1], role))
        return out

    def empty_pools(row) -> list[str]:
        """Pools that set a pvc key but still had no complete pair (rendered no volume)."""
        pools = _pool_pvcs(_load(row["pd_config"]), row["pvc_name"], row["pvc_mount_path"])
        return [role for role, pair in pools.items() if pair is None]

    taken = {r[0] for r in bind.execute(sa.text("SELECT name FROM custom_storage"))}
    storage_of: dict[str, str] = {}
    for row in [*recipes, *deps]:
        for claim, _, _ in pvcs_of(row):
            if claim not in storage_of:
                name = _unique(f"pvc-{_slug(claim)}", taken)
                storage_of[claim] = _insert_storage(bind, name, f"Migrated from PVC {claim}")

    def binding_id(sid: str, cluster_id, namespace: str, claim: str) -> str:
        found = bind.execute(
            sa.text(
                "SELECT id FROM custom_storage_binding WHERE storage_id = :sid "
                "AND cluster_id IS NOT DISTINCT FROM :cid AND namespace = :ns"
            ),
            {"sid": sid, "cid": cluster_id, "ns": namespace},
        ).scalar()
        if found:
            return str(found)
        bid = str(uuid.uuid4())
        bind.execute(
            sa.text(
                "INSERT INTO custom_storage_binding (id, storage_id, cluster_id, namespace, type, claim_name, "
                "created_by, updated_by) VALUES (:id, :sid, :cid, :ns, 'pvc', :claim, :by, :by)"
            ),
            {"id": bid, "sid": sid, "cid": cluster_id, "ns": namespace, "claim": claim, "by": _BY},
        )
        return bid

    def write(table: str, row_id, storages: list, cfg: dict | None) -> None:
        bind.execute(
            sa.text(f"UPDATE {table} SET storages = CAST(:s AS jsonb), pd_config = CAST(:c AS jsonb) WHERE id = :id"),
            {"s": json.dumps(storages), "c": json.dumps(cfg) if cfg is not None else None, "id": row_id},
        )

    for r in recipes:
        storages = []
        new_cfg = _strip_pool_pvcs(_load(r["pd_config"]))
        for role in empty_pools(r):
            new_cfg[role]["storages"] = []
        for claim, mount, role in pvcs_of(r):
            entry = {"storage_id": storage_of[claim], "mount_path": mount, "read_only": False}
            if role is None:
                storages.append(entry)
            else:
                new_cfg[role]["storages"] = [entry]
        write("custom_serving_recipe", r["id"], storages, new_cfg)

    for d in deps:
        storages = []
        new_cfg = _strip_pool_pvcs(_load(d["pd_config"]))
        for role in empty_pools(d):
            new_cfg[role]["storages"] = []
        for claim, mount, role in pvcs_of(d):
            sid = storage_of[claim]
            bid = binding_id(sid, d["cluster_id"], d["namespace"], claim)
            resolved = {
                "binding_id": bid, "type": "pvc", "claim_name": claim, "namespace": d["namespace"],
                "nfs_server": None, "nfs_path": None, "sub_path": None,
            }
            entry = {
                "storage_id": sid, "mount_path": mount, "read_only": False, "volume_name": _VOLUME,
                "resolved": resolved,
            }
            if role is None:
                storages.append(entry)
            else:
                new_cfg[role]["storages"] = [entry]
        write("custom_model_deployment", d["id"], storages, new_cfg)

    clusters = bind.execute(
        sa.text(
            "SELECT id, name, default_nfs_server, default_nfs_path, default_nfs_mount_path FROM custom_k8s_cluster "
            "WHERE default_nfs_server IS NOT NULL AND default_nfs_path IS NOT NULL "
            "AND default_nfs_mount_path IS NOT NULL"
        )
    ).mappings().all()
    for c in clusters:
        sid = _insert_storage(
            bind, _unique(_bench_name(c["name"]), taken), f"Migrated from cluster {c['name']} default NFS"
        )
        bind.execute(
            sa.text(
                "INSERT INTO custom_storage_binding (id, storage_id, cluster_id, namespace, type, nfs_server, "
                "nfs_path, created_by, updated_by) VALUES (:id, :sid, :cid, NULL, 'nfs', :srv, :path, :by, :by)"
            ),
            {
                "id": str(uuid.uuid4()), "sid": sid, "cid": c["id"], "srv": c["default_nfs_server"],
                "path": c["default_nfs_path"], "by": _BY,
            },
        )
        bind.execute(
            sa.text("UPDATE custom_k8s_cluster SET bench_storage_id = :sid, bench_mount_path = :mp WHERE id = :id"),
            {"sid": sid, "mp": c["default_nfs_mount_path"], "id": c["id"]},
        )

    op.drop_column("custom_k8s_cluster", "default_nfs_mount_path")
    op.drop_column("custom_k8s_cluster", "default_nfs_path")
    op.drop_column("custom_k8s_cluster", "default_nfs_server")


def downgrade() -> None:
    op.add_column("custom_k8s_cluster", sa.Column("default_nfs_server", sa.String(253), nullable=True))
    op.add_column("custom_k8s_cluster", sa.Column("default_nfs_path", sa.String(512), nullable=True))
    op.add_column("custom_k8s_cluster", sa.Column("default_nfs_mount_path", sa.String(512), nullable=True))
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE custom_k8s_cluster c SET default_nfs_server = b.nfs_server, default_nfs_path = b.nfs_path, "
            "default_nfs_mount_path = c.bench_mount_path FROM custom_storage_binding b "
            "WHERE b.storage_id = c.bench_storage_id AND b.cluster_id = c.id AND b.namespace IS NULL "
            "AND b.type = 'nfs'"
        )
    )
    claim_of = {
        str(r["storage_id"]): r["claim_name"]
        for r in bind.execute(
            sa.text(
                "SELECT DISTINCT ON (storage_id) storage_id, claim_name FROM custom_storage_binding "
                "WHERE type = 'pvc'"
            )
        ).mappings()
    }
    for table in ("custom_serving_recipe", "custom_model_deployment"):
        for row in bind.execute(sa.text(f"SELECT id, pd_config FROM {table} WHERE pd_config IS NOT NULL")).mappings():
            cfg = _load(row["pd_config"])
            changed = False
            for role in _ROLES:
                pool = cfg.get(role)
                entries = (pool or {}).pop("storages", None) if isinstance(pool, dict) else None
                if entries:
                    first = entries[0]
                    claim = (first.get("resolved") or {}).get("claim_name") or claim_of.get(first["storage_id"])
                    if claim:
                        pool["pvc_name"], pool["pvc_mount_path"] = claim, first["mount_path"]
                changed = changed or entries is not None
            if changed:
                bind.execute(
                    sa.text(f"UPDATE {table} SET pd_config = CAST(:c AS jsonb) WHERE id = :id"),
                    {"c": json.dumps(cfg), "id": row["id"]},
                )
    # Undo the rest so `downgrade -1` + `upgrade` is repeatable (no "pvc-…-2" names on re-upgrade).
    for table in ("custom_serving_recipe", "custom_model_deployment"):
        bind.execute(sa.text(f"UPDATE {table} SET storages = '[]'::jsonb"))
    bind.execute(sa.text("UPDATE custom_k8s_cluster SET bench_storage_id = NULL, bench_mount_path = NULL"))
    bind.execute(sa.text("DELETE FROM custom_storage WHERE created_by = :by"), {"by": _BY})
```

Remove the three `default_nfs_*` attributes (and their comment) from `custom_k8s_cluster.py`.

- [ ] **Step 4: Run helper tests**

Run:
```bash
PYTHONPATH=. $PY -m pytest tests/test_storage_backfill.py tests/test_storage_schema.py -q
$PY -m ruff check migrations/versions/052_storages_backfill.py app/db/models/custom_k8s_cluster.py tests/test_storage_backfill.py
```
Expected: PASS; `All checks passed!`

- [ ] **Step 5: Live migration check on a copy of the local portal DB**

The local stack's DB container is `litellm_db` (user `llmproxy`, portal DB `litellm_portal`). Never migrate `litellm_portal` itself.

```bash
docker exec litellm_db psql -U llmproxy -d postgres -c "DROP DATABASE IF EXISTS portal_migtest" -c "CREATE DATABASE portal_migtest"
docker exec litellm_db sh -c "pg_dump -U llmproxy litellm_portal | psql -q -U llmproxy portal_migtest"
docker exec litellm_db psql -U llmproxy -d portal_migtest -c "SELECT version_num FROM alembic_version"
```

Seed rows covering every path (adapt column lists to `\d custom_serving_recipe` if NOT NULL columns differ):

```bash
docker exec litellm_db psql -U llmproxy -d portal_migtest <<'SQL'
INSERT INTO custom_serving_recipe (id, name, model_path, image, pvc_name, pvc_mount_path)
VALUES (gen_random_uuid(), 'mig-agg', '/models/llama', 'vllm:1', 'models-pvc', '/models');
INSERT INTO custom_serving_recipe (id, name, model_path, image, serving_mode, pvc_name, pvc_mount_path, pd_config)
VALUES (gen_random_uuid(), 'mig-pd', '/models/llama', 'vllm:1', 'pd', 'models-pvc', '/models',
        '{"prefill": {"replicas": 1, "pvc_name": "prefill-pvc", "pvc_mount_path": "/models"},
          "decode": {"replicas": 1, "pvc_name": "decode-pvc"}}');
INSERT INTO custom_model_deployment (id, model_name, namespace, image, model_path, ingress_host, pvc_name, pvc_mount_path)
VALUES (gen_random_uuid(), 'mig-dep', 'serving-prod', 'vllm:1', '/models/llama', 'h', 'models-pvc', '/models');
UPDATE custom_k8s_cluster SET default_nfs_server = '10.0.0.5', default_nfs_path = '/export', default_nfs_mount_path = '/models'
WHERE id = (SELECT id FROM custom_k8s_cluster LIMIT 1);
SQL
```

Upgrade, inspect, downgrade, re-upgrade:

```bash
cd backend
export APP_DATABASE_URL=postgresql+asyncpg://llmproxy:dbpassword9090@localhost:5432/portal_migtest
PYTHONPATH=. $PY -m alembic upgrade head
docker exec litellm_db psql -U llmproxy -d portal_migtest -c "SELECT name, description FROM custom_storage" \
  -c "SELECT s.name, b.cluster_id, b.namespace, b.type, b.claim_name, b.nfs_server FROM custom_storage_binding b JOIN custom_storage s ON s.id = b.storage_id" \
  -c "SELECT name, storages, pd_config FROM custom_serving_recipe WHERE name LIKE 'mig-%'" \
  -c "SELECT model_name, storages FROM custom_model_deployment WHERE model_name = 'mig-dep'" \
  -c "SELECT name, bench_storage_id, bench_mount_path FROM custom_k8s_cluster"
PYTHONPATH=. $PY -m alembic downgrade 050_pd_serving
docker exec litellm_db psql -U llmproxy -d portal_migtest -c "SELECT name, default_nfs_server, default_nfs_mount_path FROM custom_k8s_cluster" \
  -c "SELECT name, pd_config FROM custom_serving_recipe WHERE name = 'mig-pd'"
PYTHONPATH=. $PY -m alembic upgrade 052_storages_backfill
PYTHONPATH=. $PY -m alembic downgrade 051_storages
docker exec litellm_db psql -U llmproxy -d portal_migtest -c "SELECT count(*) FROM custom_storage" \
  -c "SELECT name, storages FROM custom_serving_recipe WHERE name LIKE 'mig-%'"
PYTHONPATH=. $PY -m alembic upgrade head
docker exec litellm_db psql -U llmproxy -d portal_migtest -c "SELECT name FROM custom_storage ORDER BY name"
unset APP_DATABASE_URL
```

Expected after upgrade: storages `pvc-models-pvc`, `pvc-prefill-pvc`, `pvc-decode-pvc`, `<cluster slug ≤ 36>-bench-weights`; one pvc binding (`mig-dep`'s cluster NULL / `serving-prod` / `models-pvc`), one nfs binding (cluster-wide); `mig-agg.storages` = one read-write entry at `/models`; `mig-pd.storages` = `models-pvc` at `/models`, `mig-pd.pd_config.prefill.storages` = `prefill-pvc` at `/models`, `mig-pd.pd_config.decode.storages` = `decode-pvc` at `/models` (mount path from the base — per-key fallback), no `pvc_*` keys left in either pool; `mig-dep.storages[0]` has `volume_name: model-weights` and a `resolved.binding_id`; the cluster has `bench_mount_path = /models`. After downgrade to `050_pd_serving`: the cluster's `default_nfs_*` and `mig-pd`'s pool `pvc_*` are back (decode now carries both keys — renders the same). After the 052-only downgrade (`downgrade 051_storages`): `count = 0` and every `storages = []`. The final re-upgrade succeeds and lists the same four names (no `-2` suffixes).

Then render the migrated deployment with the worktree code and compare against the old renderer:

```bash
PYTHONPATH=. $PY - <<'EOF'
import asyncio, json
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from app.db.models.custom_model_deployment import CustomModelDeployment
from app.services.model_deployment_manifests import build_all
async def main():
    eng = create_async_engine("postgresql+asyncpg://llmproxy:dbpassword9090@localhost:5432/portal_migtest")
    async with AsyncSession(eng) as s:
        d = (await s.execute(select(CustomModelDeployment).where(CustomModelDeployment.model_name == "mig-dep"))).scalar_one()
        pod = next(m for m in build_all(d) if m["kind"] == "Deployment")["spec"]["template"]["spec"]
        print(json.dumps(pod["volumes"]), json.dumps(pod["containers"][0]["volumeMounts"]))
    await eng.dispose()
asyncio.run(main())
EOF
```

Expected: `[{"name": "model-weights", "persistentVolumeClaim": {"claimName": "models-pvc"}}] [{"name": "model-weights", "mountPath": "/models"}]` — identical to what the old `_volumes` produced from `pvc_name`/`pvc_mount_path`.

Clean up: `docker exec litellm_db psql -U llmproxy -d postgres -c "DROP DATABASE portal_migtest"`.

- [ ] **Step 6: Full backend suite vs baseline**

```bash
SCR=<session scratchpad dir>
PYTHONPATH=. $PY -m pytest -q 2>&1 | grep ^FAILED | sort > $SCR/after.txt
git worktree add -q $SCR/base origin/main
(cd $SCR/base/backend && PYTHONPATH=. $PY -m pytest -q 2>&1 | grep ^FAILED | sort > $SCR/before.txt)
git worktree remove --force $SCR/base
diff $SCR/before.txt $SCR/after.txt && echo same-baseline-failures
```

Expected: `same-baseline-failures` (only the known Postgres-dependent tests fail, on both sides). Never use `git stash` here — the stash stack is shared with other worktrees.

- [ ] **Step 7: Commit**

```bash
git add backend/migrations/versions/052_storages_backfill.py backend/app/db/models/custom_k8s_cluster.py backend/tests/test_storage_backfill.py
git commit -m "feat(storage): backfill storages from pvc/NFS settings (052); drop cluster default_nfs_*"
```

---

### Task 12: Frontend types + hooks

**Files:**
- Modify: `frontend/src/types/index.ts`, `frontend/src/hooks/use-api.ts`

**Interfaces:**
- Produces (types):

```ts
export type StorageKind = "weights" | "cache" | "data" | "other";
export interface StorageEntry { storage_id: string; mount_path: string; read_only: boolean }
export interface StorageSnapshot {
  binding_id: string; type: "pvc" | "nfs"; claim_name: string | null; namespace: string | null;
  nfs_server: string | null; nfs_path: string | null; sub_path: string | null;
}
export interface ResolvedStorageEntry extends StorageEntry { volume_name: string; resolved: StorageSnapshot }
export interface StorageBoundOn { cluster_id: string | null; cluster_name: string; namespace: string | null }
export interface Storage {
  id: string; name: string; description: string | null; kind: StorageKind; created_by: string | null; updated_at: string | null;
  binding_count?: number; bound_on?: StorageBoundOn[]; recipe_count?: number; deployment_count?: number;
}
export interface StorageBinding {
  id: string; storage_id: string; cluster_id: string | null; cluster_name: string; namespace: string | null;
  type: "pvc" | "nfs"; claim_name: string | null; nfs_server: string | null; nfs_path: string | null;
  sub_path: string | null; updated_at: string | null;
}
export interface StorageDetail extends Storage {
  bindings: StorageBinding[];
  usage: {
    recipes: { id: string; name: string }[];
    deployments: { id: string; model_name: string; stale: boolean }[];
    clusters: { id: string; name: string }[];
  };
}
export type StorageInput = Pick<Storage, "name" | "description" | "kind">;
export type StorageBindingInput = Omit<StorageBinding, "id" | "storage_id" | "cluster_name" | "updated_at">;
export type PvcPhase = "Bound" | "Pending" | "Lost" | "missing" | "error";
export interface ClusterPvc { name: string; phase: string | null; capacity: string | null; storage_class: string | null; access_modes: string[] }
export interface StorageResolution { storage_id: string; name: string | null; binding: StorageSnapshot | null; pvc_phase: PvcPhase | null }
```

- Produces (hooks, all `apiFetch` like `useGpuProfiles`):
  - `useStorages(params?: { kind?: string; q?: string })` → `Storage[]` (key `["storages", params]`)
  - `useStorage(id: string)` → `StorageDetail` (key `["storage", id]`)
  - `useCreateStorage()`, `useUpdateStorage(id)`, `useDeleteStorage()` (invalidate `["storages"]`, `["storage", id]`)
  - `useCreateStorageBinding(storageId)`, `useUpdateStorageBinding(storageId)` (`{id, body}`), `useDeleteStorageBinding(storageId)` (`{id, dryRun}` → `{deleted, affected_deployments}`) — invalidate `["storage", storageId]`, `["storage-status", storageId]`, `["storages"]`
  - `useStorageBindingStatus(storageId)` → `Record<string, PvcPhase | null>` (key `["storage-status", storageId]`, `staleTime: 30_000`)
  - `useClusterPvcs(clusterId: string | null, namespace: string, enabled: boolean)` → `{ pvcs: ClusterPvc[]; error: string | null }` (path `${gpuClusterPath(clusterId)}/pvcs?namespace=…`)
  - `useResolveStorages(storageIds: string[], clusterId: string | null, namespace: string)` → `StorageResolution[]` (`useQuery`, key `["storage-resolve", ids, clusterId, namespace]`, `enabled: ids.length > 0 && !!namespace`, POST body)

- [ ] **Step 1: Types.** Add the block above to `types/index.ts` (next to the GPU profile types). Then:
  - Replace `pvc_name`/`pvc_mount_path` in `PoolConfig` with `storages: StorageEntry[] | null;` (null = inherit, for pools).
  - In `ServingRecipe`/`ServingRecipeInput` and `ModelDeployment`/`CreateDeploymentBody`: replace the two `pvc_*` fields with `storages: StorageEntry[]` (`ModelDeployment.storages: ResolvedStorageEntry[]`, plus `storage_stale?: string[]`; responses of create/update carry `warnings?: string[]`).
  - In the cluster type (line ~729): replace `default_nfs_*` with `bench_storage_id: string | null; bench_mount_path: string | null;` (same in its input type).
  - Benchmark snapshot type (~577): keep `pvc_name?`/`pvc_mount_path?` (old runs) and add `storages?: ResolvedStorageEntry[];`.

- [ ] **Step 2: Hooks.** Add after the GPU-profile hooks:

```ts
// ─── Storage ───────────────────────────────────────────────────────────────

export function useStorages(params: { kind?: string; q?: string } = {}) {
  const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v) as [string, string][]).toString();
  return useQuery({
    queryKey: ["storages", params],
    queryFn: () => apiFetch<{ storages: Storage[] }>(`/api/admin/storages${qs ? `?${qs}` : ""}`).then((r) => r.storages),
  });
}

export function useStorage(id: string) {
  return useQuery({ queryKey: ["storage", id], queryFn: () => apiFetch<StorageDetail>(`/api/admin/storages/${id}`) });
}

function useStorageInvalidate() {
  const qc = useQueryClient();
  return (id?: string) => {
    qc.invalidateQueries({ queryKey: ["storages"] });
    if (id) {
      qc.invalidateQueries({ queryKey: ["storage", id] });
      qc.invalidateQueries({ queryKey: ["storage-status", id] });
    }
  };
}

export function useCreateStorage() {
  const invalidate = useStorageInvalidate();
  return useMutation({
    mutationFn: (body: StorageInput) => apiFetch<Storage>("/api/admin/storages", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => invalidate(),
  });
}

export function useUpdateStorage(id: string) {
  const invalidate = useStorageInvalidate();
  return useMutation({
    mutationFn: (body: StorageInput) => apiFetch<Storage>(`/api/admin/storages/${id}`, { method: "PUT", body: JSON.stringify(body) }),
    onSuccess: () => invalidate(id),
  });
}

export function useDeleteStorage() {
  const invalidate = useStorageInvalidate();
  return useMutation({
    mutationFn: (id: string) => apiFetch<{ deleted: boolean }>(`/api/admin/storages/${id}`, { method: "DELETE" }),
    onSuccess: () => invalidate(),
  });
}

export function useCreateStorageBinding(storageId: string) {
  const invalidate = useStorageInvalidate();
  return useMutation({
    mutationFn: (body: StorageBindingInput) =>
      apiFetch<StorageBinding>(`/api/admin/storages/${storageId}/bindings`, { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => invalidate(storageId),
  });
}

export function useUpdateStorageBinding(storageId: string) {
  const invalidate = useStorageInvalidate();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: StorageBindingInput }) =>
      apiFetch<StorageBinding>(`/api/admin/storages/${storageId}/bindings/${id}`, { method: "PUT", body: JSON.stringify(body) }),
    onSuccess: () => invalidate(storageId),
  });
}

export function useDeleteStorageBinding(storageId: string) {
  const invalidate = useStorageInvalidate();
  return useMutation({
    mutationFn: ({ id, dryRun }: { id: string; dryRun: boolean }) =>
      apiFetch<{ deleted: boolean; affected_deployments: { id: string; model_name: string }[] }>(
        `/api/admin/storages/${storageId}/bindings/${id}?dry_run=${dryRun}`, { method: "DELETE" },
      ),
    onSuccess: (r) => { if (r.deleted) invalidate(storageId); },
  });
}

export function useStorageBindingStatus(storageId: string) {
  return useQuery({
    queryKey: ["storage-status", storageId],
    queryFn: () =>
      apiFetch<{ status: Record<string, PvcPhase | null> }>(`/api/admin/storages/${storageId}/bindings/status`).then((r) => r.status),
    staleTime: 30_000,
  });
}

export function useClusterPvcs(clusterId: string | null, namespace: string, enabled: boolean) {
  return useQuery({
    queryKey: ["cluster-pvcs", clusterId ?? "default", namespace],
    queryFn: () =>
      apiFetch<{ pvcs: ClusterPvc[]; error: string | null }>(
        `${gpuClusterPath(clusterId)}/pvcs?namespace=${encodeURIComponent(namespace)}`,
      ),
    enabled: enabled && !!namespace,
  });
}

export function useResolveStorages(storageIds: string[], clusterId: string | null, namespace: string) {
  return useQuery({
    queryKey: ["storage-resolve", storageIds, clusterId ?? "default", namespace],
    queryFn: () =>
      apiFetch<{ results: StorageResolution[] }>("/api/admin/storages/resolve", {
        method: "POST",
        body: JSON.stringify({ storage_ids: storageIds, cluster_id: clusterId, namespace }),
      }).then((r) => r.results),
    enabled: storageIds.length > 0 && !!namespace,
  });
}
```

Add the new types to the hooks file's `import type { … } from "@/types"`. Check `gpuClusterPath` builds `/api/admin/k8s-clusters/<id|default>` (`grep -n "function gpuClusterPath" -A4 src/hooks/use-api.ts`).

- [ ] **Step 3: Type-check**

Run: `cd frontend && npx tsc --noEmit -p .`
Expected: errors only in files that still read `pvc_name`/`default_nfs_*` (`pool-config-cards.tsx`, `pd-serving.ts`, `recipe-drafts.ts`, `serving-recipe-form.tsx`, `deploy-from-recipe-dialog.tsx`, `deployments/[id]/page.tsx`, `cluster-settings-tab.tsx`). They are fixed in Tasks 13–16; note the list.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/hooks/use-api.ts
git commit -m "feat(storage): frontend storage types and API hooks"
```

---

### Task 13: Storage list + detail pages, binding dialog, sidebar

**Files:**
- Create: `frontend/src/app/(app)/admin/storages/page.tsx`, `frontend/src/app/(app)/admin/storages/[id]/page.tsx`, `frontend/src/components/storage-binding-dialog.tsx`, `frontend/src/components/storage-bound-on.tsx`
- Modify: `frontend/src/components/app-sidebar.tsx`, `frontend/messages/ko.json`, `frontend/messages/en.json`

**Interfaces:**
- Consumes: Task 12 hooks; `useK8sClusters()` (existing hook listing clusters — confirm the name with `grep -n "export function use.*Cluster" src/hooks/use-api.ts`).
- Produces: route `/admin/storages`, `/admin/storages/[id]`; `StorageBindingDialog({ storageId, binding, open, onClose })`; `StorageBoundOn({ boundOn })` (chips `<cluster>/<namespace|*>`, or "not bound") in `storage-bound-on.tsx`; i18n namespace `storages`; sidebar key `adminStorages`.

- [ ] **Step 1: Messages.** Add a top-level `"storages"` namespace (ko shown; en mirrors it in English) and `sidebar.adminStorages` (`"스토리지"` / `"Storage"`):

```json
"storages": {
  "title": "스토리지",
  "subtitle": "클러스터·네임스페이스마다 실제 PVC 또는 NFS에 연결해 두고, 레시피에서는 스토리지 이름만 고릅니다.",
  "newStorage": "스토리지 추가",
  "empty": "등록된 스토리지가 없습니다.",
  "search": "이름·설명 검색",
  "allKinds": "전체 용도",
  "kind_weights": "모델 가중치", "kind_cache": "캐시", "kind_data": "데이터", "kind_other": "기타",
  "colName": "이름", "colKind": "용도", "colDescription": "설명", "colBoundOn": "바인딩된 클러스터", "colUsage": "사용처",
  "usageCount": "레시피 {recipes} · 배포 {deployments}",
  "name": "이름", "nameHint": "영문 소문자·숫자·하이픈, 50자 이하 (예: llama-weights)",
  "description": "설명", "kind": "용도",
  "save": "저장", "create": "추가", "cancel": "취소", "delete": "삭제", "edit": "편집",
  "saved": "저장했습니다.", "saveError": "저장하지 못했습니다.",
  "deleteConfirm": "스토리지 '{name}'을(를) 삭제할까요?",
  "inUse": "사용 중이라 삭제할 수 없습니다. 레시피·배포·클러스터 벤치마크 설정에서 먼저 빼 주세요.",
  "bindings": "바인딩",
  "bindingsHint": "배포할 때 (클러스터, 네임스페이스)가 정확히 맞는 바인딩을 먼저 쓰고, 없으면 네임스페이스 공통(*) 바인딩을 씁니다.",
  "addBinding": "바인딩 추가", "editBinding": "바인딩 편집",
  "colCluster": "클러스터", "colNamespace": "네임스페이스", "colType": "종류", "colTarget": "대상", "colSubPath": "하위 경로", "colStatus": "상태",
  "allNamespaces": "* (전체)",
  "status_Bound": "Bound", "status_Pending": "Pending", "status_Lost": "Lost", "status_missing": "PVC 없음", "status_error": "조회 실패",
  "cluster": "클러스터", "clusterDefault": "포털 기본 클러스터",
  "namespace": "네임스페이스", "namespaceHintPvc": "PVC가 있는 네임스페이스 (필수)", "namespaceHintNfs": "비우면 이 클러스터의 모든 네임스페이스에 적용",
  "type": "종류", "claimName": "PVC", "claimManual": "직접 입력", "pvcLookupFailed": "PVC 목록을 가져오지 못했습니다: {error}",
  "nfsServer": "NFS 서버", "nfsPath": "내보내기 경로", "subPath": "하위 경로 (선택)", "subPathHint": "스토리지 안의 하위 디렉터리 (상대 경로)",
  "deleteBindingConfirm": "이 바인딩을 삭제할까요?",
  "deleteBindingAffected": "실행 중인 배포 {count}개가 이 바인딩을 쓰고 있습니다 ({names}). 지금 떠 있는 배포는 그대로지만, 재배포하면 실패합니다. 삭제할까요?",
  "usage": "사용처", "usageRecipes": "레시피", "usageDeployments": "배포", "usageClusters": "벤치마크 기본 스토리지", "usageNone": "아직 쓰는 곳이 없습니다.",
  "picker": "스토리지", "pickerPlaceholder": "스토리지 선택", "mountPath": "마운트 경로", "readOnly": "읽기 전용", "addMount": "스토리지 추가",
  "noStorages": "등록된 스토리지가 없습니다. 관리자 → 스토리지에서 먼저 추가하세요.",
  "notBound": "바인딩 없음",
  "modelPathUnder": "{name} 안의 경로입니다.", "modelPathOutside": "모델 경로가 붙인 스토리지 안에 있지 않습니다.",
  "inheritBase": "기본 스토리지 사용", "overrideForPool": "이 풀만 따로 지정",
  "resolution": "스토리지 연결 결과", "resolutionNone": "바인딩 없음", "resolutionAll": "클러스터 공통",
  "addBindingLink": "바인딩 추가하기", "deployBlocked": "바인딩이 없는 스토리지가 있어 배포할 수 없습니다.",
  "volumes": "마운트된 스토리지", "stale": "바인딩이 바뀜 · 재배포 필요",
  "warn_model_path_outside_storages": "모델 경로가 붙인 스토리지 안에 있지 않습니다.",
  "warn_pvc_pending": "PVC가 아직 Bound 상태가 아닙니다: {claim}",
  "warn_pvc_missing": "네임스페이스에 PVC가 없습니다: {claim}"
}
```

- [ ] **Step 2: Sidebar.** In `app-sidebar.tsx` add after `adminRecipes`:

```ts
  { key: "adminStorages", href: "/admin/storages", icon: HardDrive, roles: ["super_user"], section: "serving" },
```

and import `HardDrive` from `lucide-react`.

- [ ] **Step 3: Bound-on chips + list page**

```tsx
// frontend/src/components/storage-bound-on.tsx
"use client";

import { useTranslations } from "next-intl";

import type { StorageBoundOn as BoundOn } from "@/types";
import { Badge } from "@/components/ui/badge";

/** Where a storage is bound: one chip per binding, "<cluster>/<namespace>" (`*` = every namespace). */
export function StorageBoundOn({ boundOn }: { boundOn: BoundOn[] | undefined }) {
  const t = useTranslations("storages");
  if (!boundOn?.length) return <span className="text-xs text-amber-600">{t("notBound")}</span>;
  return (
    <div className="flex flex-wrap gap-1">
      {boundOn.map((b) => (
        <Badge key={`${b.cluster_id ?? "default"}/${b.namespace ?? "*"}`} variant="outline" className="font-mono text-xs">
          {b.cluster_name}/{b.namespace ?? "*"}
        </Badge>
      ))}
    </div>
  );
}
```

```tsx
// frontend/src/app/(app)/admin/storages/page.tsx
"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import { useCreateStorage, useStorages } from "@/hooks/use-api";
import type { StorageKind } from "@/types";
import { StorageBoundOn } from "@/components/storage-bound-on";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

const KINDS: StorageKind[] = ["weights", "cache", "data", "other"];

export default function AdminStoragesPage() {
  const t = useTranslations("storages");
  const router = useRouter();
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const { data: storages, isLoading } = useStorages({ kind: kind || undefined, q: q.trim() || undefined });
  const create = useCreateStorage();
  const [name, setName] = useState("");

  function add() {
    create.mutate(
      { name: name.trim(), description: null, kind: "weights" },
      {
        onSuccess: (s) => router.push(`/admin/storages/${s.id}`),
        onError: (e) => toast.error(e instanceof Error ? e.message : t("saveError")),
      },
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">{t("title")}</h1>
        <p className="text-muted-foreground mt-1">{t("subtitle")}</p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Input className="w-64" placeholder={t("search")} value={q} onChange={(e) => setQ(e.target.value)} />
        <select
          className="h-9 rounded-md border border-input bg-transparent px-3 text-sm"
          value={kind}
          onChange={(e) => setKind(e.target.value)}
          aria-label={t("kind")}
        >
          <option value="">{t("allKinds")}</option>
          {KINDS.map((k) => <option key={k} value={k}>{t(`kind_${k}`)}</option>)}
        </select>
        <div className="ml-auto flex items-center gap-2">
          <Input
            id="new-storage-name"
            className="w-56 font-mono"
            placeholder="llama-weights"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <Button onClick={add} disabled={!name.trim() || create.isPending} data-testid="storage-create">{t("newStorage")}</Button>
        </div>
      </div>

      {isLoading ? (
        <div className="h-32 animate-pulse rounded-lg bg-muted" />
      ) : !storages || storages.length === 0 ? (
        <div className="rounded-lg border border-dashed p-8 text-center text-muted-foreground">{t("empty")}</div>
      ) : (
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("colName")}</TableHead>
                <TableHead>{t("colKind")}</TableHead>
                <TableHead>{t("colDescription")}</TableHead>
                <TableHead>{t("colBoundOn")}</TableHead>
                <TableHead>{t("colUsage")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {storages.map((s) => (
                <TableRow key={s.id} data-testid={`storage-row-${s.name}`}>
                  <TableCell className="font-mono font-medium">
                    <Link className="hover:underline" href={`/admin/storages/${s.id}`}>{s.name}</Link>
                  </TableCell>
                  <TableCell><Badge variant="secondary">{t(`kind_${s.kind}`)}</Badge></TableCell>
                  <TableCell className="text-sm text-muted-foreground">{s.description ?? "-"}</TableCell>
                  <TableCell><StorageBoundOn boundOn={s.bound_on} /></TableCell>
                  <TableCell className="text-sm">
                    {t("usageCount", { recipes: s.recipe_count ?? 0, deployments: s.deployment_count ?? 0 })}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Binding dialog**

```tsx
// frontend/src/components/storage-binding-dialog.tsx
"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { useClusterPvcs, useCreateStorageBinding, useK8sClusters, useUpdateStorageBinding } from "@/hooks/use-api";
import type { StorageBinding, StorageBindingInput } from "@/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const MANUAL = "__manual__";

export function StorageBindingDialog({
  storageId, binding, open, onClose,
}: { storageId: string; binding: StorageBinding | null; open: boolean; onClose: () => void }) {
  const t = useTranslations("storages");
  const { data: clusters } = useK8sClusters();
  const create = useCreateStorageBinding(storageId);
  const update = useUpdateStorageBinding(storageId);
  const [form, setForm] = useState<StorageBindingInput>(() => ({
    cluster_id: binding?.cluster_id ?? null,
    namespace: binding?.namespace ?? "",
    type: binding?.type ?? "pvc",
    claim_name: binding?.claim_name ?? "",
    nfs_server: binding?.nfs_server ?? "",
    nfs_path: binding?.nfs_path ?? "",
    sub_path: binding?.sub_path ?? "",
  }));
  const [manual, setManual] = useState(false);
  const set = (patch: Partial<StorageBindingInput>) => setForm((f) => ({ ...f, ...patch }));
  const ns = (form.namespace ?? "").trim();
  const pvcs = useClusterPvcs(form.cluster_id, ns, open && form.type === "pvc");
  const pvcList = pvcs.data?.pvcs ?? [];
  const showManual = manual || !!pvcs.data?.error || (!!form.claim_name && !pvcList.some((p) => p.name === form.claim_name));

  function save() {
    const body: StorageBindingInput = {
      cluster_id: form.cluster_id,
      namespace: ns || null,
      type: form.type,
      claim_name: form.type === "pvc" ? (form.claim_name ?? "").trim() || null : null,
      nfs_server: form.type === "nfs" ? (form.nfs_server ?? "").trim() || null : null,
      nfs_path: form.type === "nfs" ? (form.nfs_path ?? "").trim() || null : null,
      sub_path: (form.sub_path ?? "").trim() || null,
    };
    const opts = {
      onSuccess: () => { toast.success(t("saved")); onClose(); },
      onError: (e: unknown) => toast.error(e instanceof Error ? e.message : t("saveError")),
    };
    if (binding) update.mutate({ id: binding.id, body }, opts);
    else create.mutate(body, opts);
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent>
        <DialogHeader><DialogTitle>{binding ? t("editBinding") : t("addBinding")}</DialogTitle></DialogHeader>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="space-y-1">
            <Label htmlFor="binding-cluster">{t("cluster")}</Label>
            <select
              id="binding-cluster"
              className="w-full h-9 rounded-md border border-input bg-transparent px-3 text-sm"
              value={form.cluster_id ?? ""}
              onChange={(e) => set({ cluster_id: e.target.value || null })}
            >
              <option value="">{t("clusterDefault")}</option>
              {(clusters ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="binding-type">{t("type")}</Label>
            <select
              id="binding-type"
              className="w-full h-9 rounded-md border border-input bg-transparent px-3 text-sm"
              value={form.type}
              onChange={(e) => set({ type: e.target.value as "pvc" | "nfs" })}
            >
              <option value="pvc">PVC</option>
              <option value="nfs">NFS</option>
            </select>
          </div>
          <div className="space-y-1 sm:col-span-2">
            <Label htmlFor="binding-namespace">{t("namespace")}</Label>
            <Input id="binding-namespace" className="font-mono" value={form.namespace ?? ""} onChange={(e) => set({ namespace: e.target.value })} />
            <p className="text-xs text-muted-foreground">{form.type === "pvc" ? t("namespaceHintPvc") : t("namespaceHintNfs")}</p>
          </div>
          {form.type === "pvc" ? (
            <div className="space-y-1 sm:col-span-2">
              <Label htmlFor="binding-claim">{t("claimName")}</Label>
              {!showManual && pvcList.length > 0 ? (
                <select
                  id="binding-claim"
                  className="w-full h-9 rounded-md border border-input bg-transparent px-3 font-mono text-sm"
                  value={form.claim_name ?? ""}
                  onChange={(e) => (e.target.value === MANUAL ? setManual(true) : set({ claim_name: e.target.value }))}
                >
                  <option value="" />
                  {pvcList.map((p) => (
                    <option key={p.name} value={p.name}>
                      {p.name} · {p.phase ?? "?"}{p.capacity ? ` · ${p.capacity}` : ""}
                    </option>
                  ))}
                  <option value={MANUAL}>{t("claimManual")}</option>
                </select>
              ) : (
                <Input id="binding-claim" className="font-mono" value={form.claim_name ?? ""} onChange={(e) => set({ claim_name: e.target.value })} />
              )}
              {pvcs.data?.error && <p className="text-xs text-amber-600">{t("pvcLookupFailed", { error: pvcs.data.error })}</p>}
            </div>
          ) : (
            <>
              <div className="space-y-1">
                <Label htmlFor="binding-nfs-server">{t("nfsServer")}</Label>
                <Input id="binding-nfs-server" className="font-mono" value={form.nfs_server ?? ""} onChange={(e) => set({ nfs_server: e.target.value })} />
              </div>
              <div className="space-y-1">
                <Label htmlFor="binding-nfs-path">{t("nfsPath")}</Label>
                <Input id="binding-nfs-path" className="font-mono" placeholder="/export/models" value={form.nfs_path ?? ""} onChange={(e) => set({ nfs_path: e.target.value })} />
              </div>
            </>
          )}
          <div className="space-y-1 sm:col-span-2">
            <Label htmlFor="binding-sub-path">{t("subPath")}</Label>
            <Input id="binding-sub-path" className="font-mono" value={form.sub_path ?? ""} onChange={(e) => set({ sub_path: e.target.value })} />
            <p className="text-xs text-muted-foreground">{t("subPathHint")}</p>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>{t("cancel")}</Button>
          <Button onClick={save} disabled={create.isPending || update.isPending} data-testid="binding-save">{t("save")}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 5: Detail page**

```tsx
// frontend/src/app/(app)/admin/storages/[id]/page.tsx
"use client";

import { use, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import {
  useDeleteStorage, useDeleteStorageBinding, useStorage, useStorageBindingStatus, useUpdateStorage,
} from "@/hooks/use-api";
import type { StorageBinding, StorageDetail, StorageKind } from "@/types";
import { StorageBindingDialog } from "@/components/storage-binding-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

const KINDS: StorageKind[] = ["weights", "cache", "data", "other"];

export default function StorageDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data, isLoading } = useStorage(id);
  if (isLoading || !data) return <div className="h-32 animate-pulse rounded-lg bg-muted" />;
  return <StorageDetailView key={data.updated_at ?? data.id} storage={data} />;
}

function target(b: StorageBinding) {
  return b.type === "pvc" ? b.claim_name : `${b.nfs_server}:${b.nfs_path}`;
}

function StorageDetailView({ storage }: { storage: StorageDetail }) {
  const t = useTranslations("storages");
  const router = useRouter();
  const update = useUpdateStorage(storage.id);
  const remove = useDeleteStorage();
  const removeBinding = useDeleteStorageBinding(storage.id);
  const { data: status } = useStorageBindingStatus(storage.id);
  const [form, setForm] = useState({ name: storage.name, description: storage.description ?? "", kind: storage.kind });
  const [dialog, setDialog] = useState<{ binding: StorageBinding | null } | null>(null);
  const inUse = storage.usage.recipes.length + storage.usage.deployments.length + storage.usage.clusters.length > 0;

  function save() {
    update.mutate(
      { name: form.name.trim(), description: form.description.trim() || null, kind: form.kind },
      { onSuccess: () => toast.success(t("saved")), onError: (e) => toast.error(e instanceof Error ? e.message : t("saveError")) },
    );
  }

  function del() {
    if (!confirm(t("deleteConfirm", { name: storage.name }))) return;
    remove.mutate(storage.id, {
      onSuccess: () => router.push("/admin/storages"),
      onError: (e) => toast.error(e instanceof Error ? e.message : t("saveError")),
    });
  }

  async function delBinding(b: StorageBinding) {
    const dry = await removeBinding.mutateAsync({ id: b.id, dryRun: true });
    const n = dry.affected_deployments.length;
    const msg = n
      ? t("deleteBindingAffected", { count: n, names: dry.affected_deployments.map((d) => d.model_name).join(", ") })
      : t("deleteBindingConfirm");
    if (!confirm(msg)) return;
    removeBinding.mutate({ id: b.id, dryRun: false }, { onError: (e) => toast.error(e instanceof Error ? e.message : t("saveError")) });
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-2">
        <h1 className="font-mono text-2xl font-bold">{storage.name}</h1>
        <Button variant="destructive" size="sm" onClick={del} disabled={inUse} title={inUse ? t("inUse") : undefined}>
          {t("delete")}
        </Button>
      </div>

      <Card>
        <CardContent className="grid grid-cols-1 gap-3 pt-6 sm:grid-cols-3">
          <div className="space-y-1">
            <Label htmlFor="storage-name">{t("name")}</Label>
            <Input id="storage-name" className="font-mono" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            <p className="text-xs text-muted-foreground">{t("nameHint")}</p>
          </div>
          <div className="space-y-1">
            <Label htmlFor="storage-kind">{t("kind")}</Label>
            <select
              id="storage-kind"
              className="w-full h-9 rounded-md border border-input bg-transparent px-3 text-sm"
              value={form.kind}
              onChange={(e) => setForm({ ...form, kind: e.target.value as StorageKind })}
            >
              {KINDS.map((k) => <option key={k} value={k}>{t(`kind_${k}`)}</option>)}
            </select>
          </div>
          <div className="space-y-1 sm:col-span-3">
            <Label htmlFor="storage-description">{t("description")}</Label>
            <Input id="storage-description" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          </div>
          <div className="sm:col-span-3 flex justify-end">
            <Button onClick={save} disabled={update.isPending}>{t("save")}</Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <div>
            <CardTitle className="text-base">{t("bindings")}</CardTitle>
            <p className="text-xs text-muted-foreground mt-1">{t("bindingsHint")}</p>
          </div>
          <Button size="sm" onClick={() => setDialog({ binding: null })} data-testid="binding-add">{t("addBinding")}</Button>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("colCluster")}</TableHead>
                <TableHead>{t("colNamespace")}</TableHead>
                <TableHead>{t("colType")}</TableHead>
                <TableHead>{t("colTarget")}</TableHead>
                <TableHead>{t("colSubPath")}</TableHead>
                <TableHead>{t("colStatus")}</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {storage.bindings.map((b) => {
                const st = status?.[b.id];
                return (
                  <TableRow key={b.id} data-testid={`binding-row-${b.id}`}>
                    <TableCell>{b.cluster_name}</TableCell>
                    <TableCell className="font-mono text-xs">{b.namespace ?? t("allNamespaces")}</TableCell>
                    <TableCell><Badge variant="outline" className="uppercase">{b.type}</Badge></TableCell>
                    <TableCell className="font-mono text-xs">{target(b)}</TableCell>
                    <TableCell className="font-mono text-xs">{b.sub_path ?? "-"}</TableCell>
                    <TableCell>
                      {st ? (
                        <Badge variant={st === "Bound" ? "secondary" : "destructive"}>{t(`status_${st}`)}</Badge>
                      ) : "—"}
                    </TableCell>
                    <TableCell className="space-x-2 text-right whitespace-nowrap">
                      <Button size="xs" variant="outline" onClick={() => setDialog({ binding: b })}>{t("edit")}</Button>
                      <Button size="xs" variant="destructive" onClick={() => delBinding(b)}>{t("delete")}</Button>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">{t("usage")}</CardTitle></CardHeader>
        <CardContent className="space-y-2 text-sm">
          {!inUse && <p className="text-muted-foreground">{t("usageNone")}</p>}
          {storage.usage.recipes.length > 0 && (
            <p>{t("usageRecipes")}: {storage.usage.recipes.map((r, i) => (
              <span key={r.id}>{i > 0 && ", "}<Link className="underline" href={`/admin/recipes/${r.id}`}>{r.name}</Link></span>
            ))}</p>
          )}
          {storage.usage.deployments.length > 0 && (
            <p>{t("usageDeployments")}: {storage.usage.deployments.map((d, i) => (
              <span key={d.id} data-testid={`usage-deployment-${d.id}`}>
                {i > 0 && ", "}
                <Link className="underline" href={`/admin/deployments/${d.id}`}>{d.model_name}</Link>
                {d.stale && <Badge variant="destructive" className="ml-1">{t("stale")}</Badge>}
              </span>
            ))}</p>
          )}
          {storage.usage.clusters.length > 0 && (
            <p>{t("usageClusters")}: {storage.usage.clusters.map((c) => c.name).join(", ")}</p>
          )}
        </CardContent>
      </Card>

      {dialog && (
        <StorageBindingDialog
          key={dialog.binding?.id ?? "new"}
          storageId={storage.id}
          binding={dialog.binding}
          open
          onClose={() => setDialog(null)}
        />
      )}
    </div>
  );
}
```

Check: `Button size="xs"` exists (used on the recipes page); dynamic route params follow the existing `[id]` pages — open `src/app/(app)/admin/recipes/[id]/page.tsx` and use the same `params` pattern if it isn't `use(params)`. Each usage deployment carries `stale` from `GET /storages/{id}` and shows the `stale` badge (spec: usage section with stale badge).

- [ ] **Step 6: Check**

Run: `npx tsc --noEmit -p . 2>&1 | grep -E "storages|storage-binding|storage-bound-on|app-sidebar"` (expect nothing), `npx eslint "src/app/(app)/admin/storages" src/components/storage-binding-dialog.tsx src/components/storage-bound-on.tsx src/components/app-sidebar.tsx`.

- [ ] **Step 7: Commit**

```bash
git add "frontend/src/app/(app)/admin/storages" frontend/src/components/storage-binding-dialog.tsx frontend/src/components/storage-bound-on.tsx frontend/src/components/app-sidebar.tsx frontend/messages
git commit -m "feat(storage): storage list/detail pages, binding dialog with PVC picker, sidebar entry"
```

---

### Task 14: Recipe form storage rows (+ P/D pool override, recipe drafts)

**Files:**
- Create: `frontend/src/components/storage-mounts-editor.tsx`
- Modify: `frontend/src/components/pool-config-cards.tsx`, `frontend/src/lib/pd-serving.ts`, `frontend/src/lib/recipe-drafts.ts`, `frontend/src/components/serving-recipe-form.tsx`

**Interfaces:**
- Consumes: Task 12 `useStorages`, `StorageEntry`, `Storage` (with `bound_on`); Task 13 `StorageBoundOn` (`@/components/storage-bound-on`).
- Produces: `StorageMountsEditor({ value, onChange, modelPath, idPrefix, inheritable? })` where `value: StorageEntry[] | null` (null only when `inheritable`).

- [ ] **Step 1: Editor component**

```tsx
// frontend/src/components/storage-mounts-editor.tsx
"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import { Plus, X } from "lucide-react";

import { useStorages } from "@/hooks/use-api";
import type { StorageEntry } from "@/types";
import { StorageBoundOn } from "@/components/storage-bound-on";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/** Which entry an absolute model path lives under (mirrors backend model_path_warnings). */
export function storageForPath(path: string, entries: StorageEntry[]): StorageEntry | null | undefined {
  if (!path.startsWith("/")) return undefined; // HF repo id: no hint
  return entries.find((e) => path === e.mount_path || path.startsWith(e.mount_path.replace(/\/$/, "") + "/")) ?? null;
}

/**
 * Storage rows of a recipe or one P/D pool: [storage ▾] [mount path] [read-only] [✕],
 * each picked storage followed by chips of where it is bound.
 * With `inheritable`, `null` means "use the recipe's list" (pool tabs).
 */
export function StorageMountsEditor({
  value, onChange, modelPath, idPrefix, inheritable,
}: {
  value: StorageEntry[] | null;
  onChange: (next: StorageEntry[] | null) => void;
  modelPath: string;
  idPrefix: string;
  inheritable?: boolean;
}) {
  const t = useTranslations("storages");
  const { data: storages } = useStorages();
  const byId = new Map((storages ?? []).map((s) => [s.id, s]));
  const rows = value ?? [];
  const set = (i: number, patch: Partial<StorageEntry>) => onChange(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const hint = modelPath ? storageForPath(modelPath, rows) : undefined;

  if (inheritable && value === null) {
    return (
      <div className="flex items-center justify-between rounded-md border border-dashed p-3 text-sm">
        <span className="text-muted-foreground">{t("inheritBase")}</span>
        <Button type="button" size="xs" variant="outline" onClick={() => onChange([])}>{t("overrideForPool")}</Button>
      </div>
    );
  }

  return (
    <div className="space-y-2" data-testid={`${idPrefix}-storages`}>
      {(storages ?? []).length === 0 && (
        <p className="text-xs text-muted-foreground">
          {t("noStorages")} <Link className="underline" href="/admin/storages">{t("title")}</Link>
        </p>
      )}
      {rows.map((r, i) => (
        <div key={i} className="grid grid-cols-[1fr_1fr_auto_auto] items-center gap-2">
          <select
            id={`${idPrefix}-storage-${i}`}
            aria-label={t("picker")}
            className="h-9 rounded-md border border-input bg-transparent px-3 font-mono text-sm"
            value={r.storage_id}
            onChange={(e) => set(i, { storage_id: e.target.value })}
          >
            <option value="">{t("pickerPlaceholder")}</option>
            {(storages ?? []).map((s) => (
              <option key={s.id} value={s.id} disabled={rows.some((o, j) => j !== i && o.storage_id === s.id)}>
                {s.name}{s.binding_count ? "" : ` (${t("notBound")})`}
              </option>
            ))}
          </select>
          <Input
            id={`${idPrefix}-storage-mount-${i}`}
            aria-label={t("mountPath")}
            className="font-mono"
            placeholder="/models"
            value={r.mount_path}
            onChange={(e) => set(i, { mount_path: e.target.value })}
          />
          <label className="flex items-center gap-1 text-xs whitespace-nowrap">
            <input type="checkbox" checked={r.read_only} onChange={(e) => set(i, { read_only: e.target.checked })} />
            {t("readOnly")}
          </label>
          <Button type="button" size="icon" variant="ghost" aria-label={t("delete")} onClick={() => onChange(rows.filter((_, j) => j !== i))}>
            <X className="size-4" />
          </Button>
          {r.storage_id && (
            <div className="col-span-4" data-testid={`${idPrefix}-storage-bound-${i}`}>
              <StorageBoundOn boundOn={byId.get(r.storage_id)?.bound_on} />
            </div>
          )}
        </div>
      ))}
      <div className="flex items-center gap-3">
        <Button
          type="button"
          size="xs"
          variant="outline"
          onClick={() => onChange([...rows, { storage_id: "", mount_path: rows.length ? "" : "/models", read_only: true }])}
          data-testid={`${idPrefix}-storage-add`}
        >
          <Plus className="size-3" /> {t("addMount")}
        </Button>
        {inheritable && (
          <Button type="button" size="xs" variant="ghost" onClick={() => onChange(null)}>{t("inheritBase")}</Button>
        )}
      </div>
      {hint !== undefined && (
        <p className={hint ? "text-xs text-muted-foreground" : "text-xs text-amber-600"} data-testid={`${idPrefix}-model-path-hint`}>
          {hint ? t("modelPathUnder", { name: byId.get(hint.storage_id)?.name ?? "?" }) : t("modelPathOutside")}
        </p>
      )}
    </div>
  );
}
```

- [ ] **Step 2: `pd-serving.ts`.** In `POOL_KEYS` replace `"pvc_name", "pvc_mount_path"` with `"storages"`; in `EMPTY_POOL` replace them with `storages: null`; in `effectiveRole` replace the two `pvc_*` lines with `storages: r.storages ?? base.storages ?? [],`.

- [ ] **Step 3: `pool-config-cards.tsx`.**
  - Remove `"pvc_name" | "pvc_mount_path"` from `OptionalText`.
  - Add a prop `inheritStorages?: boolean` (true on P/D pool tabs) and replace the Storage card body with:

```tsx
      <Card>
        <CardHeader><CardTitle className="text-base">{t("sectionStorage")}</CardTitle></CardHeader>
        <CardContent>
          <StorageMountsEditor
            idPrefix={idPrefix}
            value={inheritStorages ? value.storages : value.storages ?? []}
            onChange={(storages) => onChange({ storages })}
            modelPath={value.model_path}
            inheritable={inheritStorages}
          />
        </CardContent>
      </Card>
```

  (import `StorageMountsEditor`).

- [ ] **Step 4: `serving-recipe-form.tsx`.** In the empty-recipe defaults (line ~29) replace `pvc_name: null, pvc_mount_path: null` with `storages: []`. Where the P/D pool tabs render `PoolConfigCards` (the `TabsContent` per role), pass `inheritStorages`. When building the submit body, drop rows without a storage: `storages: (form.storages ?? []).filter((s) => s.storage_id)`, and for each pool `storages: pool.storages === null ? null : pool.storages.filter((s) => s.storage_id)`. Then find how the form compares a pool to the base to decide which overrides to store (search for where `pd_config` is assembled) and make sure `storages: null` is omitted from the role block. After save, show `warnings` from the response with `toast.warning(t(\`warn_${code}\`))` using the `storages` namespace (`useTranslations("storages")`).

- [ ] **Step 5: `recipe-drafts.ts`.** In `recipeFromDeployment` replace the two `pvc_*` lines with
  `storages: (dep.storages ?? []).map(({ storage_id, mount_path, read_only }) => ({ storage_id, mount_path, read_only })),`. In the external-draft conversion (search the file for `pvc_name`), use the draft's `storages` (already plain entries).

- [ ] **Step 6: Check**

Run: `npx tsc --noEmit -p .` — remaining errors only in `deploy-from-recipe-dialog.tsx`, `deployments/[id]/page.tsx`, `cluster-settings-tab.tsx` (Tasks 15–16). `npx eslint src/components/storage-mounts-editor.tsx src/components/pool-config-cards.tsx src/components/serving-recipe-form.tsx src/lib/pd-serving.ts src/lib/recipe-drafts.ts`.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/storage-mounts-editor.tsx frontend/src/components/pool-config-cards.tsx frontend/src/components/serving-recipe-form.tsx frontend/src/lib
git commit -m "feat(storage): recipe form picks storages per recipe and per P/D pool"
```

---

### Task 15: Deploy dialog preview + deployment detail volumes

**Files:**
- Create: `frontend/src/components/storage-resolution-preview.tsx`
- Modify: `frontend/src/components/deploy-from-recipe-dialog.tsx`, `frontend/src/app/(app)/admin/deployments/[id]/page.tsx`

**Interfaces:**
- Consumes: Task 12 `useResolveStorages`, `ResolvedStorageEntry`.
- Produces: `StorageResolutionPreview({ storageIds, clusterId, namespace, onBlockedChange })`; `recipeStorageIds(recipe): string[]` (base + pool lists, unique).

- [ ] **Step 1: Preview component**

```tsx
// frontend/src/components/storage-resolution-preview.tsx
"use client";

import { useEffect } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { useResolveStorages } from "@/hooks/use-api";
import type { ServingRecipe, StorageResolution } from "@/types";

export function recipeStorageIds(recipe: Pick<ServingRecipe, "storages" | "pd_config">): string[] {
  const ids = new Set((recipe.storages ?? []).map((s) => s.storage_id));
  for (const role of ["prefill", "decode"] as const) {
    for (const s of recipe.pd_config?.[role]?.storages ?? []) ids.add(s.storage_id);
  }
  return [...ids];
}

function targetOf(r: StorageResolution, t: (k: string) => string): string {
  const b = r.binding;
  if (!b) return t("resolutionNone");
  if (b.type === "pvc") return `PVC ${b.claim_name} (${b.namespace})`;
  return `NFS ${b.nfs_server}:${b.nfs_path}${b.namespace ? ` (${b.namespace})` : ` (${t("resolutionAll")})`}`;
}

/** Per-storage resolution for the chosen cluster/namespace; reports whether any storage is unbound. */
export function StorageResolutionPreview({
  storageIds, clusterId, namespace, onBlockedChange,
}: {
  storageIds: string[];
  clusterId: string | null;
  namespace: string;
  onBlockedChange: (blocked: boolean) => void;
}) {
  const t = useTranslations("storages");
  const { data, isFetching } = useResolveStorages(storageIds, clusterId, namespace);
  const blocked = storageIds.length > 0 && (!data || data.some((r) => !r.binding));

  useEffect(() => { onBlockedChange(blocked); }, [blocked, onBlockedChange]);

  if (storageIds.length === 0) return null;
  return (
    <div className="sm:col-span-2 rounded-md border bg-muted/30 p-3 text-xs" data-testid="storage-resolution">
      <div className="mb-1 font-medium">{t("resolution")}</div>
      {isFetching && !data ? (
        <div className="h-4 w-40 animate-pulse rounded bg-muted" />
      ) : (
        <ul className="space-y-1">
          {(data ?? []).map((r) => (
            <li key={r.storage_id} className="flex flex-wrap items-center gap-2 font-mono">
              <span className={r.binding ? "" : "text-destructive"}>{r.binding ? "✓" : "✗"}</span>
              <span className="font-medium">{r.name ?? r.storage_id}</span>
              <span className="text-muted-foreground">→ {targetOf(r, t)}</span>
              {r.pvc_phase && r.pvc_phase !== "Bound" && (
                <span className="text-amber-600">{t(`status_${r.pvc_phase}`)}</span>
              )}
              {!r.binding && (
                <Link className="underline" href={`/admin/storages/${r.storage_id}`}>{t("addBindingLink")}</Link>
              )}
            </li>
          ))}
        </ul>
      )}
      {blocked && data && <p className="mt-2 text-destructive">{t("deployBlocked")}</p>}
    </div>
  );
}
```

- [ ] **Step 2: Deploy dialog.**
  - In the body: replace `pvc_name`/`pvc_mount_path` with `storages: recipe.storages ?? [],` (P/D pool lists travel inside `pd_config` already).
  - Add state `const [storageBlocked, setStorageBlocked] = useState(false);` and render, after the resolved-placement block,

```tsx
          {recipe && (
            <StorageResolutionPreview
              storageIds={recipeStorageIds(recipe)}
              clusterId={clusterId || null}
              namespace={namespace.trim() || "default"}
              onBlockedChange={setStorageBlocked}
            />
          )}
```

  - Add `storageBlocked` to the Deploy button's `disabled` condition.
  - In `onSuccess`, show each returned warning: `(res.warnings ?? []).forEach((w) => { const [code, claim] = w.split(":"); toast.warning(ts(\`warn_${code}\`, { claim })); });` with `const ts = useTranslations("storages");` (adapt to how the mutation result is typed in `useCreateModelDeployment`).
  - Leave the cluster `<option>`s unchanged (no greying). The spec was amended: the per-storage ✓/✗ preview for the chosen cluster + namespace is the signal, and Deploy stays disabled while any row is ✗.

- [ ] **Step 3: Deployment detail.** In `deployments/[id]/page.tsx`:
  - P/D pool card: replace the `pdStorage` field with
    `<Field label={t("pdStorage")} mono>{(eff.storages ?? []).length ? (eff.storages ?? []).map((s) => s.mount_path).join(", ") : "-"}</Field>`.
  - Add a "Mounted storage" card for every deployment (near the spec/config cards), listing `dep.storages` as `volume_name · mount_path · ro/rw → PVC claim (ns) | NFS server:path`, with a `Badge variant="destructive"` reading `ts("stale")` when `dep.storage_stale?.length`, and each storage linking to `/admin/storages/{storage_id}`. Use `const ts = useTranslations("storages");`.

```tsx
function MountedStorages({ dep }: { dep: ModelDeployment }) {
  const ts = useTranslations("storages");
  if (!dep.storages?.length) return null;
  return (
    <Card data-testid="deployment-storages">
      <CardHeader className="flex flex-row items-center gap-2 space-y-0">
        <CardTitle className="text-base">{ts("volumes")}</CardTitle>
        {(dep.storage_stale?.length ?? 0) > 0 && <Badge variant="destructive">{ts("stale")}</Badge>}
      </CardHeader>
      <CardContent className="space-y-1 font-mono text-xs">
        {dep.storages.map((s) => (
          <div key={s.storage_id} className="flex flex-wrap gap-2">
            <Link className="underline" href={`/admin/storages/${s.storage_id}`}>{s.volume_name}</Link>
            <span>{s.mount_path}</span>
            <span className="text-muted-foreground">{s.read_only ? "ro" : "rw"}</span>
            <span className="text-muted-foreground">
              → {s.resolved.type === "pvc" ? `PVC ${s.resolved.claim_name} (${s.resolved.namespace})` : `NFS ${s.resolved.nfs_server}:${s.resolved.nfs_path}`}
            </span>
            {dep.storage_stale?.includes(s.storage_id) && <span className="text-destructive">●</span>}
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
```

  Render `<MountedStorages dep={dep} />` in the detail layout (import `Card*`, `Badge`, `Link` if missing).

- [ ] **Step 4: Check**

`npx tsc --noEmit -p .` (only `cluster-settings-tab.tsx` errors left), `npx eslint src/components/storage-resolution-preview.tsx src/components/deploy-from-recipe-dialog.tsx "src/app/(app)/admin/deployments/[id]/page.tsx"`.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/storage-resolution-preview.tsx frontend/src/components/deploy-from-recipe-dialog.tsx "frontend/src/app/(app)/admin/deployments/[id]/page.tsx"
git commit -m "feat(storage): deploy dialog previews storage resolution; detail shows mounted storages + stale"
```

---

### Task 16: Cluster settings bench storage + full UI verification

**Files:**
- Modify: `frontend/src/components/cluster-settings-tab.tsx`, `frontend/messages/ko.json`, `frontend/messages/en.json` (`settings.clusters`)
- Test: Playwright script in the session scratchpad (not committed)

**Interfaces:**
- Consumes: Task 12 `useStorages`; cluster type with `bench_storage_id` / `bench_mount_path`.

- [ ] **Step 1: Cluster form.**
  - Form state: replace `default_nfs_server/path/mount_path` with `bench_storage_id: string` and `bench_mount_path: string` (defaults `""`, filled from `c.bench_storage_id ?? ""`, `c.bench_mount_path ?? ""`).
  - Validation (replace the NFS all-three check):

```ts
    const benchStorage = form.bench_storage_id;
    const benchMount = form.bench_mount_path.trim();
    if (!!benchStorage !== !!benchMount) {
      toast.error(t("benchStorageBoth"));
      return;
    }
```

  - Create body: `bench_storage_id: benchStorage || null, bench_mount_path: benchMount || null`; update body: `bench_storage_id: benchStorage, bench_mount_path: benchMount` (empty string clears, matching the API).
  - Replace the three NFS inputs (around line 412) with:

```tsx
            <div className="space-y-1 sm:col-span-2">
              <Label htmlFor="cluster-bench-storage">{t("benchStorageLabel")}</Label>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                <select
                  id="cluster-bench-storage"
                  className="h-9 rounded-md border border-input bg-transparent px-3 font-mono text-sm"
                  value={form.bench_storage_id}
                  onChange={(e) => setForm({ ...form, bench_storage_id: e.target.value })}
                >
                  <option value="">{t("benchStorageNone")}</option>
                  {(storages ?? []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
                <Input
                  id="cluster-bench-mount"
                  className="font-mono"
                  placeholder="/models"
                  value={form.bench_mount_path}
                  onChange={(e) => setForm({ ...form, bench_mount_path: e.target.value })}
                />
              </div>
              <p className="text-xs text-muted-foreground">{t("benchStorageHint")}</p>
            </div>
```

  with `const { data: storages } = useStorages();`.
  - Messages under `settings.clusters`: remove `defaultNfsLabel`, `nfsServerPlaceholder`, the NFS path/mount placeholders and `nfsFieldsRequired` (grep the component for every `t("…nfs…")` key first); add
    - ko: `"benchStorageLabel": "벤치마크 기본 스토리지"`, `"benchStorageNone": "없음"`, `"benchStorageHint": "모델 이름으로 돌리는 벤치마크에 읽기 전용으로 마운트됩니다. 잡의 네임스페이스 기준으로 바인딩을 찾습니다."`, `"benchStorageBoth": "스토리지와 마운트 경로를 함께 지정하세요."`
    - en: `"benchStorageLabel": "Benchmark default storage"`, `"benchStorageNone": "None"`, `"benchStorageHint": "Mounted read-only into benchmarks that target a model name, resolved for the job's namespace."`, `"benchStorageBoth": "Set the storage and the mount path together."`

- [ ] **Step 2: Static checks**

```bash
cd frontend
npx tsc --noEmit -p .
npx eslint src/components/cluster-settings-tab.tsx src/components/storage-*.tsx "src/app/(app)/admin/storages" src/components/pool-config-cards.tsx src/components/serving-recipe-form.tsx src/components/deploy-from-recipe-dialog.tsx "src/app/(app)/admin/deployments/[id]/page.tsx" src/lib/pd-serving.ts src/lib/recipe-drafts.ts src/hooks/use-api.ts
npm run build
python3 -c "import json; [json.load(open(f'messages/{l}.json')) for l in ('ko','en')]"
```

Expected: tsc clean, eslint clean on changed files, build succeeds, both message files parse and have the same `storages` keys (`python3 -c "import json;a=json.load(open('messages/ko.json'))['storages'];b=json.load(open('messages/en.json'))['storages'];print(set(a)^set(b))"` → `set()`).

- [ ] **Step 3: Playwright on mocked API**

Start `npx next dev -p 3100` (background). Write a Playwright script in the session scratchpad that sets a dummy `litellm_session` cookie, removes the `nextjs-portal` overlay before clicking, and mocks `/api/proxy/**` (follow the shape of earlier sessions' scripts: `/api/me` super user, sidebar endpoints with empty data). Mock:
- `GET /api/admin/storages` → two storages (`llama-weights` with 2 bindings and `bound_on: [{cluster_id: null, cluster_name: "default", namespace: null}, {cluster_id: null, cluster_name: "default", namespace: "serving-prod"}]`, `hf-cache` with 0 and `bound_on: []`)
- `GET /api/admin/storages/<id>` → detail with one PVC binding (`serving-prod`) and one NFS cluster-wide binding; usage with one recipe and two deployments (`llama` `stale: true`, `qwen` `stale: false`)
- `GET /api/admin/storages/<id>/bindings/status` → `{status: {<pvc>: "Bound", <nfs>: null}}`
- `GET /api/admin/k8s-clusters/default/pvcs?namespace=serving-dev` → `{pvcs: [{name: "dev-pvc", phase: "Bound", capacity: "1Ti", …}], error: null}`; any other namespace → `{pvcs: [], error: "forbidden"}`
- `POST /api/admin/storages/resolve` → first storage bound, second `binding: null`
- recipes/deployments/clusters list endpoints the pages need

Verify, with a screenshot each:
1. `/admin/storages` lists both rows with counts; `llama-weights` shows the chips `default/*` and `default/serving-prod`, `hf-cache` shows "바인딩 없음"; the kind filter hides `hf-cache` for "모델 가중치".
2. Detail: bindings table shows `serving-prod`/`*`, `Bound` and `—`; the usage section shows the "바인딩이 바뀜 · 재배포 필요" badge after `llama` only; Add binding → type PVC, namespace `serving-dev` → the claim select lists `dev-pvc · Bound · 1Ti`; namespace `other` → the free-text input and the "PVC 목록을 가져오지 못했습니다" note appear; Save posts `{type: "pvc", namespace: "serving-dev", claim_name: "dev-pvc", cluster_id: null, …}`.
3. Recipe form: Storage section → add row, pick `llama-weights` (its bound-on chips appear under the row), mount `/models`, model path `/models/llama` shows "llama-weights 안의 경로입니다."; change model path to `/data/x` shows the warning; a second row at `/models/cache` → save → the 422 message is shown.
4. Deploy dialog from a recipe with both storages: preview shows ✓ for one, ✗ + "바인딩 추가하기" for the other, Deploy is disabled; mock resolve with both bound → Deploy enabled; POST body has `storages` and no `pvc_name`.
5. Deployment detail with `storage_stale: [<id>]` shows the "바인딩이 바뀜 · 재배포 필요" badge.
6. Settings → clusters: the bench storage select + mount path; selecting a storage with no mount path → toast `benchStorageBoth`.

Stop the dev server afterwards.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/cluster-settings-tab.tsx frontend/messages
git commit -m "feat(storage): cluster settings choose a benchmark default storage"
```

---

### Task 17: Live check on the local stack + docs

**Files:**
- Modify: `docs/superpowers/specs/2026-10-19-cluster-storage-design.md` (append "Live verification")

- [ ] **Step 1:** Rebuild the local backend/frontend images from this worktree only if the user agrees (the running stack serves another branch). Otherwise run the worktree backend on a spare port against a **copy** of the portal DB (as in Task 11) and the real proxy/K8s config, and exercise through `curl`:
  - create storage `llama-weights`, PVC binding on the portal default cluster for an existing namespace/claim (`kubectl get pvc -A` on the configured kubeconfig), `GET …/bindings/status` → `Bound`;
  - `POST /api/admin/storages/resolve` for that namespace → binding; another namespace → `null`.
  - If a local K8s (minikube `portal-test`) is up, deploy a mock-vllm recipe that mounts the storage and check `kubectl get deploy -o jsonpath='{.spec.template.spec.volumes}'`; then delete the deployment.
- [ ] **Step 2:** Append the commands and observed results to the spec under `## Live verification (YYYY-MM-DD)`; clean up every resource created.
- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-10-19-cluster-storage-design.md
git commit -m "docs(storage): live verification on the local stack"
```
