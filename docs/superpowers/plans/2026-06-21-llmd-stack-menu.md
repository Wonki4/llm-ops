# llm-d Stack Menu Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an admin menu that deploys an llm-d precise-prefix-cache-aware serving stack for a selected model by generating an ArgoCD `Application`, with full create/edit-scale/delete lifecycle and live status.

**Architecture:** The portal persists a `custom_llmd_stack` row, renders per-model Helm values, and applies an `argoproj.io/v1alpha1` `Application` CR to the target cluster's ArgoCD namespace via a new generic CustomObjects path on `K8sClient`. ArgoCD owns sync/drift; the portal reads the Application's status live on each query. Cluster targeting reuses the existing `cluster_id` / `k8s_for_cluster` machinery.

**Tech Stack:** FastAPI + SQLAlchemy (async) + Alembic, `kubernetes_asyncio`, Next.js + React Query + next-intl, shadcn/ui.

## Global Constraints

- Air-gap: chart repo and image registry default to **internal** values from settings; never reference external registries at runtime. HF token is referenced by **Secret name** only.
- Super-user only: every endpoint depends on `require_super_user` (see `app/api/k8s_clusters.py`).
- Status is **never** persisted — always read live from the Application CR.
- Backend tests run with `uv run pytest` from `backend/`. Lint with `uv run ruff check`.
- Prerequisites assumed present on the cluster (NOT portal-managed): ArgoCD, Gateway API + `gateway-api-inference-extension` CRDs, the internal llm-d Helm chart, the HF token Secret.
- i18n: every user-facing string has a key in BOTH `frontend/messages/en.json` and `frontend/messages/ko.json`, with identical key sets.

---

### Task 1: DB table + ORM model

**Files:**
- Create: `backend/migrations/versions/025_llmd_stack.py`
- Create: `backend/app/db/models/custom_llmd_stack.py`
- Modify: `backend/app/db/models/__init__.py`
- Test: `backend/tests/test_llmd.py`

**Interfaces:**
- Produces: ORM model `CustomLlmdStack` with columns `id, name, model_ref, served_model_name, cluster_id, namespace, argo_app_name, replicas, gpu_count, gpu_resource_key, values_snapshot, created_by, updated_by, created_at, updated_at`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_llmd.py
from app.db.models.custom_llmd_stack import CustomLlmdStack


def test_model_has_expected_columns():
    cols = set(CustomLlmdStack.__table__.columns.keys())
    assert {
        "id", "name", "model_ref", "served_model_name", "cluster_id",
        "namespace", "argo_app_name", "replicas", "gpu_count",
        "gpu_resource_key", "values_snapshot", "created_by", "updated_by",
        "created_at", "updated_at",
    } <= cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_llmd.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.db.models.custom_llmd_stack'`

- [ ] **Step 3: Create the ORM model**

```python
# backend/app/db/models/custom_llmd_stack.py
"""An llm-d serving stack deployed for a model via an ArgoCD Application.

The portal stores the desired config; ArgoCD owns the running workloads. Sync/
health status is read live from the Application CR, never persisted here.
"""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import CustomBase


class CustomLlmdStack(CustomBase):
    __tablename__ = "custom_llmd_stack"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    model_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    served_model_name: Mapped[str] = mapped_column(String(256), nullable=False)
    cluster_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("custom_k8s_cluster.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    namespace: Mapped[str] = mapped_column(String(128), nullable=False, default="default", server_default="default")
    argo_app_name: Mapped[str] = mapped_column(String(253), nullable=False)
    replicas: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    gpu_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    gpu_resource_key: Mapped[str] = mapped_column(
        String(64), nullable=False, default="nvidia.com/gpu", server_default="nvidia.com/gpu"
    )
    values_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
```

Add the missing import at the top: change the sqlalchemy import line to
`from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func`.
(`Text` is unused — drop it; final import: `from sqlalchemy import DateTime, ForeignKey, Integer, String, func`.)

- [ ] **Step 4: Register the model**

```python
# backend/app/db/models/__init__.py  — add the import and __all__ entry
from app.db.models.custom_llmd_stack import CustomLlmdStack
```
Add `"CustomLlmdStack",` to the `__all__` list.

- [ ] **Step 5: Create the migration**

```python
# backend/migrations/versions/025_llmd_stack.py
"""llm-d serving stacks (ArgoCD-managed).

Revision ID: 025_llmd_stack
Revises: 024_cluster_default_nfs
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "025_llmd_stack"
down_revision = "024_cluster_default_nfs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_llmd_stack",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("model_ref", sa.String(512), nullable=False),
        sa.Column("served_model_name", sa.String(256), nullable=False),
        sa.Column("cluster_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("namespace", sa.String(128), nullable=False, server_default="default"),
        sa.Column("argo_app_name", sa.String(253), nullable=False),
        sa.Column("replicas", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("gpu_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("gpu_resource_key", sa.String(64), nullable=False, server_default="nvidia.com/gpu"),
        sa.Column("values_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("updated_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_custom_llmd_stack_name", "custom_llmd_stack", ["name"], unique=True)
    op.create_index("ix_custom_llmd_stack_cluster_id", "custom_llmd_stack", ["cluster_id"])
    op.create_foreign_key(
        "fk_custom_llmd_stack_cluster_id", "custom_llmd_stack", "custom_k8s_cluster",
        ["cluster_id"], ["id"], ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_custom_llmd_stack_cluster_id", "custom_llmd_stack", type_="foreignkey")
    op.drop_index("ix_custom_llmd_stack_cluster_id", table_name="custom_llmd_stack")
    op.drop_index("ix_custom_llmd_stack_name", table_name="custom_llmd_stack")
    op.drop_table("custom_llmd_stack")
```

- [ ] **Step 6: Run test + alembic head check**

Run: `cd backend && uv run pytest tests/test_llmd.py -q && uv run alembic heads`
Expected: test PASS; heads shows `025_llmd_stack (head)` only.

- [ ] **Step 7: Commit**

```bash
git add backend/migrations/versions/025_llmd_stack.py backend/app/db/models/custom_llmd_stack.py backend/app/db/models/__init__.py backend/tests/test_llmd.py
git commit -m "llm-d: custom_llmd_stack table + model"
```

---

### Task 2: Config settings for llm-d

**Files:**
- Modify: `backend/app/config.py` (the `Settings` class, after the `vllm_bench_image` block)
- Test: `backend/tests/test_llmd.py`

**Interfaces:**
- Produces: `settings.llmd_chart_repo`, `settings.llmd_chart_name`, `settings.llmd_chart_version`, `settings.llmd_image_registry`, `settings.argocd_namespace`, `settings.llmd_hf_secret_name`.

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/test_llmd.py
from app.config import settings


def test_llmd_settings_have_internal_defaults():
    assert settings.argocd_namespace == "argocd"
    assert settings.llmd_hf_secret_name  # non-empty
    # air-gap: defaults must not point at public registries
    assert "registry.k8s.io" not in settings.llmd_chart_repo
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_llmd.py::test_llmd_settings_have_internal_defaults -q`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'argocd_namespace'`

- [ ] **Step 3: Add the settings**

```python
# backend/app/config.py — inside class Settings, after the vllm_bench_image lines
    # llm-d stack (ArgoCD-deployed). Air-gap: internal chart repo + image registry.
    llmd_chart_repo: str = "oci://internal-registry.local/charts"
    llmd_chart_name: str = "llm-d-stack"
    llmd_chart_version: str = "0.7.0"
    llmd_image_registry: str = "internal-registry.local"
    argocd_namespace: str = "argocd"
    llmd_hf_secret_name: str = "llm-d-hf-token"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_llmd.py::test_llmd_settings_have_internal_defaults -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/config.py backend/tests/test_llmd.py
git commit -m "llm-d: internal-registry config defaults"
```

---

### Task 3: Pure builders — values + ArgoCD Application

**Files:**
- Create: `backend/app/services/llmd_manifests.py`
- Test: `backend/tests/test_llmd.py`

**Interfaces:**
- Consumes: `CustomLlmdStack` (Task 1).
- Produces:
  - `argo_app_name_for(name: str) -> str`
  - `build_llmd_values(stack, *, image_registry: str, hf_secret_name: str) -> dict`
  - `build_argo_application(stack, *, chart_repo: str, chart_name: str, chart_version: str, values: dict, argocd_namespace: str) -> dict`

- [ ] **Step 1: Write the failing tests**

```python
# append to backend/tests/test_llmd.py
import types
import uuid

from app.services.llmd_manifests import (
    argo_app_name_for,
    build_argo_application,
    build_llmd_values,
)


def _stack(**kw):
    base = dict(
        id=uuid.uuid4(), name="my-stack", model_ref="facebook/opt-125m",
        served_model_name="opt-125m", namespace="llmd", replicas=2,
        gpu_count=1, gpu_resource_key="nvidia.com/gpu",
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_argo_app_name_is_sanitised():
    assert argo_app_name_for("My_Stack.1") == "llmd-my-stack-1"


def test_build_values_uses_internal_registry_and_secret():
    v = build_llmd_values(_stack(), image_registry="reg.local", hf_secret_name="hf")
    assert v["model"]["id"] == "facebook/opt-125m"
    assert v["model"]["servedName"] == "opt-125m"
    assert v["replicas"] == 2
    assert v["resources"]["gpu"]["count"] == 1
    assert v["resources"]["gpu"]["resourceKey"] == "nvidia.com/gpu"
    assert v["image"]["registry"] == "reg.local"
    assert v["hfTokenSecret"] == "hf"


def test_build_argo_application_shape():
    stack = _stack()
    values = {"replicas": 2}
    app = build_argo_application(
        stack, chart_repo="oci://reg.local/charts", chart_name="llm-d-stack",
        chart_version="0.7.0", values=values, argocd_namespace="argocd",
    )
    assert app["apiVersion"] == "argoproj.io/v1alpha1"
    assert app["kind"] == "Application"
    assert app["metadata"]["namespace"] == "argocd"
    src = app["spec"]["source"]
    assert src["repoURL"] == "oci://reg.local/charts"
    assert src["chart"] == "llm-d-stack"
    assert src["targetRevision"] == "0.7.0"
    assert src["helm"]["valuesObject"] == values
    assert app["spec"]["destination"]["namespace"] == "llmd"
    assert app["spec"]["syncPolicy"]["automated"] == {"prune": True, "selfHeal": True}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_llmd.py -q -k "argo or values"`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.llmd_manifests'`

- [ ] **Step 3: Implement the builders**

```python
# backend/app/services/llmd_manifests.py
"""Pure builders for an llm-d stack's ArgoCD Application + Helm values.

The portal renders per-model values and wraps them in an argoproj.io Application
that points at the internal llm-d Helm chart. ArgoCD reconciles it. The values
schema here is the contract with that internal chart.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.db.models.custom_llmd_stack import CustomLlmdStack


def argo_app_name_for(name: str) -> str:
    """Deterministic, DNS-safe Application name: `llmd-<sanitised name>`."""
    safe = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    return f"llmd-{safe}"


def build_llmd_values(stack: CustomLlmdStack, *, image_registry: str, hf_secret_name: str) -> dict:
    """Per-model Helm values for the internal llm-d chart."""
    return {
        "model": {"id": stack.model_ref, "servedName": stack.served_model_name},
        "replicas": stack.replicas,
        "resources": {"gpu": {"count": stack.gpu_count, "resourceKey": stack.gpu_resource_key}},
        "image": {"registry": image_registry},
        "hfTokenSecret": hf_secret_name,
        "namespace": stack.namespace,
    }


def build_argo_application(
    stack: CustomLlmdStack,
    *,
    chart_repo: str,
    chart_name: str,
    chart_version: str,
    values: dict,
    argocd_namespace: str,
) -> dict:
    """An argoproj.io/v1alpha1 Application that deploys the llm-d stack."""
    return {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Application",
        "metadata": {"name": stack.argo_app_name, "namespace": argocd_namespace},
        "spec": {
            "project": "default",
            "source": {
                "repoURL": chart_repo,
                "chart": chart_name,
                "targetRevision": chart_version,
                "helm": {"valuesObject": values},
            },
            "destination": {"server": "https://kubernetes.default.svc", "namespace": stack.namespace},
            "syncPolicy": {
                "automated": {"prune": True, "selfHeal": True},
                "syncOptions": ["CreateNamespace=true"],
            },
        },
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_llmd.py -q && uv run ruff check app/services/llmd_manifests.py`
Expected: PASS; ruff "All checks passed!"

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/llmd_manifests.py backend/tests/test_llmd.py
git commit -m "llm-d: pure values + ArgoCD Application builders"
```

---

### Task 4: K8sClient — generic CustomObjects (Argo Application) support

**Files:**
- Modify: `backend/app/clients/k8s.py` (add methods on `K8sClient`, after `delete_job`)
- Test: `backend/tests/test_llmd.py`

**Interfaces:**
- Consumes: existing `K8sClient._api_client()`.
- Produces on `K8sClient`:
  - `async def apply_custom_object(self, group, version, namespace, plural, manifest) -> None` (upsert)
  - `async def get_custom_object(self, group, version, namespace, plural, name) -> dict | None` (None on 404)
  - `async def delete_custom_object(self, group, version, namespace, plural, name) -> None` (ignore 404)

- [ ] **Step 1: Write the failing test (mock CustomObjectsApi)**

```python
# append to backend/tests/test_llmd.py
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_get_custom_object_returns_none_on_404():
    from kubernetes_asyncio.client.exceptions import ApiException
    from app.clients.k8s import K8sClient

    api = MagicMock()
    api.get_namespaced_custom_object = AsyncMock(side_effect=ApiException(status=404))
    fake_client = MagicMock()
    fake_client.close = AsyncMock()
    with patch.object(K8sClient, "_api_client", AsyncMock(return_value=fake_client)), \
         patch("app.clients.k8s.client.CustomObjectsApi", return_value=api):
        out = await K8sClient().get_custom_object(
            "argoproj.io", "v1alpha1", "argocd", "applications", "missing"
        )
    assert out is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_llmd.py::test_get_custom_object_returns_none_on_404 -q`
Expected: FAIL with `AttributeError: 'K8sClient' object has no attribute 'get_custom_object'`

- [ ] **Step 3: Implement the methods**

```python
# backend/app/clients/k8s.py — add these methods to K8sClient (after delete_job)

    async def apply_custom_object(self, group: str, version: str, namespace: str, plural: str, manifest: dict) -> None:
        """Create or patch (upsert) a namespaced custom resource."""
        api_client = await self._api_client()
        try:
            api = client.CustomObjectsApi(api_client)
            name = manifest["metadata"]["name"]
            try:
                await api.get_namespaced_custom_object(group, version, namespace, plural, name)
                exists = True
            except ApiException as e:
                if e.status == 404:
                    exists = False
                else:
                    raise
            if exists:
                await api.patch_namespaced_custom_object(group, version, namespace, plural, name, manifest)
            else:
                await api.create_namespaced_custom_object(group, version, namespace, plural, manifest)
        finally:
            await api_client.close()

    async def get_custom_object(self, group: str, version: str, namespace: str, plural: str, name: str) -> dict | None:
        """Read a namespaced custom resource; None if it does not exist."""
        api_client = await self._api_client()
        try:
            api = client.CustomObjectsApi(api_client)
            try:
                return await api.get_namespaced_custom_object(group, version, namespace, plural, name)
            except ApiException as e:
                if e.status == 404:
                    return None
                raise
        finally:
            await api_client.close()

    async def delete_custom_object(self, group: str, version: str, namespace: str, plural: str, name: str) -> None:
        """Delete a namespaced custom resource; ignore if already gone."""
        api_client = await self._api_client()
        try:
            api = client.CustomObjectsApi(api_client)
            try:
                await api.delete_namespaced_custom_object(group, version, namespace, plural, name)
            except ApiException as e:
                if e.status != 404:
                    raise
        finally:
            await api_client.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_llmd.py::test_get_custom_object_returns_none_on_404 -q`
Expected: PASS

(If `pytest.mark.asyncio` is unknown, confirm `asyncio_mode = "auto"` is set under `[tool.pytest.ini_options]` in `backend/pyproject.toml`; the existing suite already uses async tests.)

- [ ] **Step 5: Commit**

```bash
git add backend/app/clients/k8s.py backend/tests/test_llmd.py
git commit -m "k8s: generic CustomObjects upsert/get/delete"
```

---

### Task 5: API — `/api/admin/llmd-stacks` CRUD + live status

**Files:**
- Create: `backend/app/api/llmd.py`
- Modify: `backend/app/main.py` (import + `include_router`)
- Test: `backend/tests/test_llmd.py`

**Interfaces:**
- Consumes: `build_llmd_values`, `build_argo_application`, `argo_app_name_for` (Task 3); `K8sClient.apply/get/delete_custom_object` (Task 4); `k8s_for_cluster` (`app/services/clusters.py`); `settings` (Task 2).
- Produces: router at prefix `/api/admin/llmd-stacks`; module constants `ARGO_GROUP="argoproj.io"`, `ARGO_VERSION="v1alpha1"`, `ARGO_PLURAL="applications"`; helper `_argo_status(obj) -> dict`.

- [ ] **Step 1: Write the failing test (pure status helper)**

```python
# append to backend/tests/test_llmd.py
from app.api.llmd import _argo_status


def test_argo_status_extracts_sync_and_health():
    obj = {"status": {"sync": {"status": "Synced"}, "health": {"status": "Healthy"}}}
    assert _argo_status(obj) == {"sync_status": "Synced", "health_status": "Healthy", "status_message": None}


def test_argo_status_unknown_when_missing():
    assert _argo_status(None) == {"sync_status": "Unknown", "health_status": "Unknown", "status_message": None}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_llmd.py -q -k argo_status`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.api.llmd'`

- [ ] **Step 3: Implement the API**

```python
# backend/app/api/llmd.py
"""Admin endpoints for llm-d serving stacks (ArgoCD-managed).

The portal renders an argoproj.io Application per stack and applies it to the
target cluster's ArgoCD namespace. Sync/health status is read live from the
Application CR — never persisted.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_super_user
from app.config import settings
from app.db.models.custom_llmd_stack import CustomLlmdStack
from app.db.models.custom_user import CustomUser
from app.db.session import get_db
from app.services.clusters import k8s_for_cluster
from app.services.llmd_manifests import argo_app_name_for, build_argo_application, build_llmd_values

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/llmd-stacks", tags=["llmd-stacks"])

ARGO_GROUP = "argoproj.io"
ARGO_VERSION = "v1alpha1"
ARGO_PLURAL = "applications"


class CreateLlmdStackRequest(BaseModel):
    name: str
    model_ref: str
    served_model_name: str
    cluster_id: str | None = None
    namespace: str = "default"
    replicas: int = 1
    gpu_count: int = 1
    gpu_resource_key: str = "nvidia.com/gpu"


class UpdateLlmdStackRequest(BaseModel):
    served_model_name: str | None = None
    namespace: str | None = None
    replicas: int | None = None
    gpu_count: int | None = None
    gpu_resource_key: str | None = None


def _argo_status(obj: dict | None) -> dict:
    """Extract sync/health from an Application CR (Unknown when absent)."""
    st = (obj or {}).get("status", {}) if obj else {}
    return {
        "sync_status": (st.get("sync") or {}).get("status", "Unknown") if obj else "Unknown",
        "health_status": (st.get("health") or {}).get("status", "Unknown") if obj else "Unknown",
        "status_message": (st.get("health") or {}).get("message") if obj else None,
    }


def _values_for(stack: CustomLlmdStack) -> dict:
    return build_llmd_values(
        stack, image_registry=settings.llmd_image_registry, hf_secret_name=settings.llmd_hf_secret_name
    )


def _application_for(stack: CustomLlmdStack) -> dict:
    return build_argo_application(
        stack,
        chart_repo=settings.llmd_chart_repo,
        chart_name=settings.llmd_chart_name,
        chart_version=settings.llmd_chart_version,
        values=stack.values_snapshot,
        argocd_namespace=settings.argocd_namespace,
    )


def _cluster_uuid(cluster_id: str | None) -> uuid.UUID | None:
    return uuid.UUID(cluster_id) if cluster_id else None


async def _live_status(db: AsyncSession, stack: CustomLlmdStack) -> dict:
    try:
        k8s = await k8s_for_cluster(db, stack.cluster_id)
        obj = await k8s.get_custom_object(
            ARGO_GROUP, ARGO_VERSION, settings.argocd_namespace, ARGO_PLURAL, stack.argo_app_name
        )
        return _argo_status(obj)
    except Exception as e:  # noqa: BLE001 — status is best-effort
        logger.info("llm-d status read failed for %s: %s", stack.name, e)
        return _argo_status(None)


def _serialize(stack: CustomLlmdStack, status_fields: dict) -> dict:
    return {
        "id": str(stack.id),
        "name": stack.name,
        "model_ref": stack.model_ref,
        "served_model_name": stack.served_model_name,
        "cluster_id": str(stack.cluster_id) if stack.cluster_id else None,
        "namespace": stack.namespace,
        "argo_app_name": stack.argo_app_name,
        "replicas": stack.replicas,
        "gpu_count": stack.gpu_count,
        "gpu_resource_key": stack.gpu_resource_key,
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


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_stack(
    body: CreateLlmdStackRequest,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    existing = await db.execute(select(CustomLlmdStack).where(CustomLlmdStack.name == body.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Stack '{body.name}' already exists")

    stack = CustomLlmdStack(
        id=uuid.uuid4(),
        name=body.name,
        model_ref=body.model_ref,
        served_model_name=body.served_model_name,
        cluster_id=_cluster_uuid(body.cluster_id),
        namespace=body.namespace,
        argo_app_name=argo_app_name_for(body.name),
        replicas=body.replicas,
        gpu_count=body.gpu_count,
        gpu_resource_key=body.gpu_resource_key,
        values_snapshot={},
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    stack.values_snapshot = _values_for(stack)
    db.add(stack)
    await db.flush()

    k8s = await k8s_for_cluster(db, stack.cluster_id)
    await k8s.apply_custom_object(
        ARGO_GROUP, ARGO_VERSION, settings.argocd_namespace, ARGO_PLURAL, _application_for(stack)
    )
    await db.commit()
    await db.refresh(stack)
    return _serialize(stack, await _live_status(db, stack))


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

    if body.served_model_name is not None:
        stack.served_model_name = body.served_model_name
    if body.namespace is not None:
        stack.namespace = body.namespace
    if body.replicas is not None:
        stack.replicas = body.replicas
    if body.gpu_count is not None:
        stack.gpu_count = body.gpu_count
    if body.gpu_resource_key is not None:
        stack.gpu_resource_key = body.gpu_resource_key
    stack.values_snapshot = _values_for(stack)
    stack.updated_by = user.user_id
    await db.flush()

    k8s = await k8s_for_cluster(db, stack.cluster_id)
    await k8s.apply_custom_object(
        ARGO_GROUP, ARGO_VERSION, settings.argocd_namespace, ARGO_PLURAL, _application_for(stack)
    )
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
    k8s = await k8s_for_cluster(db, stack.cluster_id)
    await k8s.delete_custom_object(
        ARGO_GROUP, ARGO_VERSION, settings.argocd_namespace, ARGO_PLURAL, stack.argo_app_name
    )
    await db.delete(stack)
    await db.commit()
    return {"ok": True}
```

- [ ] **Step 4: Register the router**

```python
# backend/app/main.py — add to the `from app.api import (...)` block
    llmd,
# and after `app.include_router(k8s_clusters.router)`
app.include_router(llmd.router)
```

- [ ] **Step 5: Run tests + import smoke + ruff**

Run: `cd backend && uv run pytest tests/test_llmd.py -q && uv run python -c "import app.main" && uv run ruff check app/api/llmd.py`
Expected: PASS; import OK; ruff clean.

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/llmd.py backend/app/main.py backend/tests/test_llmd.py
git commit -m "llm-d: /api/admin/llmd-stacks CRUD with live Argo status"
```

---

### Task 6: Frontend — types + React Query hooks

**Files:**
- Modify: `frontend/src/types/index.ts` (append)
- Modify: `frontend/src/hooks/use-api.ts` (append, follow the `useK8sClusters` block ~line 954+)
- Test: typecheck only (`npx tsc --noEmit`)

**Interfaces:**
- Produces: type `LlmdStackSummary`; `CreateLlmdStackBody`, `UpdateLlmdStackBody`; hooks `useLlmdStacks`, `useCreateLlmdStack`, `useUpdateLlmdStack`, `useDeleteLlmdStack`.

- [ ] **Step 1: Add the type**

```typescript
// frontend/src/types/index.ts — append
export interface LlmdStackSummary {
  id: string;
  name: string;
  model_ref: string;
  served_model_name: string;
  cluster_id: string | null;
  namespace: string;
  argo_app_name: string;
  replicas: number;
  gpu_count: number;
  gpu_resource_key: string;
  sync_status: string;
  health_status: string;
  status_message: string | null;
  created_by: string | null;
  created_at: string | null;
  updated_at: string | null;
}
```

- [ ] **Step 2: Add the hooks**

```typescript
// frontend/src/hooks/use-api.ts — append (mirrors the K8s cluster hooks)
import type { LlmdStackSummary } from "@/types"; // add to the existing type import if separate

export interface CreateLlmdStackBody {
  name: string;
  model_ref: string;
  served_model_name: string;
  cluster_id?: string | null;
  namespace?: string;
  replicas?: number;
  gpu_count?: number;
  gpu_resource_key?: string;
}

export type UpdateLlmdStackBody = Partial<Omit<CreateLlmdStackBody, "name" | "model_ref" | "cluster_id">>;

export function useLlmdStacks() {
  return useQuery({
    queryKey: ["llmd-stacks"],
    queryFn: () =>
      apiFetch<{ stacks: LlmdStackSummary[] }>("/api/admin/llmd-stacks").then((r) => r.stacks),
  });
}

export function useCreateLlmdStack() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateLlmdStackBody) =>
      apiFetch<LlmdStackSummary>("/api/admin/llmd-stacks", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["llmd-stacks"] }),
  });
}

export function useUpdateLlmdStack() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: UpdateLlmdStackBody }) =>
      apiFetch<LlmdStackSummary>(`/api/admin/llmd-stacks/${id}`, { method: "PUT", body: JSON.stringify(body) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["llmd-stacks"] }),
  });
}

export function useDeleteLlmdStack() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiFetch(`/api/admin/llmd-stacks/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["llmd-stacks"] }),
  });
}
```

Before writing, open `use-api.ts` and copy the EXACT `apiFetch` call signature and the `useQuery`/`useMutation` import style already in the file (the `useK8sClusters` block is the reference). Match it rather than the sketch above if they differ.

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: exit 0, no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/hooks/use-api.ts
git commit -m "llm-d: frontend types + hooks"
```

---

### Task 7: Frontend — admin page, nav entry, i18n

**Files:**
- Create: `frontend/src/app/(app)/admin/llmd/page.tsx`
- Modify: `frontend/src/components/app-sidebar.tsx` (add a nav item near the `adminDashboard` entry ~line 44)
- Modify: `frontend/messages/en.json`, `frontend/messages/ko.json` (add an `llmd` namespace)
- Test: typecheck + i18n symmetry

**Interfaces:**
- Consumes: hooks + `LlmdStackSummary` (Task 6); `useK8sClusters` for the cluster picker; `useTranslations("llmd")`.

- [ ] **Step 1: Add i18n keys (both locales, identical key sets)**

```json
// frontend/messages/en.json — add a top-level "llmd" object
"llmd": {
  "title": "llm-d stacks",
  "description": "Prefix-cache-aware serving stacks deployed via ArgoCD.",
  "addButton": "Add stack",
  "empty": "No llm-d stacks yet.",
  "name": "Name",
  "modelRef": "Model (HF id / path)",
  "servedName": "Served model name",
  "cluster": "Cluster",
  "clusterDefault": "Portal default",
  "namespace": "Namespace",
  "replicas": "Replicas",
  "gpuCount": "GPU count",
  "gpuResourceKey": "GPU resource key",
  "addTitle": "Add llm-d stack",
  "editTitle": "Edit llm-d stack",
  "save": "Save",
  "cancel": "Cancel",
  "delete": "Delete",
  "deleteConfirm": "Delete stack \"{name}\"? ArgoCD will prune its workloads.",
  "createSuccess": "Stack created",
  "updateSuccess": "Stack updated",
  "deleteSuccess": "Stack deleted",
  "saveFailed": "Save failed",
  "deleteFailed": "Delete failed",
  "nameModelRequired": "Name, model and served name are required",
  "syncLabel": "Sync",
  "healthLabel": "Health"
}
```

```json
// frontend/messages/ko.json — same keys, Korean values
"llmd": {
  "title": "llm-d 스택",
  "description": "ArgoCD로 배포되는 prefix-cache-aware 서빙 스택.",
  "addButton": "스택 추가",
  "empty": "아직 llm-d 스택이 없습니다.",
  "name": "이름",
  "modelRef": "모델 (HF id / 경로)",
  "servedName": "서빙 모델 이름",
  "cluster": "클러스터",
  "clusterDefault": "포털 기본",
  "namespace": "네임스페이스",
  "replicas": "레플리카",
  "gpuCount": "GPU 개수",
  "gpuResourceKey": "GPU 리소스 키",
  "addTitle": "llm-d 스택 추가",
  "editTitle": "llm-d 스택 수정",
  "save": "저장",
  "cancel": "취소",
  "delete": "삭제",
  "deleteConfirm": "스택 \"{name}\"을(를) 삭제할까요? ArgoCD가 워크로드를 정리합니다.",
  "createSuccess": "스택이 생성되었습니다",
  "updateSuccess": "스택이 수정되었습니다",
  "deleteSuccess": "스택이 삭제되었습니다",
  "saveFailed": "저장 실패",
  "deleteFailed": "삭제 실패",
  "nameModelRequired": "이름, 모델, 서빙 이름은 필수입니다",
  "syncLabel": "동기화",
  "healthLabel": "상태"
}
```

- [ ] **Step 2: Add the nav entry**

```typescript
// frontend/src/components/app-sidebar.tsx — add near the adminDashboard item (~line 44).
// Pick an icon already imported in the file (e.g. Boxes/Network/Server from lucide-react);
// import one if absent. Use the same shape as the existing items:
{ key: "adminLlmd", href: "/admin/llmd", icon: Network, roles: ["super_user"] },
```
Add the matching label key `adminLlmd` to wherever the sidebar reads item labels (search the file for how `adminDashboard` resolves its text — add the parallel key in the same messages namespace the sidebar uses).

- [ ] **Step 3: Create the page**

```tsx
// frontend/src/app/(app)/admin/llmd/page.tsx
"use client";

import { useState } from "react";
import { Loader2, Plus, Pencil, Trash2, Network } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import {
  useLlmdStacks, useCreateLlmdStack, useUpdateLlmdStack, useDeleteLlmdStack,
  useK8sClusters, type CreateLlmdStackBody,
} from "@/hooks/use-api";
import type { LlmdStackSummary } from "@/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";

type FormState = {
  name: string; model_ref: string; served_model_name: string;
  cluster_id: string; namespace: string; replicas: number;
  gpu_count: number; gpu_resource_key: string;
};
const EMPTY: FormState = {
  name: "", model_ref: "", served_model_name: "", cluster_id: "",
  namespace: "default", replicas: 1, gpu_count: 1, gpu_resource_key: "nvidia.com/gpu",
};

export default function LlmdPage() {
  const t = useTranslations("llmd");
  const { data: stacks, isLoading } = useLlmdStacks();
  const { data: clusters } = useK8sClusters();
  const createMut = useCreateLlmdStack();
  const updateMut = useUpdateLlmdStack();
  const deleteMut = useDeleteLlmdStack();

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<LlmdStackSummary | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY);

  const openCreate = () => { setEditing(null); setForm(EMPTY); setDialogOpen(true); };
  const openEdit = (s: LlmdStackSummary) => {
    setEditing(s);
    setForm({
      name: s.name, model_ref: s.model_ref, served_model_name: s.served_model_name,
      cluster_id: s.cluster_id ?? "", namespace: s.namespace, replicas: s.replicas,
      gpu_count: s.gpu_count, gpu_resource_key: s.gpu_resource_key,
    });
    setDialogOpen(true);
  };

  const handleSave = () => {
    if (!form.name.trim() || !form.model_ref.trim() || !form.served_model_name.trim()) {
      toast.error(t("nameModelRequired")); return;
    }
    if (editing) {
      updateMut.mutate(
        { id: editing.id, body: {
          served_model_name: form.served_model_name, namespace: form.namespace,
          replicas: form.replicas, gpu_count: form.gpu_count, gpu_resource_key: form.gpu_resource_key,
        } },
        { onSuccess: () => { toast.success(t("updateSuccess")); setDialogOpen(false); },
          onError: (e) => toast.error(e instanceof Error ? e.message : t("saveFailed")) },
      );
    } else {
      const body: CreateLlmdStackBody = {
        name: form.name, model_ref: form.model_ref, served_model_name: form.served_model_name,
        cluster_id: form.cluster_id || null, namespace: form.namespace,
        replicas: form.replicas, gpu_count: form.gpu_count, gpu_resource_key: form.gpu_resource_key,
      };
      createMut.mutate(body, {
        onSuccess: () => { toast.success(t("createSuccess")); setDialogOpen(false); },
        onError: (e) => toast.error(e instanceof Error ? e.message : t("saveFailed")),
      });
    }
  };

  const handleDelete = (s: LlmdStackSummary) => {
    if (!window.confirm(t("deleteConfirm", { name: s.name }))) return;
    deleteMut.mutate(s.id, {
      onSuccess: () => toast.success(t("deleteSuccess")),
      onError: (e) => toast.error(e instanceof Error ? e.message : t("deleteFailed")),
    });
  };

  const saving = createMut.isPending || updateMut.isPending;

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-4">
          <div>
            <CardTitle className="text-base flex items-center gap-2"><Network className="size-4" />{t("title")}</CardTitle>
            <CardDescription>{t("description")}</CardDescription>
          </div>
          <Button size="sm" onClick={openCreate}><Plus className="size-4" />{t("addButton")}</Button>
        </div>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="flex justify-center py-8"><Loader2 className="size-5 animate-spin text-muted-foreground" /></div>
        ) : stacks && stacks.length > 0 ? (
          <div className="space-y-2">
            {stacks.map((s) => (
              <div key={s.id} className="flex items-center justify-between gap-3 rounded-md border p-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium truncate">{s.name}</span>
                    <Badge variant="secondary">{t("syncLabel")}: {s.sync_status}</Badge>
                    <Badge variant={s.health_status === "Healthy" ? "default" : "secondary"}>
                      {t("healthLabel")}: {s.health_status}
                    </Badge>
                  </div>
                  <div className="text-xs text-muted-foreground mt-0.5 space-x-2 font-mono">
                    <span>{s.model_ref}</span><span>ns: {s.namespace}</span><span>x{s.replicas}</span>
                  </div>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <Button variant="ghost" size="icon-xs" onClick={() => openEdit(s)}><Pencil className="size-3.5" /></Button>
                  <Button variant="ghost" size="icon-xs" className="text-destructive hover:text-destructive" onClick={() => handleDelete(s)}>
                    <Trash2 className="size-3.5" />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">{t("empty")}</p>
        )}
      </CardContent>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader><DialogTitle>{editing ? t("editTitle") : t("addTitle")}</DialogTitle></DialogHeader>
          <div className="space-y-4 max-h-[60vh] overflow-y-auto pr-1">
            <div className="space-y-2">
              <Label htmlFor="llmd-name">{t("name")}</Label>
              <Input id="llmd-name" value={form.name} disabled={!!editing}
                onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2">
                <Label htmlFor="llmd-model">{t("modelRef")}</Label>
                <Input id="llmd-model" value={form.model_ref} disabled={!!editing}
                  onChange={(e) => setForm({ ...form, model_ref: e.target.value })} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="llmd-served">{t("servedName")}</Label>
                <Input id="llmd-served" value={form.served_model_name}
                  onChange={(e) => setForm({ ...form, served_model_name: e.target.value })} />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2">
                <Label htmlFor="llmd-cluster">{t("cluster")}</Label>
                <select id="llmd-cluster" disabled={!!editing} value={form.cluster_id}
                  onChange={(e) => setForm({ ...form, cluster_id: e.target.value })}
                  className="w-full h-9 rounded-md border border-input bg-transparent px-3 text-sm">
                  <option value="">{t("clusterDefault")}</option>
                  {(clusters ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="llmd-ns">{t("namespace")}</Label>
                <Input id="llmd-ns" value={form.namespace}
                  onChange={(e) => setForm({ ...form, namespace: e.target.value })} />
              </div>
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div className="space-y-2">
                <Label htmlFor="llmd-replicas">{t("replicas")}</Label>
                <Input id="llmd-replicas" type="number" min={1} value={form.replicas}
                  onChange={(e) => setForm({ ...form, replicas: Number(e.target.value) })} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="llmd-gpu">{t("gpuCount")}</Label>
                <Input id="llmd-gpu" type="number" min={0} value={form.gpu_count}
                  onChange={(e) => setForm({ ...form, gpu_count: Number(e.target.value) })} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="llmd-gpukey">{t("gpuResourceKey")}</Label>
                <Input id="llmd-gpukey" value={form.gpu_resource_key}
                  onChange={(e) => setForm({ ...form, gpu_resource_key: e.target.value })} />
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>{t("cancel")}</Button>
            <Button onClick={handleSave} disabled={saving}>
              {saving && <Loader2 className="size-4 animate-spin" />}{t("save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
```

- [ ] **Step 4: Typecheck + i18n symmetry**

Run:
```bash
cd frontend && npx tsc --noEmit && \
python3 -c "import json; e=json.load(open('messages/en.json'))['llmd']; k=json.load(open('messages/ko.json'))['llmd']; assert set(e)==set(k), set(e)^set(k); print('i18n llmd OK')"
```
Expected: exit 0; "i18n llmd OK".

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/\(app\)/admin/llmd/page.tsx frontend/src/components/app-sidebar.tsx frontend/messages/en.json frontend/messages/ko.json
git commit -m "llm-d: admin page, nav entry, i18n"
```

---

## Self-Review

**Spec coverage:**
- ArgoCD Application generation → Task 3 (`build_argo_application`) + Task 5 (apply).
- Internal Helm chart + inline values → Task 2 (config) + Task 3 (`build_llmd_values`, `helm.valuesObject`).
- `custom_llmd_stack` table → Task 1.
- CustomObjects on K8sClient → Task 4.
- Full CRUD + edit/scale → Task 5 (`PUT` re-renders values + patches Application).
- Live status (no persistence) → Task 5 (`_argo_status`, `_live_status`, read on list/get).
- Cluster targeting via `cluster_id` → Tasks 1, 5 (`k8s_for_cluster`).
- Air-gap internal registry/secret → Task 2 defaults + Task 3 values.
- New menu + status badges → Task 7.
- Hooks/types/i18n → Tasks 6, 7.

**Placeholder scan:** No TBD/TODO; every code step has concrete code. The two "open the file and match the existing pattern" notes (Tasks 6/7) point at concrete reference blocks (`useK8sClusters`, `adminDashboard`) — keep them as guidance, not placeholders.

**Type consistency:** `LlmdStackSummary` fields (Task 6) match `_serialize` output (Task 5) including `sync_status`/`health_status`/`status_message`. Hook names (`useLlmdStacks` etc.) are used identically in Task 7. `CreateLlmdStackBody` matches `CreateLlmdStackRequest`. `argo_app_name` set in Task 5 via `argo_app_name_for` (Task 3).

**Note for implementer:** local verification stops at unit tests + typecheck — there is no ArgoCD/GPU cluster here (same constraint as the multi-cluster work). A live sync must be validated on a real cluster.

---

## REVISION (2026-06-21): ArgoCD connection registry + REST API

Per the spec addendum, the portal manages Applications via **ArgoCD's REST API**
through a registered **ArgoCD connection** (settings menu like the cluster tab),
not by applying an Application CR. Reuses `app/services/crypto.py` (Fernet) and
mirrors `app/api/k8s_clusters.py` + `frontend/src/components/cluster-settings-tab.tsx`.

**Task 4 (K8sClient CustomObjects) is DROPPED** — not needed.

**Revised execution order:** Task 1 (done) → 1b → A → B → C → D → 2 → 3 → 5' → 6' → 7'.

### Task 1b: add `argocd_connection_id` to `custom_llmd_stack`
- Create migration `026_llmd_argocd_connection.py` (down_revision `025_llmd_stack`)
  adding nullable FK `argocd_connection_id` UUID → `custom_argocd_connection.id`
  ON DELETE RESTRICT — **created in Task A's migration order: A's table must
  exist first**, so sequence A before 1b OR fold this column into Task A's table
  creation is impossible (different table). Order: **A (creates connection table) → 1b (adds FK)**.
- Add `argocd_connection_id` mapped_column to `CustomLlmdStack`.
- Test: column present in `CustomLlmdStack.__table__.columns`.

### Task A: `custom_argocd_connection` model + migration
- Files: `backend/app/db/models/custom_argocd_connection.py`,
  `migrations/versions/026_argocd_connection.py` (down_revision `025_llmd_stack`),
  register in `__init__.py`. (Then Task 1b migration `027` adds the FK column.)
- Columns: `id, name(unique,128), server_url(512), token_encrypted(Text),
  insecure_skip_verify(bool default false), is_default(bool default false),
  description(Text null), created_by, updated_by, created_at, updated_at`.
- Test: columns present; pattern = `custom_k8s_cluster.py`.

### Task B: ArgoCD REST client `app/clients/argocd.py`
- `class ArgoCDClient` ctor `(server_url, token, *, insecure_skip_verify=False)`.
- Methods (httpx.AsyncClient, `Authorization: Bearer <token>`, `verify=not insecure`):
  - `async def version() -> str` — `GET /api/version` → `.version`.
  - `async def userinfo() -> dict` — `GET /api/v1/session/userinfo` (test).
  - `async def create_application(body: dict) -> None` — `POST /api/v1/applications`.
  - `async def get_application(name: str) -> dict | None` — `GET /api/v1/applications/{name}`; None on 404.
  - `async def delete_application(name: str, *, cascade=True) -> None` — `DELETE`; ignore 404.
- `async def probe_argocd(server_url, token, insecure) -> str` — connection test → returns version.
- Test: with `respx`/`httpx.MockTransport`, assert 404→None for get_application and correct URL/headers for create. (Mirror the mock style in Task 4's old test.)

### Task C: ArgoCD connections CRUD API `/api/admin/argocd-connections`
- File `backend/app/api/argocd_connections.py`; register in `main.py`.
- Mirror `k8s_clusters.py`: `CreateArgocdConnectionRequest{name, server_url, token,
  insecure_skip_verify=False, description?, is_default=False}`,
  `UpdateArgocdConnectionRequest{... token optional = keep existing ...}`.
  `_serialize` masks the token → returns `has_token`, never `token`.
  Endpoints: `GET ""`, `POST ""` (encrypt token via `crypto.encrypt`), `PUT/{id}`,
  `DELETE/{id}`, `POST /test` (unsaved), `POST /{id}/test` (stored) → `probe_argocd`.
  Single default via an `_unset_other_defaults` helper (copy from k8s_clusters).
- Test: `_serialize` never contains `token`/`token_encrypted`; crypto round-trip.

### Task D: Settings UI — ArgoCD tab
- `frontend/src/components/argocd-settings-tab.tsx` modeled on
  `cluster-settings-tab.tsx` (list, add/edit dialog with masked token field
  "변경하려면 새로 붙여넣기", insecure checkbox, **연결 테스트**, delete).
- Add a 4th tab in `admin/settings/page.tsx`: `<TabsTrigger value="argocd">` +
  `<TabsContent>` rendering `<ArgocdSettingsTab/>`.
- `use-api.ts` hooks `useArgocdConnections/useCreate/Update/Delete/Test/TestSaved`,
  `type ArgocdConnectionSummary` in `types/index.ts`, i18n `settings.argocd.*`
  (+ `tabArgocd`) in en.json/ko.json (symmetric keys).

### Task 2' (revised): config
- Keep `llmd_chart_repo/name/version`, `llmd_image_registry`, `llmd_hf_secret_name`.
- **Remove** `argocd_namespace` (unused now). Test updated accordingly.

### Task 3 (unchanged): builders
- `build_argo_application` keeps producing the Application body; drop the
  `argocd_namespace` parameter (the REST API path doesn't need metadata.namespace;
  ArgoCD assigns it). Adjust signature to drop `argocd_namespace` and the
  `metadata.namespace` line. Update test accordingly.

### Task 5' (revised): llm-d API uses the ArgoCD connection
- `CreateLlmdStackRequest` gains `argocd_connection_id: str` (required).
- Resolve the connection row → decrypt token → `ArgoCDClient(server_url, token,
  insecure_skip_verify=...)`. create/update → `create_application(body)`;
  delete → `delete_application(name)`; status → `get_application(name)` → `_argo_status`.
- Drop all `k8s_for_cluster` / CustomObjects usage for llm-d.
- `_serialize` adds `argocd_connection_id`.

### Task 6'/7' (revised): frontend
- `LlmdStackSummary` + `CreateLlmdStackBody` gain `argocd_connection_id`.
- The llm-d page Add dialog gets an **ArgoCD connection** picker
  (`useArgocdConnections`) — required.

**Self-review of revision:** ArgoCD connection (A–D) fully precedes llm-d API
(5'). Token is masked everywhere (Task C `_serialize`, Task D masked field).
crypto reused, not reinvented. Migration chain: 025 → 026 (argocd table) → 027
(llmd argocd_connection_id FK). Single head after each.
