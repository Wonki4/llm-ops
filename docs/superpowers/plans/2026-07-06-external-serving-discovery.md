# External vLLM/SGLang Serving Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show externally-deployed vLLM/SGLang servings (not created via the portal) in the Admin → Deployments list, with one-click LiteLLM registration.

**Architecture:** `GET /api/model-deployments/external` live-scans every cluster (portal default kubeconfig + all registered `custom_k8s_cluster` rows) in parallel with per-cluster timeouts, filters Deployments by image heuristic (`vllm`/`sglang` in the image name), excludes portal-managed workloads by label, and joins the result against a new `custom_external_serving` table that stores only LiteLLM registration state. No reconciler involvement.

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic + kubernetes_asyncio (backend); Next.js + react-query + shadcn/ui + next-intl (frontend); pytest with `asyncio_mode = "auto"`.

**Spec:** `docs/superpowers/specs/2026-07-06-external-serving-discovery-design.md`

## Global Constraints

- Branch: all commits go on `feat/external-serving-discovery` (already checked out).
- Backend tests run with `cd /Users/wongibaek/Documents/litellm-ops/backend && .venv/bin/pytest` (asyncio_mode is `auto` — async tests need no decorator).
- Ruff: line-length 120, target py311. Match existing code style (double quotes, trailing commas as in neighboring code).
- Portal-managed label: `llm-ops/managed-by: litellm-portal` (defined in `app/services/model_deployment_manifests.py` as `LABEL_OWNER`); external discovery MUST exclude Deployments carrying it.
- FastAPI route-order hazard: the existing router has `GET /{deployment_id}`. Every new `/external*` route MUST be registered BEFORE it in `app/api/model_deployments.py`, or `"external"` gets parsed as a UUID and 500s.
- i18n: every new UI string goes into BOTH `frontend/messages/en.json` and `frontend/messages/ko.json` under `adminDeployments`.
- Frontend type check: `cd /Users/wongibaek/Documents/litellm-ops/frontend && npx tsc --noEmit`.

## File Map

| File | Action | Responsibility |
|---|---|---|
| `backend/app/services/deployment_status.py` | Create | Shared status classifier (moved out of reconciler) |
| `backend/app/jobs/reconcile_deployments.py` | Modify | Import `classify` from the new module; delete local `_classify` |
| `backend/app/clients/k8s.py` | Modify | Add `list_deployments_all()` |
| `backend/app/services/external_servings.py` | Create | Heuristic filter, serving serialization, parallel cluster scan |
| `backend/app/db/models/custom_external_serving.py` | Create | Registration mapping table model |
| `backend/app/db/models/__init__.py` | Modify | Export new model |
| `backend/migrations/versions/034_external_serving.py` | Create | Create `custom_external_serving` |
| `backend/app/api/model_deployments.py` | Modify | `GET /external`, `POST /external/register`, `DELETE /external/register/{id}` |
| `backend/tests/test_external_servings.py` | Create | All backend tests for this feature |
| `frontend/src/hooks/use-api.ts` | Modify | `useExternalServings` + register/unregister mutations + types |
| `frontend/src/components/external-serving-register-dialog.tsx` | Create | Registration dialog |
| `frontend/src/app/(app)/admin/deployments/page.tsx` | Modify | Merged table, External badge, inline expansion, error banner |
| `frontend/messages/en.json`, `frontend/messages/ko.json` | Modify | New `adminDeployments` keys |

---

### Task 1: Shared status classifier

**Files:**
- Create: `backend/app/services/deployment_status.py`
- Modify: `backend/app/jobs/reconcile_deployments.py` (delete `_classify`, import instead)
- Test: `backend/tests/test_external_servings.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `classify(observed: dict, desired_replicas: int) -> tuple[str, str | None]` where `observed = {"ready": int, "available": int, "conditions": [{"type","status","reason","message"}]}`. Returns `(status, message)`; status ∈ Ready/Pending/Updating/Unhealthy/Failed/Stopped. Task 3 and the reconciler both call this.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_external_servings.py`:

```python
"""Tests for external vLLM/SGLang serving discovery."""

from app.services.deployment_status import classify


# ─── classify ────────────────────────────────────────────────


def test_classify_ready():
    observed = {"ready": 2, "available": 2, "conditions": []}
    assert classify(observed, 2) == ("Ready", None)


def test_classify_pending_when_no_ready_pods():
    observed = {"ready": 0, "available": 0, "conditions": []}
    status, message = classify(observed, 1)
    assert status == "Pending"


def test_classify_stopped_when_zero_desired():
    observed = {"ready": 0, "available": 0, "conditions": []}
    status, _ = classify(observed, 0)
    assert status == "Stopped"


def test_classify_failed_on_progress_deadline():
    observed = {
        "ready": 0,
        "available": 0,
        "conditions": [
            {"type": "Progressing", "status": "False", "reason": "ProgressDeadlineExceeded", "message": "x"}
        ],
    }
    status, _ = classify(observed, 1)
    assert status == "Failed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/wongibaek/Documents/litellm-ops/backend && .venv/bin/pytest tests/test_external_servings.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.deployment_status'`

- [ ] **Step 3: Create the module (move `_classify` verbatim)**

Create `backend/app/services/deployment_status.py` — the body is the exact `_classify` from `app/jobs/reconcile_deployments.py`, renamed:

```python
"""Coarse-grained K8s Deployment status classification.

Shared by the reconciler (portal-managed deployments) and the external
serving discovery endpoint so both report identical status strings.
"""


def classify(observed: dict, desired_replicas: int) -> tuple[str, str | None]:
    """Return (status, message) from a K8s deployment status payload.

    ``observed`` = {"ready": int, "available": int, "conditions": [...]} as
    produced by K8sClient.read_deployment_status / list_deployments_all.
    """
    ready = observed.get("ready", 0)
    available = observed.get("available", 0)
    conditions = observed.get("conditions", [])

    progressing_failed = any(
        c.get("type") == "Progressing"
        and c.get("status") == "False"
        and c.get("reason") in ("ProgressDeadlineExceeded",)
        for c in conditions
    )
    if progressing_failed:
        return "Failed", "Deployment progress deadline exceeded"

    replica_failure = any(c.get("type") == "ReplicaFailure" and c.get("status") == "True" for c in conditions)
    if replica_failure:
        msg = next((c.get("message") for c in conditions if c.get("type") == "ReplicaFailure"), None)
        return "Unhealthy", msg or "ReplicaFailure condition true"

    if desired_replicas == 0:
        return "Stopped", "replicas set to 0"

    if ready >= desired_replicas and available >= desired_replicas:
        return "Ready", None
    if ready == 0:
        return "Pending", "No ready pods yet"
    return "Updating", f"{ready}/{desired_replicas} pods ready"
```

In `backend/app/jobs/reconcile_deployments.py`:
1. Delete the whole `_classify` function.
2. Add import: `from app.services.deployment_status import classify`
3. Change the single call site `new_status, message = _classify(observed, dep.replicas)` to `new_status, message = classify(observed, dep.replicas)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/wongibaek/Documents/litellm-ops/backend && .venv/bin/pytest tests/test_external_servings.py -v && .venv/bin/pytest -q`
Expected: new tests PASS; full suite still green (reconciler import intact).

- [ ] **Step 5: Commit**

```bash
cd /Users/wongibaek/Documents/litellm-ops
git add backend/app/services/deployment_status.py backend/app/jobs/reconcile_deployments.py backend/tests/test_external_servings.py
git commit -m "refactor(backend): extract deployment status classifier to shared service"
```

---

### Task 2: `K8sClient.list_deployments_all()`

**Files:**
- Modify: `backend/app/clients/k8s.py` (add method after `read_service_cluster_ip`, before the Jobs section)
- Test: `backend/tests/test_external_servings.py`

**Interfaces:**
- Consumes: existing `K8sClient._api_client()`.
- Produces: `async def list_deployments_all(self) -> list[dict]` — each item:
  ```python
  {
    "name": str, "namespace": str, "labels": dict[str, str],
    "created_at": str | None,               # ISO 8601
    "containers": [{"image": str, "args": list[str]}],
    "replicas": int, "ready": int, "available": int,
    "conditions": [{"type","status","reason","message"}],
  }
  ```
  Task 3 consumes this shape.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_external_servings.py`:

```python
import types
from unittest.mock import AsyncMock, MagicMock, patch

from app.clients.k8s import K8sClient


# ─── list_deployments_all ────────────────────────────────────


def _fake_k8s_deployment(name="ext-vllm", namespace="team-a", image="vllm/vllm-openai:v0.6.0",
                         args=None, labels=None, replicas=2, ready=2, available=2):
    container = types.SimpleNamespace(image=image, args=args or ["--model", "/models/llama"])
    dep = types.SimpleNamespace(
        metadata=types.SimpleNamespace(name=name, namespace=namespace, labels=labels or {},
                                       creation_timestamp=None),
        spec=types.SimpleNamespace(
            replicas=replicas,
            template=types.SimpleNamespace(spec=types.SimpleNamespace(containers=[container])),
        ),
        status=types.SimpleNamespace(ready_replicas=ready, available_replicas=available, conditions=None),
    )
    return dep


async def test_list_deployments_all_shapes_items():
    fake_apps = MagicMock()
    fake_apps.list_deployment_for_all_namespaces = AsyncMock(
        return_value=types.SimpleNamespace(items=[_fake_k8s_deployment()])
    )
    fake_api_client = MagicMock()
    fake_api_client.close = AsyncMock()

    k8s = K8sClient()
    with patch.object(K8sClient, "_api_client", AsyncMock(return_value=fake_api_client)), \
         patch("app.clients.k8s.client.AppsV1Api", return_value=fake_apps):
        items = await k8s.list_deployments_all()

    assert len(items) == 1
    item = items[0]
    assert item["name"] == "ext-vllm"
    assert item["namespace"] == "team-a"
    assert item["containers"] == [{"image": "vllm/vllm-openai:v0.6.0", "args": ["--model", "/models/llama"]}]
    assert item["replicas"] == 2 and item["ready"] == 2 and item["available"] == 2
    assert item["conditions"] == []
    fake_api_client.close.assert_awaited()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/wongibaek/Documents/litellm-ops/backend && .venv/bin/pytest tests/test_external_servings.py::test_list_deployments_all_shapes_items -v`
Expected: FAIL — `AttributeError: 'K8sClient' object has no attribute 'list_deployments_all'`

- [ ] **Step 3: Implement the method**

In `backend/app/clients/k8s.py`, after `read_service_cluster_ip` (line ~143), add:

```python
    async def list_deployments_all(self) -> list[dict]:
        """List Deployments across all namespaces, shaped for discovery.

        One cluster-wide LIST call. Used by the external-serving discovery
        endpoint; the RBAC ClusterRole already grants deployments list.
        """
        api_client = await self._api_client()
        try:
            apps = client.AppsV1Api(api_client)
            result = await apps.list_deployment_for_all_namespaces()
            items: list[dict] = []
            for dep in result.items:
                containers = dep.spec.template.spec.containers or []
                created = dep.metadata.creation_timestamp
                items.append(
                    {
                        "name": dep.metadata.name,
                        "namespace": dep.metadata.namespace,
                        "labels": dict(dep.metadata.labels or {}),
                        "created_at": created.isoformat() if created else None,
                        "containers": [{"image": c.image, "args": list(c.args or [])} for c in containers],
                        "replicas": int(dep.spec.replicas or 0),
                        "ready": int(dep.status.ready_replicas or 0),
                        "available": int(dep.status.available_replicas or 0),
                        "conditions": [
                            {"type": c.type, "status": c.status, "reason": c.reason, "message": c.message}
                            for c in (dep.status.conditions or [])
                        ],
                    }
                )
            return items
        finally:
            await api_client.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/wongibaek/Documents/litellm-ops/backend && .venv/bin/pytest tests/test_external_servings.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
cd /Users/wongibaek/Documents/litellm-ops
git add backend/app/clients/k8s.py backend/tests/test_external_servings.py
git commit -m "feat(backend): K8sClient.list_deployments_all for cluster-wide discovery"
```

---

### Task 3: Discovery service (heuristic + scan)

**Files:**
- Create: `backend/app/services/external_servings.py`
- Test: `backend/tests/test_external_servings.py`

**Interfaces:**
- Consumes: `classify` (Task 1), `list_deployments_all` item shape (Task 2), `K8sNotConfigured` from `app.clients.k8s`.
- Produces (used by Task 5):
  - `to_external_serving(dep: dict) -> dict | None` — None when not an external vLLM/SGLang serving.
  - `async scan_clusters(targets: list[tuple[str | None, str, Any]], timeout: float = 5.0) -> tuple[list[dict], list[dict]]` — targets are `(cluster_id, cluster_name, k8s_client)`; returns `(servings, errors)`; each serving carries `cluster_id`/`cluster_name`; errors are `{"cluster": name, "message": str}`.
  - Serving dict shape:
    ```python
    {
      "cluster_id": str | None, "cluster_name": str,
      "namespace": str, "deployment_name": str,
      "engine": "vllm" | "sglang", "image": str,
      "replicas": int, "ready_replicas": int,
      "status": str, "status_message": str | None,
      "created_at": str | None, "model_path": str | None,
      "labels": dict, "args": list[str],
    }
    ```

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_external_servings.py`:

```python
import asyncio

from app.clients.k8s import K8sNotConfigured
from app.services.external_servings import scan_clusters, to_external_serving


def _raw_dep(name="ext-vllm", namespace="team-a", image="vllm/vllm-openai:v0.6.0",
             args=None, labels=None, replicas=2, ready=2, available=2):
    return {
        "name": name, "namespace": namespace, "labels": labels or {},
        "created_at": "2026-07-01T00:00:00+00:00",
        "containers": [{"image": image, "args": args if args is not None else ["--model", "/models/llama-3-8b"]}],
        "replicas": replicas, "ready": ready, "available": available, "conditions": [],
    }


# ─── to_external_serving ─────────────────────────────────────


def test_vllm_image_detected():
    serving = to_external_serving(_raw_dep())
    assert serving["engine"] == "vllm"
    assert serving["deployment_name"] == "ext-vllm"
    assert serving["model_path"] == "/models/llama-3-8b"
    assert serving["status"] == "Ready"


def test_sglang_image_detected():
    serving = to_external_serving(_raw_dep(image="lmsysorg/sglang:latest"))
    assert serving["engine"] == "sglang"


def test_unrelated_image_ignored():
    assert to_external_serving(_raw_dep(image="nginx:1.27")) is None


def test_portal_managed_label_excluded():
    dep = _raw_dep(labels={"llm-ops/managed-by": "litellm-portal"})
    assert to_external_serving(dep) is None


def test_model_arg_equals_form():
    serving = to_external_serving(_raw_dep(args=["--model=/models/qwen", "--port", "8000"]))
    assert serving["model_path"] == "/models/qwen"


def test_missing_model_arg_gives_none_path():
    serving = to_external_serving(_raw_dep(args=["--port", "8000"]))
    assert serving["model_path"] is None


# ─── scan_clusters ───────────────────────────────────────────


def _fake_client(deployments=None, error=None):
    fake = MagicMock()
    if error is not None:
        fake.list_deployments_all = AsyncMock(side_effect=error)
    else:
        fake.list_deployments_all = AsyncMock(return_value=deployments or [])
    return fake


async def test_scan_clusters_merges_and_tags_cluster():
    targets = [
        (None, "default", _fake_client([_raw_dep()])),
        ("cid-1", "prod", _fake_client([_raw_dep(name="prod-vllm", namespace="ml")])),
    ]
    servings, errors = await scan_clusters(targets)
    assert errors == []
    assert {(s["cluster_name"], s["deployment_name"]) for s in servings} == {("default", "ext-vllm"), ("prod", "prod-vllm")}
    assert [s for s in servings if s["cluster_name"] == "prod"][0]["cluster_id"] == "cid-1"


async def test_scan_clusters_partial_failure_reports_error():
    targets = [
        (None, "default", _fake_client([_raw_dep()])),
        ("cid-1", "prod", _fake_client(error=RuntimeError("connection refused"))),
    ]
    servings, errors = await scan_clusters(targets)
    assert len(servings) == 1
    assert errors == [{"cluster": "prod", "message": "connection refused"}]


async def test_scan_clusters_not_configured_is_silent():
    targets = [(None, "default", _fake_client(error=K8sNotConfigured("no kubeconfig")))]
    servings, errors = await scan_clusters(targets)
    assert servings == [] and errors == []


async def test_scan_clusters_timeout_reports_error():
    async def _hang():
        await asyncio.sleep(10)

    fake = MagicMock()
    fake.list_deployments_all = MagicMock(side_effect=lambda: _hang())
    servings, errors = await scan_clusters([("cid-1", "slow", fake)], timeout=0.05)
    assert servings == []
    assert errors[0]["cluster"] == "slow"
    assert "timed out" in errors[0]["message"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/wongibaek/Documents/litellm-ops/backend && .venv/bin/pytest tests/test_external_servings.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.external_servings'`

- [ ] **Step 3: Implement the service**

Create `backend/app/services/external_servings.py`:

```python
"""Discovery of externally-deployed vLLM/SGLang servings.

Live-scans clusters (no persistence): a Deployment counts as an external
serving when any container image name contains "vllm" or "sglang" and it does
NOT carry the portal's managed-by label. Pure functions + a parallel scan
helper; the API layer owns DB access and LiteLLM registration.
"""

import asyncio
import logging
from typing import Any

from app.clients.k8s import K8sNotConfigured
from app.services.deployment_status import classify
from app.services.model_deployment_manifests import LABEL_OWNER

logger = logging.getLogger(__name__)

PORTAL_MANAGED_VALUE = "litellm-portal"


def _detect_engine(containers: list[dict]) -> tuple[str, dict] | None:
    """Return (engine, container) for the first vLLM/SGLang container, else None."""
    for c in containers:
        image = (c.get("image") or "").lower()
        if "sglang" in image:
            return "sglang", c
        if "vllm" in image:
            return "vllm", c
    return None


def _extract_model_path(args: list[str]) -> str | None:
    for i, a in enumerate(args):
        if a == "--model" and i + 1 < len(args):
            return str(args[i + 1])
        if isinstance(a, str) and a.startswith("--model="):
            return a.split("=", 1)[1]
    return None


def to_external_serving(dep: dict) -> dict | None:
    """Shape one list_deployments_all item into an external serving, or None."""
    if dep.get("labels", {}).get(LABEL_OWNER) == PORTAL_MANAGED_VALUE:
        return None
    detected = _detect_engine(dep.get("containers", []))
    if detected is None:
        return None
    engine, container = detected
    status, message = classify(dep, dep.get("replicas", 0))
    return {
        "namespace": dep["namespace"],
        "deployment_name": dep["name"],
        "engine": engine,
        "image": container.get("image"),
        "replicas": dep.get("replicas", 0),
        "ready_replicas": dep.get("ready", 0),
        "status": status,
        "status_message": message,
        "created_at": dep.get("created_at"),
        "model_path": _extract_model_path(container.get("args", [])),
        "labels": dep.get("labels", {}),
        "args": container.get("args", []),
    }


async def scan_clusters(
    targets: list[tuple[str | None, str, Any]], timeout: float = 5.0
) -> tuple[list[dict], list[dict]]:
    """Scan (cluster_id, cluster_name, k8s_client) targets in parallel.

    Returns (servings, errors). A missing default kubeconfig is silently
    skipped; timeouts and connection errors become per-cluster error entries
    so one broken cluster never blanks the page.
    """

    async def _scan(k8s: Any) -> list[dict]:
        raw = await asyncio.wait_for(k8s.list_deployments_all(), timeout=timeout)
        return [s for s in (to_external_serving(d) for d in raw) if s is not None]

    results = await asyncio.gather(*(_scan(k8s) for _, _, k8s in targets), return_exceptions=True)

    servings: list[dict] = []
    errors: list[dict] = []
    for (cluster_id, cluster_name, _), result in zip(targets, results):
        if isinstance(result, K8sNotConfigured):
            continue
        if isinstance(result, TimeoutError | asyncio.TimeoutError):
            errors.append({"cluster": cluster_name, "message": f"scan timed out after {timeout:g}s"})
            continue
        if isinstance(result, BaseException):
            logger.warning("External serving scan failed for %s: %s", cluster_name, result)
            errors.append({"cluster": cluster_name, "message": str(result) or type(result).__name__})
            continue
        for s in result:
            s["cluster_id"] = cluster_id
            s["cluster_name"] = cluster_name
            servings.append(s)
    return servings, errors
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/wongibaek/Documents/litellm-ops/backend && .venv/bin/pytest tests/test_external_servings.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
cd /Users/wongibaek/Documents/litellm-ops
git add backend/app/services/external_servings.py backend/tests/test_external_servings.py
git commit -m "feat(backend): external vLLM/SGLang serving discovery service"
```

---

### Task 4: Registration table (model + migration)

**Files:**
- Create: `backend/app/db/models/custom_external_serving.py`
- Modify: `backend/app/db/models/__init__.py`
- Create: `backend/migrations/versions/034_external_serving.py`

**Interfaces:**
- Produces: `CustomExternalServing` ORM model (Task 5 queries it). Columns: `id, cluster_id (nullable FK), namespace, deployment_name, model_name, api_base, litellm_model_id, registered_by, created_at`. Unique `(cluster_id, namespace, deployment_name)` with `postgresql_nulls_not_distinct=True` (PG16; NULL cluster_id rows must also be unique).

- [ ] **Step 1: Write the model**

Create `backend/app/db/models/custom_external_serving.py`:

```python
"""LiteLLM registrations for externally-discovered vLLM/SGLang servings.

Discovery itself is a live cluster scan (no rows); this table only remembers
which discovered serving was registered with LiteLLM, keyed by
(cluster, namespace, deployment name).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import CustomBase


class CustomExternalServing(CustomBase):
    __tablename__ = "custom_external_serving"
    __table_args__ = (
        UniqueConstraint(
            "cluster_id", "namespace", "deployment_name",
            name="uq_external_serving_target",
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Null = portal default kubeconfig, mirroring custom_model_deployment.cluster_id.
    cluster_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("custom_k8s_cluster.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    namespace: Mapped[str] = mapped_column(String(128), nullable=False)
    deployment_name: Mapped[str] = mapped_column(String(253), nullable=False)
    model_name: Mapped[str] = mapped_column(String(256), nullable=False)
    api_base: Mapped[str] = mapped_column(String(512), nullable=False)
    litellm_model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    registered_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

In `backend/app/db/models/__init__.py`, add the import and `__all__` entry:

```python
from app.db.models.custom_external_serving import CustomExternalServing
```
and `"CustomExternalServing",` in `__all__`.

- [ ] **Step 2: Write the migration**

Create `backend/migrations/versions/034_external_serving.py`:

```python
"""External serving registrations (LiteLLM mapping for discovered vLLM/SGLang)

Stores only registration state; discovery is a live scan. Unique on
(cluster_id, namespace, deployment_name) with NULLS NOT DISTINCT so
default-cluster (NULL) rows can't duplicate either.

Revision ID: 034_external_serving
Revises: 033_cache_read_cost
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "034_external_serving"
down_revision = "033_cache_read_cost"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_external_serving",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "cluster_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("custom_k8s_cluster.id", ondelete="RESTRICT"),
            nullable=True,
            index=True,
        ),
        sa.Column("namespace", sa.String(128), nullable=False),
        sa.Column("deployment_name", sa.String(253), nullable=False),
        sa.Column("model_name", sa.String(256), nullable=False),
        sa.Column("api_base", sa.String(512), nullable=False),
        sa.Column("litellm_model_id", sa.String(128), nullable=False),
        sa.Column("registered_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "cluster_id", "namespace", "deployment_name",
            name="uq_external_serving_target",
            postgresql_nulls_not_distinct=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("custom_external_serving")
```

- [ ] **Step 3: Apply the migration against the local dockerized DB**

The docker stack must be up (`litellm_db` healthy). Run:

```bash
cd /Users/wongibaek/Documents/litellm-ops/backend
APP_DATABASE_URL="postgresql+asyncpg://llmproxy:dbpassword9090@localhost:5432/litellm_portal" .venv/bin/alembic upgrade head
```

Expected output includes: `Running upgrade 033_cache_read_cost -> 034_external_serving`.
(If the env var name differs, check `backend/migrations/env.py` for how it reads the URL and use that variable; the DSN above matches docker-compose defaults.)

- [ ] **Step 4: Verify import wiring**

Run: `cd /Users/wongibaek/Documents/litellm-ops/backend && .venv/bin/python -c "from app.db.models import CustomExternalServing; print(CustomExternalServing.__tablename__)"`
Expected: `custom_external_serving`

- [ ] **Step 5: Commit**

```bash
cd /Users/wongibaek/Documents/litellm-ops
git add backend/app/db/models/custom_external_serving.py backend/app/db/models/__init__.py backend/migrations/versions/034_external_serving.py
git commit -m "feat(backend): custom_external_serving registration table"
```

---

### Task 5: `GET /api/model-deployments/external`

**Files:**
- Modify: `backend/app/api/model_deployments.py`
- Test: `backend/tests/test_external_servings.py`

**Interfaces:**
- Consumes: `scan_clusters` (Task 3), `CustomExternalServing` (Task 4), existing `k8s_for_cluster`, `require_super_user`, `get_db`.
- Produces: `GET /external` → `{"servings": [serving + "registration": {...}|None], "errors": [...]}`. Registration object: `{"id", "model_name", "api_base", "litellm_model_id"}`. Task 7/8 consume this JSON.

**ROUTE ORDER:** insert all three `/external*` routes immediately after `list_deployments` (the `@router.get("")` handler) and BEFORE `@router.get("/{deployment_id}")`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_external_servings.py`:

```python
# ─── GET /api/model-deployments/external ─────────────────────


def _exec_result(rows):
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    result.scalar_one_or_none.return_value = rows[0] if rows else None
    return result


def _serving(cluster_id=None, cluster_name="default", name="ext-vllm", namespace="team-a"):
    return {
        "cluster_id": cluster_id, "cluster_name": cluster_name,
        "namespace": namespace, "deployment_name": name,
        "engine": "vllm", "image": "vllm/vllm-openai:v0.6.0",
        "replicas": 2, "ready_replicas": 2,
        "status": "Ready", "status_message": None,
        "created_at": "2026-07-01T00:00:00+00:00", "model_path": "/models/llama-3-8b",
        "labels": {}, "args": ["--model", "/models/llama-3-8b"],
    }


async def test_get_external_servings(client_for_user, super_user, mock_db):
    # execute #1: registered clusters (none) / execute #2: registrations (none)
    mock_db.execute = AsyncMock(side_effect=[_exec_result([]), _exec_result([])])
    with patch("app.api.model_deployments.scan_clusters", AsyncMock(return_value=([_serving()], []))):
        async with client_for_user(super_user) as client:
            resp = await client.get("/api/model-deployments/external")
    assert resp.status_code == 200
    body = resp.json()
    assert body["errors"] == []
    assert len(body["servings"]) == 1
    assert body["servings"][0]["deployment_name"] == "ext-vllm"
    assert body["servings"][0]["registration"] is None


async def test_get_external_servings_joins_registration(client_for_user, super_user, mock_db):
    reg = MagicMock()
    reg.id = uuid.uuid4()
    reg.cluster_id = None
    reg.namespace = "team-a"
    reg.deployment_name = "ext-vllm"
    reg.model_name = "llama-3-8b"
    reg.api_base = "https://ext.example.com"
    reg.litellm_model_id = "litellm-abc"
    mock_db.execute = AsyncMock(side_effect=[_exec_result([]), _exec_result([reg])])
    with patch("app.api.model_deployments.scan_clusters", AsyncMock(return_value=([_serving()], []))):
        async with client_for_user(super_user) as client:
            resp = await client.get("/api/model-deployments/external")
    body = resp.json()
    assert body["servings"][0]["registration"]["model_name"] == "llama-3-8b"
    assert body["servings"][0]["registration"]["litellm_model_id"] == "litellm-abc"


async def test_get_external_servings_reports_cluster_errors(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(side_effect=[_exec_result([]), _exec_result([])])
    with patch(
        "app.api.model_deployments.scan_clusters",
        AsyncMock(return_value=([], [{"cluster": "prod", "message": "scan timed out after 5s"}])),
    ):
        async with client_for_user(super_user) as client:
            resp = await client.get("/api/model-deployments/external")
    assert resp.status_code == 200
    assert resp.json()["errors"][0]["cluster"] == "prod"


async def test_get_external_servings_requires_super_user(client_for_user, regular_user):
    async with client_for_user(regular_user) as client:
        resp = await client.get("/api/model-deployments/external")
    assert resp.status_code == 403
```

Also add `import uuid` at the top of the test file if not present.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/wongibaek/Documents/litellm-ops/backend && .venv/bin/pytest tests/test_external_servings.py -v -k external_servings`
Expected: the new tests FAIL. Likely 500 (route falls through to `/{deployment_id}` and `uuid.UUID("external")` raises) or patch fails with `AttributeError: ... has no attribute 'scan_clusters'`.

- [ ] **Step 3: Implement the endpoint**

In `backend/app/api/model_deployments.py`:

Add imports at the top (keep existing ones):

```python
from app.clients.k8s import K8sClient
from app.db.models.custom_external_serving import CustomExternalServing
from app.db.models.custom_k8s_cluster import CustomK8sCluster
from app.services.external_servings import scan_clusters
```

Insert AFTER the `list_deployments` handler and BEFORE `@router.get("/{deployment_id}")`:

```python
def _serialize_registration(r: CustomExternalServing) -> dict:
    return {
        "id": str(r.id),
        "model_name": r.model_name,
        "api_base": r.api_base,
        "litellm_model_id": r.litellm_model_id,
    }


@router.get("/external")
async def list_external_servings(
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Live-scan all clusters for vLLM/SGLang deployments not managed by the portal."""
    targets: list[tuple[str | None, str, K8sClient]] = [(None, "default", K8sClient())]
    clusters = (await db.execute(select(CustomK8sCluster))).scalars().all()
    for row in clusters:
        targets.append((str(row.id), row.name, await k8s_for_cluster(db, row.id)))

    servings, errors = await scan_clusters(targets)

    regs = (await db.execute(select(CustomExternalServing))).scalars().all()
    reg_map = {
        (str(r.cluster_id) if r.cluster_id else None, r.namespace, r.deployment_name): r
        for r in regs
    }
    for s in servings:
        r = reg_map.get((s["cluster_id"], s["namespace"], s["deployment_name"]))
        s["registration"] = _serialize_registration(r) if r else None

    return {"servings": servings, "errors": errors}
```

(`select`, `CustomUser`, `require_super_user`, `get_db`, `k8s_for_cluster`, `AsyncSession` are already imported in this file for the existing handlers — verify, don't duplicate.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/wongibaek/Documents/litellm-ops/backend && .venv/bin/pytest tests/test_external_servings.py -v && .venv/bin/pytest -q`
Expected: PASS (all; full suite green — proves `/{deployment_id}` routes still work).

- [ ] **Step 5: Commit**

```bash
cd /Users/wongibaek/Documents/litellm-ops
git add backend/app/api/model_deployments.py backend/tests/test_external_servings.py
git commit -m "feat(backend): GET /api/model-deployments/external live discovery endpoint"
```

---

### Task 6: Register / unregister endpoints

**Files:**
- Modify: `backend/app/api/model_deployments.py`
- Test: `backend/tests/test_external_servings.py`

**Interfaces:**
- Consumes: `CustomExternalServing`, `LiteLLMClient.create_model` / `delete_model`, `get_litellm_client`.
- Produces:
  - `POST /external/register` body `{cluster_id?: str|null, namespace: str, deployment_name: str, model_name: str, served_model_name: str, api_base: str, api_key?: str}` → 201 `{"registration": {...}}`; 409 duplicate; 502 LiteLLM failure.
  - `DELETE /external/register/{registration_id}` → `{"deleted": true, "litellm_deleted": bool}`; 404 unknown id. LiteLLM delete failure is swallowed (row still removed) so unregister is idempotent.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_external_servings.py`:

```python
# ─── POST /external/register ─────────────────────────────────


REGISTER_BODY = {
    "cluster_id": None,
    "namespace": "team-a",
    "deployment_name": "ext-vllm",
    "model_name": "llama-3-8b",
    "served_model_name": "/models/llama-3-8b",
    "api_base": "https://ext-vllm.example.com",
}


async def test_register_external_serving(client_for_user, super_user, mock_db, mock_litellm):
    mock_db.execute = AsyncMock(return_value=_exec_result([]))  # duplicate check: none
    mock_litellm.create_model = AsyncMock(return_value={"model_info": {"id": "litellm-new-1"}})
    async with client_for_user(super_user) as client:
        resp = await client.post("/api/model-deployments/external/register", json=REGISTER_BODY)
    assert resp.status_code == 201
    assert resp.json()["registration"]["litellm_model_id"] == "litellm-new-1"
    mock_litellm.create_model.assert_awaited_once_with(
        model_name="llama-3-8b",
        litellm_model="openai//models/llama-3-8b",
        api_base="https://ext-vllm.example.com",
        api_key="EMPTY",
    )
    mock_db.add.assert_called_once()


async def test_register_duplicate_409(client_for_user, super_user, mock_db, mock_litellm):
    mock_db.execute = AsyncMock(return_value=_exec_result([MagicMock()]))  # existing row
    async with client_for_user(super_user) as client:
        resp = await client.post("/api/model-deployments/external/register", json=REGISTER_BODY)
    assert resp.status_code == 409
    mock_litellm.create_model.assert_not_called()


async def test_register_litellm_failure_502(client_for_user, super_user, mock_db, mock_litellm):
    mock_db.execute = AsyncMock(return_value=_exec_result([]))
    mock_litellm.create_model = AsyncMock(side_effect=RuntimeError("litellm down"))
    async with client_for_user(super_user) as client:
        resp = await client.post("/api/model-deployments/external/register", json=REGISTER_BODY)
    assert resp.status_code == 502
    mock_db.add.assert_not_called()


# ─── DELETE /external/register/{id} ──────────────────────────


async def test_unregister_external_serving(client_for_user, super_user, mock_db, mock_litellm):
    reg = MagicMock()
    reg.litellm_model_id = "litellm-abc"
    mock_db.execute = AsyncMock(return_value=_exec_result([reg]))
    mock_litellm.delete_model = AsyncMock(return_value={"deleted": True})
    async with client_for_user(super_user) as client:
        resp = await client.delete(f"/api/model-deployments/external/register/{uuid.uuid4()}")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": True, "litellm_deleted": True}
    mock_db.delete.assert_awaited_once_with(reg)


async def test_unregister_swallows_litellm_failure(client_for_user, super_user, mock_db, mock_litellm):
    reg = MagicMock()
    reg.litellm_model_id = "litellm-abc"
    mock_db.execute = AsyncMock(return_value=_exec_result([reg]))
    mock_litellm.delete_model = AsyncMock(side_effect=RuntimeError("already gone"))
    async with client_for_user(super_user) as client:
        resp = await client.delete(f"/api/model-deployments/external/register/{uuid.uuid4()}")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": True, "litellm_deleted": False}
    mock_db.delete.assert_awaited_once_with(reg)


async def test_unregister_unknown_404(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_exec_result([]))
    async with client_for_user(super_user) as client:
        resp = await client.delete(f"/api/model-deployments/external/register/{uuid.uuid4()}")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/wongibaek/Documents/litellm-ops/backend && .venv/bin/pytest tests/test_external_servings.py -v -k "register or unregister"`
Expected: FAIL with 404/405 (routes don't exist yet).

- [ ] **Step 3: Implement the endpoints**

In `backend/app/api/model_deployments.py`, add imports (verify against existing):

```python
from app.clients.litellm import LiteLLMClient, get_litellm_client
```

Add the request model next to the existing request models:

```python
class RegisterExternalServingRequest(BaseModel):
    cluster_id: str | None = None
    namespace: str
    deployment_name: str
    model_name: str
    served_model_name: str
    api_base: str
    api_key: str | None = None
```

Insert the handlers right after `list_external_servings` (still BEFORE `/{deployment_id}`):

```python
@router.post("/external/register", status_code=status.HTTP_201_CREATED)
async def register_external_serving(
    body: RegisterExternalServingRequest,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
    litellm: LiteLLMClient = Depends(get_litellm_client),
) -> dict:
    """Register a discovered external serving with LiteLLM (/model/new)."""
    cid = uuid.UUID(body.cluster_id) if body.cluster_id else None
    existing = await db.execute(
        select(CustomExternalServing).where(
            CustomExternalServing.cluster_id == cid,
            CustomExternalServing.namespace == body.namespace,
            CustomExternalServing.deployment_name == body.deployment_name,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="This serving is already registered")

    try:
        result = await litellm.create_model(
            model_name=body.model_name,
            litellm_model=f"openai/{body.served_model_name}",
            api_base=body.api_base,
            api_key=body.api_key or "EMPTY",
        )
    except Exception:
        logger.exception("LiteLLM /model/new failed for external serving %s", body.deployment_name)
        raise HTTPException(status_code=502, detail="LiteLLM registration failed; check logs")
    info = result.get("model_info") or {}
    model_id = info.get("id") or result.get("id")

    reg = CustomExternalServing(
        id=uuid.uuid4(),
        cluster_id=cid,
        namespace=body.namespace,
        deployment_name=body.deployment_name,
        model_name=body.model_name,
        api_base=body.api_base,
        litellm_model_id=str(model_id),
        registered_by=user.user_id,
    )
    db.add(reg)
    await db.flush()
    return {"registration": _serialize_registration(reg)}


@router.delete("/external/register/{registration_id}")
async def unregister_external_serving(
    registration_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
    litellm: LiteLLMClient = Depends(get_litellm_client),
) -> dict:
    """Remove an external-serving registration (LiteLLM /model/delete + row).

    LiteLLM failures are swallowed (model may already be gone) so unregister
    stays idempotent; the mapping row is always removed.
    """
    result = await db.execute(
        select(CustomExternalServing).where(CustomExternalServing.id == uuid.UUID(registration_id))
    )
    reg = result.scalar_one_or_none()
    if not reg:
        raise HTTPException(status_code=404, detail="Registration not found")

    litellm_deleted = True
    try:
        await litellm.delete_model(reg.litellm_model_id)
    except Exception:
        logger.warning("LiteLLM /model/delete failed for %s; removing mapping anyway", reg.litellm_model_id)
        litellm_deleted = False

    await db.delete(reg)
    await db.flush()
    return {"deleted": True, "litellm_deleted": litellm_deleted}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/wongibaek/Documents/litellm-ops/backend && .venv/bin/pytest tests/test_external_servings.py -v && .venv/bin/pytest -q`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
cd /Users/wongibaek/Documents/litellm-ops
git add backend/app/api/model_deployments.py backend/tests/test_external_servings.py
git commit -m "feat(backend): register/unregister external servings with LiteLLM"
```

---

### Task 7: Frontend hooks + types

**Files:**
- Modify: `frontend/src/hooks/use-api.ts` (append near `useModelDeployments`, ~line 1103)

**Interfaces:**
- Consumes: Task 5/6 JSON shapes, existing `apiFetch`.
- Produces (Task 8 consumes): `ExternalServing`, `ExternalServingsResponse` types; `useExternalServings()`, `useRegisterExternalServing()`, `useUnregisterExternalServing()` hooks.

- [ ] **Step 1: Add types and hooks**

In `frontend/src/hooks/use-api.ts`, after the `useModelDeployment` hook add:

```ts
export interface ExternalServingRegistration {
  id: string;
  model_name: string;
  api_base: string;
  litellm_model_id: string;
}

export interface ExternalServing {
  cluster_id: string | null;
  cluster_name: string;
  namespace: string;
  deployment_name: string;
  engine: "vllm" | "sglang";
  image: string;
  replicas: number;
  ready_replicas: number;
  status: string;
  status_message: string | null;
  created_at: string | null;
  model_path: string | null;
  labels: Record<string, string>;
  args: string[];
  registration: ExternalServingRegistration | null;
}

export interface ExternalServingsResponse {
  servings: ExternalServing[];
  errors: { cluster: string; message: string }[];
}

export function useExternalServings() {
  return useQuery({
    queryKey: ["external-servings"],
    queryFn: () => apiFetch<ExternalServingsResponse>("/api/model-deployments/external"),
  });
}

export interface RegisterExternalServingBody {
  cluster_id: string | null;
  namespace: string;
  deployment_name: string;
  model_name: string;
  served_model_name: string;
  api_base: string;
  api_key?: string;
}

export function useRegisterExternalServing() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: RegisterExternalServingBody) =>
      apiFetch("/api/model-deployments/external/register", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["external-servings"] }),
  });
}

export function useUnregisterExternalServing() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (registrationId: string) =>
      apiFetch(`/api/model-deployments/external/register/${registrationId}`, { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["external-servings"] }),
  });
}
```

(`useQuery`, `useMutation`, `useQueryClient`, `apiFetch` are already imported at the top of this file — verify, don't re-import.)

- [ ] **Step 2: Type check**

Run: `cd /Users/wongibaek/Documents/litellm-ops/frontend && npx tsc --noEmit`
Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
cd /Users/wongibaek/Documents/litellm-ops
git add frontend/src/hooks/use-api.ts
git commit -m "feat(frontend): external servings query + register/unregister mutation hooks"
```

---

### Task 8: Frontend UI — merged list, badges, register dialog, i18n

**Files:**
- Create: `frontend/src/components/external-serving-register-dialog.tsx`
- Modify: `frontend/src/app/(app)/admin/deployments/page.tsx`
- Modify: `frontend/messages/en.json`, `frontend/messages/ko.json`

**Interfaces:**
- Consumes: hooks/types from Task 7; shadcn `ui/dialog`, `ui/input`, `ui/label`, `ui/button` (all exist under `frontend/src/components/ui/` — verify filenames before importing).

- [ ] **Step 1: Add i18n keys**

In `frontend/messages/en.json`, inside the existing `"adminDeployments": { ... }` object, add:

```json
"colCluster": "Cluster",
"externalBadge": "External",
"externalHint": "Discovered from cluster scan — read-only",
"scanErrors": "Some clusters could not be scanned: {clusters}",
"register": "Register",
"registered": "Registered",
"unregister": "Unregister",
"registerDialogTitle": "Register with LiteLLM",
"registerDialogDesc": "Route traffic to this external serving through the LiteLLM proxy.",
"fieldModelName": "Model name (LiteLLM alias)",
"fieldServedModelName": "Served model name (as reported by the server)",
"fieldApiBase": "API base URL",
"fieldApiBasePlaceholder": "https://my-serving.example.com",
"fieldApiKey": "API key (optional)",
"cancel": "Cancel",
"submitRegister": "Register",
"registerFailed": "Registration failed: {message}"
```

In `frontend/messages/ko.json`, same keys inside `"adminDeployments"`:

```json
"colCluster": "클러스터",
"externalBadge": "External",
"externalHint": "클러스터 스캔으로 발견됨 — 조회 전용",
"scanErrors": "일부 클러스터를 스캔하지 못했습니다: {clusters}",
"register": "등록",
"registered": "등록됨",
"unregister": "등록 해제",
"registerDialogTitle": "LiteLLM에 등록",
"registerDialogDesc": "이 외부 서빙을 LiteLLM 프록시 경유로 호출할 수 있게 등록합니다.",
"fieldModelName": "모델 이름 (LiteLLM 별칭)",
"fieldServedModelName": "서빙 모델 이름 (서버가 보고하는 이름)",
"fieldApiBase": "API Base URL",
"fieldApiBasePlaceholder": "https://my-serving.example.com",
"fieldApiKey": "API 키 (선택)",
"cancel": "취소",
"submitRegister": "등록",
"registerFailed": "등록 실패: {message}"
```

- [ ] **Step 2: Create the register dialog component**

Create `frontend/src/components/external-serving-register-dialog.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";

import { ExternalServing, useRegisterExternalServing } from "@/hooks/use-api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/** Registration dialog for a discovered external serving.
 *  model_name defaults to the basename of the discovered --model value;
 *  served_model_name defaults to the full value (vLLM's default served name). */
export function ExternalServingRegisterDialog({
  serving,
  onClose,
}: {
  serving: ExternalServing | null;
  onClose: () => void;
}) {
  const t = useTranslations("adminDeployments");
  const register = useRegisterExternalServing();

  const [modelName, setModelName] = useState("");
  const [servedModelName, setServedModelName] = useState("");
  const [apiBase, setApiBase] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!serving) return;
    const path = serving.model_path;
    setModelName(path ? path.split("/").filter(Boolean).pop() ?? serving.deployment_name : serving.deployment_name);
    setServedModelName(path ?? serving.deployment_name);
    setApiBase("");
    setApiKey("");
    setError(null);
  }, [serving]);

  const submit = () => {
    if (!serving) return;
    setError(null);
    register.mutate(
      {
        cluster_id: serving.cluster_id,
        namespace: serving.namespace,
        deployment_name: serving.deployment_name,
        model_name: modelName,
        served_model_name: servedModelName,
        api_base: apiBase,
        ...(apiKey ? { api_key: apiKey } : {}),
      },
      {
        onSuccess: () => onClose(),
        onError: (e: Error) => setError(t("registerFailed", { message: e.message })),
      },
    );
  };

  return (
    <Dialog open={!!serving} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("registerDialogTitle")}</DialogTitle>
          <DialogDescription>{t("registerDialogDesc")}</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="ext-model-name">{t("fieldModelName")}</Label>
            <Input id="ext-model-name" value={modelName} onChange={(e) => setModelName(e.target.value)} />
          </div>
          <div className="space-y-2">
            <Label htmlFor="ext-served-name">{t("fieldServedModelName")}</Label>
            <Input id="ext-served-name" value={servedModelName} onChange={(e) => setServedModelName(e.target.value)} />
          </div>
          <div className="space-y-2">
            <Label htmlFor="ext-api-base">{t("fieldApiBase")}</Label>
            <Input
              id="ext-api-base"
              value={apiBase}
              placeholder={t("fieldApiBasePlaceholder")}
              onChange={(e) => setApiBase(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="ext-api-key">{t("fieldApiKey")}</Label>
            <Input id="ext-api-key" value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>{t("cancel")}</Button>
          <Button onClick={submit} disabled={!modelName || !servedModelName || !apiBase || register.isPending}>
            {register.isPending && <Loader2 className="size-4 animate-spin" />}
            {t("submitRegister")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 3: Merge external servings into the deployments page**

Replace the body of `frontend/src/app/(app)/admin/deployments/page.tsx` with:

```tsx
"use client";

import { useState } from "react";
import Link from "next/link";
import { AlertTriangle, CheckCircle2, ChevronRight, Loader2, Server } from "lucide-react";
import { useTranslations } from "next-intl";

import {
  ExternalServing,
  useExternalServings,
  useModelDeployments,
  useUnregisterExternalServing,
} from "@/hooks/use-api";
import { ExternalServingRegisterDialog } from "@/components/external-serving-register-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

/** Status → badge style. Ready=green, Failed/Missing=red, others=neutral. */
function StatusBadge({ status }: { status: string }) {
  const variant =
    status === "Ready" ? "default" : status === "Failed" || status === "Missing" ? "destructive" : "secondary";
  return <Badge variant={variant}>{status}</Badge>;
}

function externalKey(s: ExternalServing) {
  return `${s.cluster_id ?? "default"}/${s.namespace}/${s.deployment_name}`;
}

export default function DeploymentsPage() {
  const t = useTranslations("adminDeployments");
  const { data: deployments, isLoading } = useModelDeployments();
  const { data: external, isLoading: externalLoading } = useExternalServings();
  const unregister = useUnregisterExternalServing();

  const [registerTarget, setRegisterTarget] = useState<ExternalServing | null>(null);
  const [expandedKey, setExpandedKey] = useState<string | null>(null);

  const loading = isLoading || externalLoading;
  const servings = external?.servings ?? [];
  const scanErrors = external?.errors ?? [];
  const isEmpty = (!deployments || deployments.length === 0) && servings.length === 0;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2"><Server className="size-5" />{t("pageTitle")}</h1>
        <p className="text-muted-foreground mt-1">{t("pageDescription")}</p>
      </div>

      {scanErrors.length > 0 && (
        <div className="flex items-center gap-2 rounded-md border border-yellow-300 bg-yellow-50 dark:bg-yellow-950/30 px-4 py-3 text-sm">
          <AlertTriangle className="size-4 shrink-0 text-yellow-600" />
          <span>{t("scanErrors", { clusters: scanErrors.map((e) => e.cluster).join(", ") })}</span>
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("listTitle")}</CardTitle>
          <CardDescription>{t("listHint")}</CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="flex justify-center py-12"><Loader2 className="size-5 animate-spin text-muted-foreground" /></div>
          ) : isEmpty ? (
            <div className="flex flex-col items-center justify-center rounded-lg border border-dashed py-16">
              <Server className="size-10 text-muted-foreground mb-3" />
              <p className="text-muted-foreground">{t("empty")}</p>
            </div>
          ) : (
            <div className="rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("colModel")}</TableHead>
                    <TableHead>{t("colStatus")}</TableHead>
                    <TableHead className="text-right">{t("colReplicas")}</TableHead>
                    <TableHead>{t("colCluster")}</TableHead>
                    <TableHead>{t("colNamespace")}</TableHead>
                    <TableHead>{t("colImage")}</TableHead>
                    <TableHead>{t("colRegistered")}</TableHead>
                    <TableHead></TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(deployments ?? []).map((d) => (
                    <TableRow
                      key={d.id}
                      className="cursor-pointer hover:bg-muted/50"
                      onClick={() => { window.location.href = `/admin/deployments/${d.id}`; }}
                    >
                      <TableCell className="font-medium">
                        <Link
                          href={`/admin/deployments/${d.id}`}
                          className="hover:underline"
                          onClick={(e) => e.stopPropagation()}
                        >
                          {d.model_name}
                        </Link>
                      </TableCell>
                      <TableCell><StatusBadge status={d.status} /></TableCell>
                      <TableCell className="text-right tabular-nums">{d.ready_replicas}/{d.replicas}</TableCell>
                      <TableCell className="text-muted-foreground text-xs">—</TableCell>
                      <TableCell className="font-mono text-xs">{d.namespace}</TableCell>
                      <TableCell className="font-mono text-xs max-w-[200px] truncate" title={d.image}>{d.image}</TableCell>
                      <TableCell>
                        {d.litellm_model_id ? (
                          <CheckCircle2 className="size-4 text-green-600" />
                        ) : (
                          <span className="text-xs text-muted-foreground">—</span>
                        )}
                      </TableCell>
                      <TableCell><ChevronRight className="size-4 text-muted-foreground" /></TableCell>
                    </TableRow>
                  ))}
                  {servings.map((s) => {
                    const key = externalKey(s);
                    const expanded = expandedKey === key;
                    return (
                      <>
                        <TableRow
                          key={key}
                          className="cursor-pointer hover:bg-muted/50"
                          onClick={() => setExpandedKey(expanded ? null : key)}
                        >
                          <TableCell className="font-medium">
                            <span className="flex items-center gap-2">
                              {s.registration?.model_name ?? s.deployment_name}
                              <Badge variant="outline">{t("externalBadge")}</Badge>
                              <Badge variant="secondary">{s.engine}</Badge>
                            </span>
                          </TableCell>
                          <TableCell><StatusBadge status={s.status} /></TableCell>
                          <TableCell className="text-right tabular-nums">{s.ready_replicas}/{s.replicas}</TableCell>
                          <TableCell className="font-mono text-xs">{s.cluster_name}</TableCell>
                          <TableCell className="font-mono text-xs">{s.namespace}</TableCell>
                          <TableCell className="font-mono text-xs max-w-[200px] truncate" title={s.image}>{s.image}</TableCell>
                          <TableCell>
                            {s.registration ? (
                              <span className="flex items-center gap-2">
                                <CheckCircle2 className="size-4 text-green-600" />
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  className="h-7 text-xs"
                                  disabled={unregister.isPending}
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    unregister.mutate(s.registration!.id);
                                  }}
                                >
                                  {t("unregister")}
                                </Button>
                              </span>
                            ) : (
                              <Button
                                variant="outline"
                                size="sm"
                                className="h-7 text-xs"
                                onClick={(e) => { e.stopPropagation(); setRegisterTarget(s); }}
                              >
                                {t("register")}
                              </Button>
                            )}
                          </TableCell>
                          <TableCell />
                        </TableRow>
                        {expanded && (
                          <TableRow key={`${key}-detail`} className="bg-muted/30 hover:bg-muted/30">
                            <TableCell colSpan={8}>
                              <div className="space-y-1 py-2 text-xs font-mono">
                                <p className="text-muted-foreground font-sans">{t("externalHint")}</p>
                                {s.model_path && <p>--model: {s.model_path}</p>}
                                {s.args.length > 0 && <p>args: {s.args.join(" ")}</p>}
                                {Object.keys(s.labels).length > 0 && (
                                  <p>labels: {Object.entries(s.labels).map(([k, v]) => `${k}=${v}`).join(", ")}</p>
                                )}
                                {s.registration && <p>api_base: {s.registration.api_base}</p>}
                              </div>
                            </TableCell>
                          </TableRow>
                        )}
                      </>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <ExternalServingRegisterDialog serving={registerTarget} onClose={() => setRegisterTarget(null)} />
    </div>
  );
}
```

Note: the fragment inside `servings.map` needs a keyed fragment — if `tsc`/eslint complains about the bare `<>`, import `Fragment` from `react` and use `<Fragment key={key}>` (then remove `key` from the inner rows).

- [ ] **Step 4: Type check and lint**

Run: `cd /Users/wongibaek/Documents/litellm-ops/frontend && npx tsc --noEmit && npm run lint`
Expected: exit 0 (fix any fragment-key or unused-import complaints).

- [ ] **Step 5: Commit**

```bash
cd /Users/wongibaek/Documents/litellm-ops
git add frontend/src/components/external-serving-register-dialog.tsx "frontend/src/app/(app)/admin/deployments/page.tsx" frontend/messages/en.json frontend/messages/ko.json
git commit -m "feat(frontend): show external vLLM/SGLang servings with LiteLLM registration"
```

---

### Task 9: Full verification + deploy to local stack

**Files:** none new.

- [ ] **Step 1: Full backend suite**

Run: `cd /Users/wongibaek/Documents/litellm-ops/backend && .venv/bin/pytest -q`
Expected: all green.

- [ ] **Step 2: Frontend production build**

Run: `cd /Users/wongibaek/Documents/litellm-ops/frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 3: Rebuild the running containers**

```bash
cd /Users/wongibaek/Documents/litellm-ops
docker compose up -d --build backend frontend
```

Wait for `docker compose ps` to show backend healthy.

- [ ] **Step 4: Smoke-check the route exists (not shadowed)**

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8002/api/model-deployments/external
```

Expected: `401` (auth required — proves the route resolves; a `500` would mean it fell through to `/{deployment_id}`). Then verify in the browser: http://localhost:3003/admin/deployments — external servings appear with badges when a kubeconfig is configured; without one, the page renders the portal list normally.

- [ ] **Step 5: Commit any fixes, then wrap up**

If steps 1–4 forced changes, commit them:

```bash
git add -A && git commit -m "fix: verification fixes for external serving discovery"
```

Use superpowers:finishing-a-development-branch to decide merge/PR.
