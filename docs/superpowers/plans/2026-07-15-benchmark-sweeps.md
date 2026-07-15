# Benchmark Sweeps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One submission expands 1–2 `vllm serve` flag variables into ≤12 sequential self-serving benchmark Jobs under a fixed load preset, rendered as an auto-comparison table.

**Architecture:** A new `custom_benchmark_sweep` row owns the grid; each combo is an ordinary `custom_benchmark_run` (status `queued`) with its Job manifest prebuilt at submit (freeze-at-submit). The reconciler promotes the next queued combo only when no combo of that sweep is active, so exactly one GPU set is held at a time. Spec: `docs/superpowers/specs/2026-07-15-benchmark-sweep-design.md`.

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic (backend), K8s Jobs via existing `build_self_serving_bench_job`, Next.js app router + react-query + next-intl (frontend).

## Global Constraints

- Work on branch `feat/benchmark-sweeps` cut from `origin/main` (Task 0). Never commit `frontend/src/app/(app)/admin/requests/page.tsx` (unrelated WIP) or the `litellm` submodule.
- Scope: performance + vLLM only. Runs get `tool="vllm_serving"`, `kind="performance"` hardcoded.
- Caps: 1–2 variables; flag regex `^--[a-z0-9][a-z0-9-]*$`; distinct flags; every value a non-empty scalar; 2 ≤ combos ≤ 12; combo order row-major (first variable varies slowest).
- New run status `queued` = row exists, no Job yet (`k8s_job_name` null until promotion).
- `queued_job_manifest` embeds the bench API key: NEVER serialized by any endpoint; cleared on promotion and on cancel.
- Backend commands run from `backend/`: tests `.venv/bin/python -m pytest <file> -q`, lint `.venv/bin/ruff check <files>`. Frontend from `frontend/`: `npx tsc --noEmit`.
- Every new i18n key goes into BOTH `frontend/messages/en.json` and `frontend/messages/ko.json`.
- Load presets (fixed; common `seed=0`, `ignore_eos=True`):

| key | random_input_len | random_output_len | num_prompts | max_concurrency |
|---|---|---|---|---|
| `chat` | 512 | 256 | 300 | 32 |
| `long_input` | 4096 | 512 | 120 | 8 |
| `long_output` | 256 | 1024 | 200 | 16 |

---

### Task 0: Branch setup

**Files:** none (git only)

- [ ] **Step 1: Create the branch and bring the docs over**

```bash
cd /Users/wongibaek/Documents/litellm-ops
git fetch origin main
git stash push -m "budget-dialog-wip" -- "frontend/src/app/(app)/admin/requests/page.tsx"
git checkout -b feat/benchmark-sweeps origin/main
git cherry-pick f021bbe   # docs(spec): benchmark sweeps
git stash pop             # restore the unrelated WIP file (leave uncommitted)
```

Expected: branch `feat/benchmark-sweeps` at origin/main + 1 spec commit. If the plan doc (committed on `feat/budget-request-duration` after f021bbe) is needed, cherry-pick that commit too — `git log feat/budget-request-duration --oneline -3` to find it.

---

### Task 1: Load presets module

**Files:**
- Create: `backend/app/services/benchmark_presets.py`
- Test: `backend/tests/test_benchmark_presets.py`

**Interfaces:**
- Produces: `LOAD_PRESETS: dict[str, dict]` (3 keys: `chat`, `long_input`, `long_output`); `preset_params(key: str) -> dict` returning preset fields + `{"seed": 0, "ignore_eos": True, "preset": key}`; raises `KeyError` on unknown key. Consumed by Tasks 4 (API) and 7 (via `GET /presets`).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_benchmark_presets.py
"""Fixed load presets are the sweep methodology: stable keys, full expansion."""

import pytest

from app.services.benchmark_presets import LOAD_PRESETS, preset_params


def test_three_presets_with_expected_load_shapes():
    assert set(LOAD_PRESETS) == {"chat", "long_input", "long_output"}
    assert LOAD_PRESETS["chat"] == {
        "random_input_len": 512, "random_output_len": 256,
        "num_prompts": 300, "max_concurrency": 32,
    }
    assert LOAD_PRESETS["long_input"] == {
        "random_input_len": 4096, "random_output_len": 512,
        "num_prompts": 120, "max_concurrency": 8,
    }
    assert LOAD_PRESETS["long_output"] == {
        "random_input_len": 256, "random_output_len": 1024,
        "num_prompts": 200, "max_concurrency": 16,
    }


def test_preset_params_expands_common_fields_and_tags_key():
    p = preset_params("chat")
    assert p["seed"] == 0 and p["ignore_eos"] is True and p["preset"] == "chat"
    assert p["num_prompts"] == 300
    # expansion is a copy — mutating it must not touch the constant
    p["num_prompts"] = 1
    assert LOAD_PRESETS["chat"]["num_prompts"] == 300


def test_unknown_preset_raises():
    with pytest.raises(KeyError):
        preset_params("nope")
```

- [ ] **Step 2: Run it — expect FAIL (module missing)**

Run: `cd backend && .venv/bin/python -m pytest tests/test_benchmark_presets.py -q`
Expected: `ModuleNotFoundError: No module named 'app.services.benchmark_presets'`

- [ ] **Step 3: Implement**

```python
# backend/app/services/benchmark_presets.py
"""Fixed load presets for benchmark sweeps — the portal's measurement methodology.

Every sweep combo runs under exactly one preset so results stay comparable
across runs, models and time. Preset params are copied into run.params at
submit (plus {"preset": key}), so historical runs keep their actual
conditions even if these numbers are retuned later.
"""

LOAD_PRESETS: dict[str, dict] = {
    # short interactive chat
    "chat": {
        "random_input_len": 512, "random_output_len": 256,
        "num_prompts": 300, "max_concurrency": 32,
    },
    # RAG / document summarization
    "long_input": {
        "random_input_len": 4096, "random_output_len": 512,
        "num_prompts": 120, "max_concurrency": 8,
    },
    # generation-heavy
    "long_output": {
        "random_input_len": 256, "random_output_len": 1024,
        "num_prompts": 200, "max_concurrency": 16,
    },
}

_COMMON = {"seed": 0, "ignore_eos": True}


def preset_params(key: str) -> dict:
    """Expanded run params for a preset; raises KeyError on an unknown key."""
    return {**LOAD_PRESETS[key], **_COMMON, "preset": key}
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `cd backend && .venv/bin/python -m pytest tests/test_benchmark_presets.py -q`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/benchmark_presets.py backend/tests/test_benchmark_presets.py
git commit -m "feat(bench): fixed load presets for sweeps (chat / long_input / long_output)"
```

---

### Task 2: Sweep helpers (combo expansion, argv merge, promotion)

**Files:**
- Create: `backend/app/services/benchmark_sweeps.py`
- Test: `backend/tests/test_benchmark_sweep_helpers.py`

**Interfaces:**
- Consumes: `job_name_for(run_id)` from `app.services.benchmark_manifests`.
- Produces (used by Tasks 4 and 6):
  - `expand_combos(variables: list[dict]) -> list[dict]` — `[{"flag": "--a", "values": [1, 2]}]` → `[{"--a": 1}, {"--a": 2}]`, row-major.
  - `merge_serve_argv(argv: list[str], combo: dict) -> list[str]` — replace `--flag value` / `--flag=value`, else append pair; returns a new list.
  - `async promote_queued_run(k8s, run) -> None` — `create_job(run.k8s_namespace, run.queued_job_manifest)`, then sets `run.k8s_job_name = job_name_for(run.id)`, `run.status = "pending"`, `run.queued_job_manifest = None`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_benchmark_sweep_helpers.py
"""Sweep grid expansion, serve-argv merge, queued-run promotion."""

import types
import uuid
from unittest.mock import AsyncMock, MagicMock

from app.services.benchmark_manifests import job_name_for
from app.services.benchmark_sweeps import expand_combos, merge_serve_argv, promote_queued_run


def test_expand_combos_row_major_first_variable_slowest():
    combos = expand_combos([
        {"flag": "--max-num-seqs", "values": [128, 256]},
        {"flag": "--gpu-memory-utilization", "values": [0.9, 0.95]},
    ])
    assert combos == [
        {"--max-num-seqs": 128, "--gpu-memory-utilization": 0.9},
        {"--max-num-seqs": 128, "--gpu-memory-utilization": 0.95},
        {"--max-num-seqs": 256, "--gpu-memory-utilization": 0.9},
        {"--max-num-seqs": 256, "--gpu-memory-utilization": 0.95},
    ]


def test_expand_single_variable():
    assert expand_combos([{"flag": "--a", "values": ["x"]}]) == [{"--a": "x"}]


def test_merge_replaces_space_and_equals_forms_and_appends_new():
    argv = ["vllm", "serve", "/m", "--max-num-seqs", "64", "--dtype=float16"]
    out = merge_serve_argv(argv, {"--max-num-seqs": 256, "--dtype": "bfloat16", "--kv-cache-dtype": "fp8"})
    assert out == [
        "vllm", "serve", "/m", "--max-num-seqs", "256", "--dtype=bfloat16",
        "--kv-cache-dtype", "fp8",
    ]
    assert argv[4] == "64"  # input untouched


async def test_promote_creates_job_and_clears_manifest():
    run = types.SimpleNamespace(
        id=uuid.uuid4(), k8s_namespace="bench", status="queued",
        k8s_job_name=None, queued_job_manifest={"kind": "Job"},
    )
    k8s = MagicMock()
    k8s.create_job = AsyncMock()
    await promote_queued_run(k8s, run)
    k8s.create_job.assert_awaited_once_with("bench", {"kind": "Job"})
    assert run.status == "pending"
    assert run.k8s_job_name == job_name_for(run.id)
    assert run.queued_job_manifest is None
```

- [ ] **Step 2: Run — expect FAIL (module missing)**

Run: `cd backend && .venv/bin/python -m pytest tests/test_benchmark_sweep_helpers.py -q`
Expected: `ModuleNotFoundError: No module named 'app.services.benchmark_sweeps'`

- [ ] **Step 3: Implement**

```python
# backend/app/services/benchmark_sweeps.py
"""Pure helpers for benchmark sweeps: grid expansion, serve-argv merge and
queued-run promotion. Shared by the sweeps API and the reconciler."""

from __future__ import annotations

from itertools import product

from app.services.benchmark_manifests import job_name_for


def expand_combos(variables: list[dict]) -> list[dict]:
    """Cartesian product of the variables' value lists, row-major (the first
    variable varies slowest). Each combo maps flag -> value."""
    flags = [v["flag"] for v in variables]
    return [dict(zip(flags, vals)) for vals in product(*(v["values"] for v in variables))]


def merge_serve_argv(argv: list, combo: dict) -> list:
    """Merge combo flags into a CLI token list (a full serve argv or a bare
    extra-args list): an existing ``--flag value`` or ``--flag=value`` is
    replaced in place, otherwise the pair is appended. Returns a new list."""
    out = list(argv)
    for flag, value in combo.items():
        for i, tok in enumerate(out):
            if tok == flag and i + 1 < len(out):
                out[i + 1] = str(value)
                break
            if isinstance(tok, str) and tok.startswith(flag + "="):
                out[i] = f"{flag}={value}"
                break
        else:
            out += [flag, str(value)]
    return out


async def promote_queued_run(k8s, run) -> None:
    """Create the prebuilt Job for a queued sweep combo and flip it to pending.
    The stored manifest is cleared — it embeds the bench API key."""
    await k8s.create_job(run.k8s_namespace, run.queued_job_manifest)
    run.k8s_job_name = job_name_for(run.id)
    run.status = "pending"
    run.queued_job_manifest = None
```

- [ ] **Step 4: Run — expect PASS**

Run: `cd backend && .venv/bin/python -m pytest tests/test_benchmark_sweep_helpers.py -q`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/benchmark_sweeps.py backend/tests/test_benchmark_sweep_helpers.py
git commit -m "feat(bench): sweep combo expansion, serve-argv merge, queued-run promotion"
```

---

### Task 3: DB model + run columns + migration 041

**Files:**
- Create: `backend/app/db/models/custom_benchmark_sweep.py`
- Modify: `backend/app/db/models/custom_benchmark_run.py` (add 4 columns after `serving_torn_down`, before `status`)
- Create: `backend/migrations/versions/041_benchmark_sweeps.py`

**Interfaces:**
- Produces: `CustomBenchmarkSweep` (fields listed below) and run columns `sweep_id: UUID|None`, `sweep_index: int|None`, `sweep_combo: dict|None`, `queued_job_manifest: dict|None`. Consumed by Tasks 4–6.
- NOTE: `app/db/models/__init__.py` is NOT touched — `custom_benchmark_run` isn't registered there either; API modules import models directly (existing pattern).

- [ ] **Step 1: Create the sweep model**

```python
# backend/app/db/models/custom_benchmark_sweep.py
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import CustomBase


class CustomBenchmarkSweep(CustomBase):
    """One benchmark sweep: a grid of `vllm serve` flag combos over a fixed
    load preset, executed as sequential self-serving Jobs (one combo at a
    time). The sweep row owns grouping/ordering only — each combo is an
    ordinary CustomBenchmarkRun linked via run.sweep_id + run.sweep_index."""

    __tablename__ = "custom_benchmark_sweep"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # Base target: exactly one of deployment_id (portal template; plain UUID
    # like the run column) or external_source ({cluster_id, namespace,
    # deployment_name}) is set.
    deployment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    external_source: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    cluster_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("custom_k8s_cluster.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    k8s_namespace: Mapped[str] = mapped_column(String(128), nullable=False)
    preset: Mapped[str] = mapped_column(String(32), nullable=False)
    variables: Mapped[list] = mapped_column(JSONB, nullable=False)
    serving_overrides: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="running", server_default="running", index=True
    )
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 2: Add the run columns**

In `backend/app/db/models/custom_benchmark_run.py`, add `Integer` and `ForeignKey` are already imported (`ForeignKey` yes, `Integer` no — extend the import line `from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func` to include `Integer`). Insert after the `serving_torn_down` column:

```python
    # Sweep membership: a sweep's combos are ordinary runs ordered by
    # sweep_index; sweep_combo holds the flag->value map for display.
    sweep_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("custom_benchmark_sweep.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    sweep_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sweep_combo: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Job manifest prebuilt at submit (freeze-at-submit), created on promotion
    # then cleared. Embeds the bench API key — NEVER serialized by the API.
    queued_job_manifest: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
```

- [ ] **Step 3: Write migration 041**

```python
# backend/migrations/versions/041_benchmark_sweeps.py
"""Benchmark sweeps: sweep table + sweep linkage columns on runs.

A sweep expands 1-2 serve-flag variables into sequential self-serving
benchmark Jobs; runs carry their prebuilt manifest while status=queued.

Revision ID: 041_benchmark_sweeps
Revises: 040_budget_request_duration
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "041_benchmark_sweeps"
down_revision = "040_budget_request_duration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_benchmark_sweep",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(256), nullable=True),
        sa.Column("deployment_id", UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("external_source", JSONB(), nullable=True),
        sa.Column(
            "cluster_id",
            UUID(as_uuid=True),
            sa.ForeignKey("custom_k8s_cluster.id", ondelete="RESTRICT"),
            nullable=True,
            index=True,
        ),
        sa.Column("k8s_namespace", sa.String(128), nullable=False),
        sa.Column("preset", sa.String(32), nullable=False),
        sa.Column("variables", JSONB(), nullable=False),
        sa.Column("serving_overrides", JSONB(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="running", index=True),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "custom_benchmark_run",
        sa.Column(
            "sweep_id",
            UUID(as_uuid=True),
            sa.ForeignKey("custom_benchmark_sweep.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_custom_benchmark_run_sweep_id", "custom_benchmark_run", ["sweep_id"])
    op.add_column("custom_benchmark_run", sa.Column("sweep_index", sa.Integer(), nullable=True))
    op.add_column("custom_benchmark_run", sa.Column("sweep_combo", JSONB(), nullable=True))
    op.add_column("custom_benchmark_run", sa.Column("queued_job_manifest", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("custom_benchmark_run", "queued_job_manifest")
    op.drop_column("custom_benchmark_run", "sweep_combo")
    op.drop_column("custom_benchmark_run", "sweep_index")
    op.drop_index("ix_custom_benchmark_run_sweep_id", "custom_benchmark_run")
    op.drop_column("custom_benchmark_run", "sweep_id")
    op.drop_table("custom_benchmark_sweep")
```

- [ ] **Step 4: Verify nothing broke**

Run: `cd backend && .venv/bin/python -c "from app.db.models.custom_benchmark_sweep import CustomBenchmarkSweep; from app.db.models.custom_benchmark_run import CustomBenchmarkRun; print('ok')" && .venv/bin/python -m pytest tests -q 2>&1 | tail -2 && .venv/bin/ruff check app/db migrations/versions/041_benchmark_sweeps.py`
Expected: `ok`, pytest summary with no NEW failures vs the pre-task run, ruff `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add backend/app/db/models/custom_benchmark_sweep.py backend/app/db/models/custom_benchmark_run.py backend/migrations/versions/041_benchmark_sweeps.py
git commit -m "feat(db): benchmark sweep table + sweep linkage columns on runs (041)"
```

---

### Task 4: Sweep API — presets endpoint + create

**Files:**
- Create: `backend/app/api/benchmark_sweeps.py`
- Modify: `backend/app/main.py` (register router BEFORE `benchmarks.router`)
- Modify: `backend/app/api/benchmarks.py::_serialize` (add 3 sweep fields)
- Test: `backend/tests/test_benchmark_sweeps_api.py`

**Interfaces:**
- Consumes (all existing): from `app.api.benchmarks`: `ExternalTarget`, `_serialize`, `_serving_snapshot`, `DEFAULT_BENCH_NAMESPACE`; from `app.services.benchmark_serving`: `build_ephemeral_deployment`, `build_external_clone`, `ephemeral_model_name`, `external_bench_facts`, `serving_cli`, `_clone_target_port`; from `app.services.benchmark_manifests`: `build_self_serving_bench_job`, `job_name_for`; from `app.services.model_deployment_manifests`: `VLLM_PORT`, `build_deployment`, `serving_api_key`; Tasks 1–3 artifacts.
- Produces: `GET /api/benchmarks/presets` → `{"presets": LOAD_PRESETS}`; `POST /api/benchmarks/sweeps` → 201 sweep dict incl. `"runs"`; `_serialize_sweep(sweep, *, progress=None, runs=None) -> dict` (Task 5 adds more routes to this file). Run serialization gains `sweep_id`, `sweep_index`, `sweep_combo` (never `queued_job_manifest`).

- [ ] **Step 1: Extend `_serialize` in `backend/app/api/benchmarks.py`**

Add to the returned dict (after `"serving_torn_down": r.serving_torn_down,`):

```python
        "sweep_id": str(r.sweep_id) if r.sweep_id else None,
        "sweep_index": r.sweep_index,
        "sweep_combo": r.sweep_combo,
```

- [ ] **Step 2: Write the failing tests**

```python
# backend/tests/test_benchmark_sweeps_api.py
"""Sweep create: validation, grid expansion into queued runs, combo #0 promotion."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.benchmark_manifests import job_name_for


def _template_deployment():
    base = MagicMock()
    base.id = uuid.uuid4()
    base.model_name = "m"
    base.image = "vllm/vllm-openai:v0.6.0"
    base.model_path = "/models/m"
    base.replicas = 1
    base.gpu_count = 1
    base.gpu_resource_key = "nvidia.com/gpu"
    base.cpu_request = None
    base.cpu_limit = None
    base.memory_request = None
    base.memory_limit = None
    base.node_selector = {}
    base.tolerations = None
    base.pvc_name = "w"
    base.pvc_mount_path = "/models"
    base.vllm_extra_args = ["--max-num-seqs", "64"]
    base.env = {}
    return base


def _sweep_body(**kw):
    body = {
        "deployment_id": str(uuid.uuid4()),
        "preset": "chat",
        "variables": [{"flag": "--max-num-seqs", "values": [128, 256]}],
    }
    body.update(kw)
    return body


async def _post(client_for_user, super_user, body):
    async with client_for_user(super_user) as client:
        return await client.post("/api/benchmarks/sweeps", json=body)


async def test_presets_endpoint_lists_the_methodology(client_for_user, super_user):
    async with client_for_user(super_user) as client:
        resp = await client.get("/api/benchmarks/presets")
    assert resp.status_code == 200
    assert set(resp.json()["presets"]) == {"chat", "long_input", "long_output"}


async def test_create_rejects_bad_input(client_for_user, super_user, mock_db):
    checks = [
        (_sweep_body(preset="nope"), "preset"),
        (_sweep_body(variables=[]), "variable"),
        (_sweep_body(variables=[{"flag": "--a", "values": [1]}] * 3), "variable"),
        (_sweep_body(variables=[{"flag": "MaxSeqs", "values": [1, 2]}]), "flag"),
        (_sweep_body(variables=[{"flag": "--a", "values": [1]}]), "combos"),  # 1 combo < 2
        (_sweep_body(variables=[{"flag": "--a", "values": [1, 2, 3, 4]},
                                {"flag": "--b", "values": [1, 2, 3, 4]}]), "combos"),  # 16 > 12
        (_sweep_body(deployment_id=None), "deployment_id or external_target"),
    ]
    for body, needle in checks:
        resp = await _post(client_for_user, super_user, body)
        assert resp.status_code == 400, (body, resp.text)
        assert needle in resp.json()["detail"]


async def test_create_expands_grid_queues_all_and_promotes_first(
    client_for_user, super_user, mock_db
):
    base = _template_deployment()
    result = MagicMock()
    result.scalar_one_or_none.return_value = base
    mock_db.execute = AsyncMock(return_value=result)
    fake_k8s = MagicMock()
    fake_k8s.create_job = AsyncMock()
    body = _sweep_body(deployment_id=str(base.id))
    with patch("app.api.benchmark_sweeps.k8s_for_cluster", AsyncMock(return_value=fake_k8s)):
        resp = await _post(client_for_user, super_user, body)
    assert resp.status_code == 201, resp.text

    added = [c.args[0] for c in mock_db.add.call_args_list]
    sweeps = [o for o in added if getattr(o, "preset", None)]
    runs = sorted(
        (o for o in added if getattr(o, "sweep_index", None) is not None or getattr(o, "sweep_combo", None)),
        key=lambda r: r.sweep_index,
    )
    assert len(sweeps) == 1 and len(runs) == 2
    sweep = sweeps[0]
    assert sweep.status == "running" and sweep.preset == "chat"

    # combo #0 promoted (single Job created), combo #1 still queued with manifest
    assert fake_k8s.create_job.await_count == 1
    first, second = runs
    assert first.status == "pending" and first.k8s_job_name == job_name_for(first.id)
    assert first.queued_job_manifest is None
    assert second.status == "queued" and second.k8s_job_name is None
    assert second.queued_job_manifest["kind"] == "Job"

    # per-combo snapshot carries the MERGED args; params carry the preset
    assert second.serving_snapshot["vllm_extra_args"][-2:] == ["--max-num-seqs", "256"]
    assert first.serving_snapshot["vllm_extra_args"][-2:] == ["--max-num-seqs", "128"]
    for r in runs:
        assert r.params["preset"] == "chat" and r.params["num_prompts"] == 300
        assert r.tool == "vllm_serving" and r.kind == "performance"
        assert r.ephemeral is True and r.serving_torn_down is True

    # the API response never leaks the stored manifest
    payload = resp.json()
    assert "queued_job_manifest" not in str(payload)
    assert len(payload["runs"]) == 2
```

- [ ] **Step 3: Run — expect FAIL (404, router missing)**

Run: `cd backend && .venv/bin/python -m pytest tests/test_benchmark_sweeps_api.py -q`
Expected: FAIL (`assert resp.status_code == 200/201` → 404)

- [ ] **Step 4: Implement the API module**

```python
# backend/app/api/benchmark_sweeps.py
"""Admin endpoints for benchmark sweeps: one submission expands 1-2 serve-flag
variables into sequential self-serving benchmark Jobs under a fixed load
preset. Combos are ordinary CustomBenchmarkRun rows (status `queued`) whose
Job manifests are prebuilt at submit (freeze-at-submit); the reconciler
promotes the next combo when the previous one reaches a terminal state."""

import logging
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_super_user
from app.clients.k8s import K8sNotConfigured
from app.db.models.custom_benchmark_run import CustomBenchmarkRun
from app.db.models.custom_benchmark_sweep import CustomBenchmarkSweep
from app.db.models.custom_model_deployment import CustomModelDeployment
from app.db.models.custom_user import CustomUser
from app.db.session import get_db
from app.api.benchmarks import (
    DEFAULT_BENCH_NAMESPACE,
    ExternalTarget,
    _serialize,
    _serving_snapshot,
)
from app.services.benchmark_manifests import build_self_serving_bench_job
from app.services.benchmark_presets import LOAD_PRESETS, preset_params
from app.services.benchmark_serving import (
    _clone_target_port,
    build_ephemeral_deployment,
    build_external_clone,
    ephemeral_model_name,
    external_bench_facts,
    serving_cli,
)
from app.services.benchmark_sweeps import expand_combos, merge_serve_argv, promote_queued_run
from app.services.clusters import k8s_for_cluster
from app.services.model_deployment_manifests import VLLM_PORT, build_deployment, serving_api_key

logger = logging.getLogger(__name__)

# Same prefix as the runs router; registered BEFORE it in main.py so /presets
# and /sweeps* match ahead of the GET /{run_id} catch-all.
router = APIRouter(prefix="/api/benchmarks", tags=["benchmark-sweeps"])

_FLAG_RE = re.compile(r"^--[a-z0-9][a-z0-9-]*$")
SWEEP_TERMINAL = ("succeeded", "failed", "cancelled")


class SweepVariable(BaseModel):
    flag: str
    values: list[int | float | str] = Field(..., min_length=1)


class CreateSweepRequest(BaseModel):
    name: str | None = None
    deployment_id: str | None = None
    external_target: ExternalTarget | None = None
    cluster_id: str | None = None
    namespace: str | None = None
    preset: str
    variables: list[SweepVariable]
    serving_overrides: dict | None = None
    api_key: str | None = None


def _serialize_sweep(s: CustomBenchmarkSweep, *, progress: dict | None = None, runs: list | None = None) -> dict:
    out = {
        "id": str(s.id),
        "name": s.name,
        "deployment_id": str(s.deployment_id) if s.deployment_id else None,
        "external_source": s.external_source,
        "cluster_id": str(s.cluster_id) if s.cluster_id else None,
        "k8s_namespace": s.k8s_namespace,
        "preset": s.preset,
        "variables": s.variables,
        "serving_overrides": s.serving_overrides,
        "status": s.status,
        "created_by": s.created_by,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "finished_at": s.finished_at.isoformat() if s.finished_at else None,
    }
    if progress is not None:
        out["progress"] = progress
    if runs is not None:
        out["runs"] = runs
    return out


def _validated_combos(body: CreateSweepRequest) -> list[dict]:
    if body.preset not in LOAD_PRESETS:
        raise HTTPException(status_code=400, detail=f"Unknown preset '{body.preset}'")
    if not 1 <= len(body.variables) <= 2:
        raise HTTPException(status_code=400, detail="1-2 sweep variables required")
    flags = [v.flag for v in body.variables]
    if len(set(flags)) != len(flags):
        raise HTTPException(status_code=400, detail="Sweep variable flags must be distinct")
    for v in body.variables:
        if not _FLAG_RE.match(v.flag):
            raise HTTPException(
                status_code=400, detail=f"Invalid flag '{v.flag}' (expected --lower-kebab-case)"
            )
    combos = expand_combos([v.model_dump() for v in body.variables])
    if not 2 <= len(combos) <= 12:
        raise HTTPException(status_code=400, detail=f"Sweep must expand to 2-12 combos (got {len(combos)})")
    if bool(body.deployment_id) == bool(body.external_target):
        raise HTTPException(status_code=400, detail="Exactly one of deployment_id or external_target is required")
    return combos


@router.get("/presets")
async def list_presets(user: CustomUser = Depends(require_super_user)) -> dict:
    """The fixed load presets — the portal's benchmark methodology."""
    return {"presets": LOAD_PRESETS}


@router.post("/sweeps", status_code=status.HTTP_201_CREATED)
async def create_sweep(
    body: CreateSweepRequest,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    combos = _validated_combos(body)
    params = preset_params(body.preset)

    if body.external_target:
        ext = body.external_target
        cluster_uuid = uuid.UUID(ext.cluster_id) if ext.cluster_id else None
        namespace = ext.namespace
        k8s = await k8s_for_cluster(db, cluster_uuid)
        try:
            spec = await k8s.read_deployment(ext.namespace, ext.deployment_name)
        except K8sNotConfigured as e:
            raise HTTPException(status_code=503, detail=str(e))
        except Exception:
            logger.exception("Live spec read failed for %s/%s", ext.namespace, ext.deployment_name)
            raise HTTPException(status_code=502, detail="Failed to read the external serving's spec; check logs")
        if spec is None:
            raise HTTPException(status_code=404, detail="External serving no longer exists")
        try:
            facts = external_bench_facts(spec)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        base_argv = serving_cli(spec["container"])
        port = _clone_target_port(spec["container"])
        env_map = {e["name"]: e["value"] for e in spec["container"]["env"] if e.get("value")}
        model_name = facts["served_model"]
        params = {**params, "tokenizer": facts["tokenizer"]}
        external_source = {
            "cluster_id": ext.cluster_id,
            "namespace": ext.namespace,
            "deployment_name": ext.deployment_name,
        }
        deployment_uuid = None
        base = None
    else:
        try:
            tmpl_id = uuid.UUID(body.deployment_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid deployment_id")
        base = (
            await db.execute(select(CustomModelDeployment).where(CustomModelDeployment.id == tmpl_id))
        ).scalar_one_or_none()
        if not base:
            raise HTTPException(status_code=404, detail="Template deployment not found")
        cluster_uuid = uuid.UUID(body.cluster_id) if body.cluster_id else None
        namespace = body.namespace or DEFAULT_BENCH_NAMESPACE
        model_name = base.model_name
        external_source = None
        deployment_uuid = base.id

    sweep = CustomBenchmarkSweep(
        id=uuid.uuid4(),
        name=(body.name or "").strip() or None,
        deployment_id=deployment_uuid,
        external_source=external_source,
        cluster_id=cluster_uuid,
        k8s_namespace=namespace,
        preset=body.preset,
        variables=[v.model_dump() for v in body.variables],
        serving_overrides=body.serving_overrides,
        status="running",
        created_by=user.user_id,
    )
    db.add(sweep)

    runs: list[CustomBenchmarkRun] = []
    for idx, combo in enumerate(combos):
        run = CustomBenchmarkRun(
            id=uuid.uuid4(),
            model_name=model_name,
            tool="vllm_serving",
            kind="performance",
            params=params,
            status="queued",
            cluster_id=cluster_uuid,
            k8s_namespace=namespace,
            deployment_id=deployment_uuid,
            ephemeral=True,
            serving_torn_down=True,  # single self-serving Job; nothing separate to tear down
            created_by=user.user_id,
            sweep_id=sweep.id,
            sweep_index=idx,
            sweep_combo=combo,
        )
        if body.external_target:
            serve_argv = merge_serve_argv(base_argv, combo)
            clone = build_external_clone(
                spec, name=ephemeral_model_name(run.id), overrides=body.serving_overrides
            )[0]
            run.bench_image = spec["container"]["image"]
            run.serving_snapshot = {
                "source": "external",
                "image": spec["container"]["image"],
                "vllm_extra_args": serve_argv,
                "env": env_map,
                "model_path": facts["served_model"],
                "pvc_name": facts["pvc_name"],
                "pvc_mount_path": facts["pvc_mount_path"],
            }
            api_key = body.api_key or serving_api_key(serve_argv, env_map)
            run.queued_job_manifest = build_self_serving_bench_job(
                run,
                serving_deployment=clone,
                serve_argv=serve_argv,
                port=port,
                api_key=api_key,
                served_model=facts["served_model"],
                tokenizer=facts["tokenizer"],
            )
        else:
            eph = build_ephemeral_deployment(
                base, name=ephemeral_model_name(run.id), namespace=namespace, overrides=body.serving_overrides
            )
            eph.vllm_extra_args = merge_serve_argv(list(eph.vllm_extra_args or []), combo)
            run.bench_image = eph.image
            run.serving_snapshot = _serving_snapshot(eph)
            serve_argv = ["vllm", "serve", eph.model_path, "--port", str(VLLM_PORT), *(eph.vllm_extra_args or [])]
            api_key = body.api_key or serving_api_key(eph.vllm_extra_args, eph.env)
            run.queued_job_manifest = build_self_serving_bench_job(
                run,
                serving_deployment=build_deployment(eph),
                serve_argv=serve_argv,
                port=VLLM_PORT,
                api_key=api_key,
                served_model=eph.model_path,
                tokenizer=params.get("tokenizer"),
            )
        if body.api_key:
            run.serving_snapshot["api_key_override"] = body.api_key
        db.add(run)
        runs.append(run)
    await db.flush()

    # Promote combo #0 now; a create failure marks it failed and leaves the
    # sweep running — the reconciler promotes the next combo on its next tick.
    k8s = await k8s_for_cluster(db, cluster_uuid)
    try:
        await promote_queued_run(k8s, runs[0])
    except K8sNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("Sweep combo #0 Job create failed for sweep %s", sweep.id)
        runs[0].status = "failed"
        runs[0].error_message = f"Benchmark Job create failed: {e}"
        runs[0].queued_job_manifest = None
    await db.flush()
    return _serialize_sweep(sweep, runs=[_serialize(r) for r in runs])
```

- [ ] **Step 5: Register the router in `backend/app/main.py`**

Add `benchmark_sweeps` to the `from app.api import (...)` block, and insert ABOVE the existing `app.include_router(benchmarks.router)` line (order matters — `GET /api/benchmarks/{run_id}` would otherwise capture `/presets` and `/sweeps`):

```python
# Must precede benchmarks.router: its /presets and /sweeps paths would
# otherwise be captured by GET /api/benchmarks/{run_id}.
app.include_router(benchmark_sweeps.router)
app.include_router(benchmarks.router)
```

- [ ] **Step 6: Run — expect PASS**

Run: `cd backend && .venv/bin/python -m pytest tests/test_benchmark_sweeps_api.py tests/test_benchmarks_ephemeral.py -q`
Expected: all pass (existing ephemeral test still green)

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/benchmark_sweeps.py backend/app/api/benchmarks.py backend/app/main.py backend/tests/test_benchmark_sweeps_api.py
git commit -m "feat(bench): sweep create + presets endpoints — grid expands to queued self-serving runs"
```

---

### Task 5: Sweep API — list / detail / cancel

**Files:**
- Modify: `backend/app/api/benchmark_sweeps.py`
- Test: `backend/tests/test_benchmark_sweeps_api.py` (append)

**Interfaces:**
- Produces: `GET /api/benchmarks/sweeps` → `{"sweeps": [ ...sweep + progress {total, by_status} ]}` newest first; `GET /api/benchmarks/sweeps/{sweep_id}` → sweep + `runs` ordered by `sweep_index`; `POST /api/benchmarks/sweeps/{sweep_id}/cancel`. Task 7's hooks consume these shapes.

- [ ] **Step 1: Append failing tests**

```python
# append to backend/tests/test_benchmark_sweeps_api.py

def _sweep_row(**kw):
    row = MagicMock()
    row.id = uuid.uuid4()
    row.name = None
    row.deployment_id = None
    row.external_source = None
    row.cluster_id = None
    row.k8s_namespace = "default"
    row.preset = "chat"
    row.variables = [{"flag": "--max-num-seqs", "values": [128, 256]}]
    row.serving_overrides = None
    row.status = "running"
    row.created_by = "admin"
    row.created_at = None
    row.finished_at = None
    for k, v in kw.items():
        setattr(row, k, v)
    return row


async def test_list_returns_progress_rollup(client_for_user, super_user, mock_db):
    sweep = _sweep_row()
    sweeps_res = MagicMock()
    sweeps_res.scalars.return_value.all.return_value = [sweep]
    counts_res = MagicMock()
    counts_res.all.return_value = [(sweep.id, "succeeded", 1), (sweep.id, "queued", 1)]
    mock_db.execute = AsyncMock(side_effect=[sweeps_res, counts_res])
    async with client_for_user(super_user) as client:
        resp = await client.get("/api/benchmarks/sweeps")
    assert resp.status_code == 200
    s = resp.json()["sweeps"][0]
    assert s["progress"] == {"total": 2, "by_status": {"succeeded": 1, "queued": 1}}


async def test_cancel_cancels_active_job_and_queued_rest(client_for_user, super_user, mock_db):
    sweep = _sweep_row()
    active = MagicMock()
    active.status = "running"
    active.k8s_job_name = "bench-x"
    active.k8s_namespace = "default"
    active.queued_job_manifest = None
    queued = MagicMock()
    queued.status = "queued"
    queued.k8s_job_name = None
    queued.k8s_namespace = "default"
    queued.queued_job_manifest = {"kind": "Job"}
    sweep_res = MagicMock()
    sweep_res.scalar_one_or_none.return_value = sweep
    runs_res = MagicMock()
    runs_res.scalars.return_value.all.return_value = [active, queued]
    mock_db.execute = AsyncMock(side_effect=[sweep_res, runs_res])
    fake_k8s = MagicMock()
    fake_k8s.delete_job = AsyncMock()
    with patch("app.api.benchmark_sweeps.k8s_for_cluster", AsyncMock(return_value=fake_k8s)):
        async with client_for_user(super_user) as client:
            resp = await client.post(f"/api/benchmarks/sweeps/{sweep.id}/cancel")
    assert resp.status_code == 200
    fake_k8s.delete_job.assert_awaited_once_with("default", "bench-x")
    assert active.status == "cancelled"
    assert queued.status == "cancelled" and queued.queued_job_manifest is None
    assert sweep.status == "cancelled" and sweep.finished_at is not None
```

- [ ] **Step 2: Run — expect FAIL (404)**

Run: `cd backend && .venv/bin/python -m pytest tests/test_benchmark_sweeps_api.py -q`
Expected: the two new tests FAIL with 404

- [ ] **Step 3: Append the endpoints to `backend/app/api/benchmark_sweeps.py`**

```python
from datetime import UTC, datetime


def _now():
    return datetime.now(UTC)


@router.get("/sweeps")
async def list_sweeps(
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    rows = (
        await db.execute(
            select(CustomBenchmarkSweep).order_by(CustomBenchmarkSweep.created_at.desc()).limit(200)
        )
    ).scalars().all()
    ids = [s.id for s in rows]
    progress: dict = {sid: {"total": 0, "by_status": {}} for sid in ids}
    if ids:
        counts = await db.execute(
            select(CustomBenchmarkRun.sweep_id, CustomBenchmarkRun.status, func.count())
            .where(CustomBenchmarkRun.sweep_id.in_(ids))
            .group_by(CustomBenchmarkRun.sweep_id, CustomBenchmarkRun.status)
        )
        for sweep_id, run_status, n in counts.all():
            progress[sweep_id]["total"] += n
            progress[sweep_id]["by_status"][run_status] = n
    return {"sweeps": [_serialize_sweep(s, progress=progress[s.id]) for s in rows]}


async def _get_sweep(db: AsyncSession, sweep_id: str) -> CustomBenchmarkSweep:
    try:
        sid = uuid.UUID(sweep_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Sweep not found")
    sweep = (
        await db.execute(select(CustomBenchmarkSweep).where(CustomBenchmarkSweep.id == sid))
    ).scalar_one_or_none()
    if not sweep:
        raise HTTPException(status_code=404, detail="Sweep not found")
    return sweep


async def _sweep_runs(db: AsyncSession, sweep_id: uuid.UUID) -> list[CustomBenchmarkRun]:
    return (
        await db.execute(
            select(CustomBenchmarkRun)
            .where(CustomBenchmarkRun.sweep_id == sweep_id)
            .order_by(CustomBenchmarkRun.sweep_index)
        )
    ).scalars().all()


@router.get("/sweeps/{sweep_id}")
async def get_sweep(
    sweep_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    sweep = await _get_sweep(db, sweep_id)
    runs = await _sweep_runs(db, sweep.id)
    return _serialize_sweep(sweep, runs=[_serialize(r) for r in runs])


@router.post("/sweeps/{sweep_id}/cancel")
async def cancel_sweep(
    sweep_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    sweep = await _get_sweep(db, sweep_id)
    if sweep.status in ("completed", "cancelled"):
        return _serialize_sweep(sweep)
    runs = await _sweep_runs(db, sweep.id)
    k8s = await k8s_for_cluster(db, sweep.cluster_id)
    for run in runs:
        if run.status in SWEEP_TERMINAL:
            continue
        if run.k8s_job_name and run.k8s_namespace:
            try:
                await k8s.delete_job(run.k8s_namespace, run.k8s_job_name)
            except K8sNotConfigured as e:
                raise HTTPException(status_code=503, detail=str(e))
            except Exception:
                logger.exception("Sweep run Job delete failed for %s", run.id)
        run.status = "cancelled"
        run.queued_job_manifest = None
        run.finished_at = _now()
    sweep.status = "cancelled"
    sweep.finished_at = _now()
    await db.flush()
    return _serialize_sweep(sweep, runs=[_serialize(r) for r in runs])
```

- [ ] **Step 4: Run — expect PASS**

Run: `cd backend && .venv/bin/python -m pytest tests/test_benchmark_sweeps_api.py -q && .venv/bin/ruff check app/api/benchmark_sweeps.py tests/test_benchmark_sweeps_api.py`
Expected: all pass, ruff clean

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/benchmark_sweeps.py backend/tests/test_benchmark_sweeps_api.py
git commit -m "feat(bench): sweep list/detail/cancel endpoints with progress rollup"
```

---

### Task 6: Reconciler — `_drive_sweeps`

**Files:**
- Modify: `backend/app/jobs/reconcile_benchmarks.py`
- Test: `backend/tests/test_reconcile_sweeps.py`

**Interfaces:**
- Consumes: `promote_queued_run` (Task 2), `CustomBenchmarkSweep` (Task 3).
- Produces: `async _drive_sweeps(db) -> int` (transition count), called from `reconcile_once` after the safety sweep, before commit. Sequentiality invariant: promotes only when a sweep has NO run in (`provisioning`, `pending`, `running`).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_reconcile_sweeps.py
"""Sweep driver: sequential promotion, failure-continues, completion."""

import types
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.jobs.reconcile_benchmarks import _drive_sweeps
from app.services.benchmark_manifests import job_name_for


def _sweep(**kw):
    base = dict(id=uuid.uuid4(), status="running", finished_at=None, cluster_id=None)
    base.update(kw)
    return types.SimpleNamespace(**base)


def _run(**kw):
    base = dict(
        id=uuid.uuid4(), status="queued", sweep_index=0, cluster_id=None,
        k8s_namespace="bench", k8s_job_name=None, queued_job_manifest={"kind": "Job"},
        error_message=None, finished_at=None,
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def _res(items):
    m = MagicMock()
    m.scalars.return_value.all.return_value = items
    return m


def _db(sweeps, runs):
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_res(sweeps)] + [_res(r) for r in runs])
    return db


async def test_promotes_next_queued_when_previous_terminal():
    done = _run(status="succeeded", sweep_index=0, queued_job_manifest=None)
    queued = _run(status="queued", sweep_index=1)
    db = _db([_sweep()], [[done, queued]])
    k8s = MagicMock()
    k8s.create_job = AsyncMock()
    with patch("app.jobs.reconcile_benchmarks.k8s_for_cluster", AsyncMock(return_value=k8s)):
        n = await _drive_sweeps(db)
    assert n == 1
    assert queued.status == "pending"
    assert queued.k8s_job_name == job_name_for(queued.id)
    assert queued.queued_job_manifest is None


async def test_waits_while_a_combo_is_active():
    active = _run(status="running", sweep_index=0, queued_job_manifest=None, k8s_job_name="bench-x")
    queued = _run(status="queued", sweep_index=1)
    db = _db([_sweep()], [[active, queued]])
    with patch("app.jobs.reconcile_benchmarks.k8s_for_cluster", AsyncMock()) as kfc:
        n = await _drive_sweeps(db)
    assert n == 0 and queued.status == "queued"
    kfc.assert_not_awaited()


async def test_failed_combo_does_not_block_next():
    failed = _run(status="failed", sweep_index=0, queued_job_manifest=None)
    queued = _run(status="queued", sweep_index=1)
    db = _db([_sweep()], [[failed, queued]])
    k8s = MagicMock()
    k8s.create_job = AsyncMock()
    with patch("app.jobs.reconcile_benchmarks.k8s_for_cluster", AsyncMock(return_value=k8s)):
        n = await _drive_sweeps(db)
    assert n == 1 and queued.status == "pending"


async def test_promotion_failure_marks_run_failed_and_continues_next_tick():
    queued = _run(status="queued", sweep_index=0)
    db = _db([_sweep()], [[queued]])
    k8s = MagicMock()
    k8s.create_job = AsyncMock(side_effect=RuntimeError("boom"))
    with patch("app.jobs.reconcile_benchmarks.k8s_for_cluster", AsyncMock(return_value=k8s)):
        n = await _drive_sweeps(db)
    assert n == 1
    assert queued.status == "failed" and "boom" in queued.error_message
    assert queued.queued_job_manifest is None and queued.finished_at is not None


async def test_all_terminal_completes_sweep():
    sweep = _sweep()
    runs = [_run(status="succeeded", sweep_index=0, queued_job_manifest=None),
            _run(status="failed", sweep_index=1, queued_job_manifest=None)]
    db = _db([sweep], [runs])
    n = await _drive_sweeps(db)
    assert n == 1
    assert sweep.status == "completed" and sweep.finished_at is not None
```

- [ ] **Step 2: Run — expect FAIL (import error)**

Run: `cd backend && .venv/bin/python -m pytest tests/test_reconcile_sweeps.py -q`
Expected: `ImportError: cannot import name '_drive_sweeps'`

- [ ] **Step 3: Implement in `backend/app/jobs/reconcile_benchmarks.py`**

Add imports:

```python
from app.db.models.custom_benchmark_sweep import CustomBenchmarkSweep
from app.services.benchmark_sweeps import promote_queued_run
```

Add the driver (below `_drive_job`):

```python
ACTIVE = ("provisioning", "pending", "running")


async def _drive_sweeps(db) -> int:
    """Promote each running sweep's next queued combo once the previous one is
    terminal; complete the sweep when no combos remain. A failed combo never
    blocks the rest. Purely DB-state-driven, so portal restarts resume."""
    transitions = 0
    sweeps = (
        await db.execute(select(CustomBenchmarkSweep).where(CustomBenchmarkSweep.status == "running"))
    ).scalars().all()
    for sweep in sweeps:
        runs = (
            await db.execute(
                select(CustomBenchmarkRun)
                .where(CustomBenchmarkRun.sweep_id == sweep.id)
                .order_by(CustomBenchmarkRun.sweep_index)
            )
        ).scalars().all()
        if any(r.status in ACTIVE for r in runs):
            continue
        queued = [r for r in runs if r.status == "queued"]
        if not queued:
            sweep.status = "completed"
            sweep.finished_at = _now()
            transitions += 1
            continue
        nxt = queued[0]
        k8s = await k8s_for_cluster(db, nxt.cluster_id)
        try:
            await promote_queued_run(k8s, nxt)
        except K8sNotConfigured:
            logger.warning("K8s not configured; skipping sweep %s", sweep.id)
            continue
        except Exception as e:  # noqa: BLE001 — mark this combo failed, next tick continues
            logger.exception("Sweep combo promotion failed for run %s", nxt.id)
            nxt.status = "failed"
            nxt.error_message = f"Benchmark Job create failed: {e}"
            nxt.queued_job_manifest = None
            nxt.finished_at = _now()
        transitions += 1
    return transitions
```

Wire into `reconcile_once` — after the ❷ safety-sweep `for` loop, before `await db.commit()`:

```python
            # ❸ Sweeps: promote the next queued combo / complete finished sweeps.
            transitions += await _drive_sweeps(db)
```

- [ ] **Step 4: Run — expect PASS (plus existing reconciler tests)**

Run: `cd backend && .venv/bin/python -m pytest tests/test_reconcile_sweeps.py tests/test_reconcile_benchmarks.py -q && .venv/bin/ruff check app/jobs/reconcile_benchmarks.py tests/test_reconcile_sweeps.py`
Expected: all pass, ruff clean

- [ ] **Step 5: Commit**

```bash
git add backend/app/jobs/reconcile_benchmarks.py backend/tests/test_reconcile_sweeps.py
git commit -m "feat(bench): reconciler drives sweeps — sequential combo promotion + completion"
```

---

### Task 7: Frontend foundation — types, hooks, shared metrics lib, `queued` status

**Files:**
- Modify: `frontend/src/types/index.ts`
- Modify: `frontend/src/hooks/use-api.ts` (append after `useCreateBenchmark`)
- Create: `frontend/src/lib/bench-metrics.ts`
- Modify: `frontend/src/app/(app)/admin/benchmarks/compare/page.tsx` (import from the lib instead of local copies)
- Modify: every `STATUS_STYLES` map under `frontend/src/app/(app)/admin/benchmarks/` + `STATUS_OPTIONS` in the list page
- Modify: `frontend/messages/en.json`, `frontend/messages/ko.json` (`benchmarkStatus.queued`)

**Interfaces:**
- Produces for Tasks 8–9: types `BenchmarkSweep`, `SweepVariable`, `LoadPreset`, `CreateBenchmarkSweepRequest`, `SweepStatus`; hooks `useBenchmarkPresets`, `useBenchmarkSweeps`, `useBenchmarkSweep(id)`, `useCreateBenchmarkSweep`, `useCancelBenchmarkSweep`; lib exports `PERF_METRICS`, `MetricSpec`, `Direction`, `getAt`, `fmt`, `pickBestWorst`.

- [ ] **Step 1: types — `frontend/src/types/index.ts`**

Add `"queued"` to `BenchmarkStatus` (after `"pending"`). Add to `BenchmarkRun` (after `serving_torn_down`):

```ts
  sweep_id: string | null;
  sweep_index: number | null;
  sweep_combo: Record<string, string | number> | null;
```

Append after `CreateBenchmarkRequest`:

```ts
export type SweepStatus = "running" | "completed" | "cancelled";

export interface SweepVariable {
  flag: string;
  values: (number | string)[];
}

export interface LoadPreset {
  random_input_len: number;
  random_output_len: number;
  num_prompts: number;
  max_concurrency: number;
}

export interface BenchmarkSweep {
  id: string;
  name: string | null;
  deployment_id: string | null;
  external_source: { cluster_id: string | null; namespace: string; deployment_name: string } | null;
  cluster_id: string | null;
  k8s_namespace: string;
  preset: string;
  variables: SweepVariable[];
  serving_overrides: Record<string, unknown> | null;
  status: SweepStatus;
  created_by: string;
  created_at: string | null;
  finished_at: string | null;
  progress?: { total: number; by_status: Record<string, number> };
  runs?: BenchmarkRun[];
}

export interface CreateBenchmarkSweepRequest {
  name?: string;
  deployment_id?: string;
  external_target?: { cluster_id: string | null; namespace: string; deployment_name: string };
  cluster_id?: string;
  namespace?: string;
  preset: string;
  variables: SweepVariable[];
  serving_overrides?: Record<string, unknown>;
  api_key?: string;
}
```

- [ ] **Step 2: hooks — append to `frontend/src/hooks/use-api.ts`** (import `BenchmarkSweep`, `CreateBenchmarkSweepRequest`, `LoadPreset` in the existing type import)

```ts
export function useBenchmarkPresets() {
  return useQuery({
    queryKey: ["benchmark-presets"],
    queryFn: () =>
      apiFetch<{ presets: Record<string, LoadPreset> }>("/api/benchmarks/presets").then((r) => r.presets),
    staleTime: Infinity,
  });
}

export function useBenchmarkSweeps() {
  return useQuery({
    queryKey: ["benchmark-sweeps"],
    queryFn: () =>
      apiFetch<{ sweeps: BenchmarkSweep[] }>("/api/benchmarks/sweeps").then((r) => r.sweeps),
    refetchInterval: 15000,
  });
}

export function useBenchmarkSweep(id: string) {
  return useQuery({
    queryKey: ["benchmark-sweeps", id],
    queryFn: () => apiFetch<BenchmarkSweep>(`/api/benchmarks/sweeps/${id}`),
    refetchInterval: (query) =>
      query.state.data && query.state.data.status !== "running" ? false : 5000,
  });
}

export function useCreateBenchmarkSweep() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateBenchmarkSweepRequest) =>
      apiFetch<BenchmarkSweep>("/api/benchmarks/sweeps", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["benchmark-sweeps"] });
      qc.invalidateQueries({ queryKey: ["benchmarks"] });
    },
  });
}

export function useCancelBenchmarkSweep() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      apiFetch<BenchmarkSweep>(`/api/benchmarks/sweeps/${id}/cancel`, { method: "POST" }),
    onSuccess: (_data, id) => {
      qc.invalidateQueries({ queryKey: ["benchmark-sweeps"] });
      qc.invalidateQueries({ queryKey: ["benchmark-sweeps", id] });
      qc.invalidateQueries({ queryKey: ["benchmarks"] });
    },
  });
}
```

- [ ] **Step 3: shared metrics lib — `frontend/src/lib/bench-metrics.ts`**

Move (cut, don't copy) `Direction`, `MetricSpec`, `PERF_METRICS`, `getAt`, `fmt`, `pickBestWorst` from `compare/page.tsx` lines 25–103 into the new file verbatim, each prefixed with `export`. Update `compare/page.tsx` to `import { PERF_METRICS, getAt, fmt, pickBestWorst } from "@/lib/bench-metrics";` (plus `type MetricSpec/Direction` if referenced). No behavior change.

- [ ] **Step 4: `queued` status everywhere**

Run `grep -rn "STATUS_STYLES" frontend/src/app/\(app\)/admin/benchmarks/` — in EVERY map found (list `page.tsx:25`, `compare/page.tsx:16`, and `[id]/page.tsx` if present), add:

```ts
  queued: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
```

In the list page `STATUS_OPTIONS` (page.tsx:40) add `"queued"` after `"pending"`. In `frontend/messages/en.json` under `benchmarkStatus` add `"queued": "Queued"`; in `ko.json` add `"queued": "대기중"`.

- [ ] **Step 5: Verify**

Run: `cd frontend && npx tsc --noEmit`
Expected: clean (tsc enforces `Record<BenchmarkRun["status"], string>` completeness — a missed map fails here)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/hooks/use-api.ts frontend/src/lib/bench-metrics.ts "frontend/src/app/(app)/admin/benchmarks" frontend/messages/en.json frontend/messages/ko.json
git commit -m "feat(frontend): sweep types/hooks, shared bench-metrics lib, queued status"
```

---

### Task 8: Sweep submit form

**Files:**
- Create: `frontend/src/app/(app)/admin/benchmarks/sweeps/new/page.tsx`
- Modify: `frontend/messages/en.json`, `frontend/messages/ko.json` (namespace `benchmarkSweeps`)

**Interfaces:**
- Consumes: Task 7 hooks/types; existing `useModelDeployments`, `useExternalServings` from `@/hooks/use-api`; ui components `Button/Card/Input/Label`.
- Produces: form POSTs `CreateBenchmarkSweepRequest`, then routes to `/admin/benchmarks/sweeps/${id}`.

- [ ] **Step 1: i18n keys** — add to `en.json`:

```json
"benchmarkSweeps": {
  "listTitle": "Sweeps",
  "newTitle": "New benchmark sweep",
  "newDescription": "One submission runs every flag combination sequentially on the same resources under a fixed load preset.",
  "backToList": "Back to benchmarks",
  "nameLabel": "Name (optional)",
  "targetLabel": "Base serving",
  "targetPlaceholder": "Select a serving to clone per combo",
  "externalGroup": "External servings",
  "deploymentGroup": "Portal deployments",
  "presetLabel": "Load preset",
  "presetChat": "Chat — short prompts / short outputs",
  "presetLongInput": "Long input — RAG / summarization",
  "presetLongOutput": "Long output — generation-heavy",
  "presetDetail": "in {input} / out {output} · {prompts} prompts · concurrency {conc}",
  "variablesLabel": "Sweep variables (1–2 serve flags)",
  "flagPlaceholder": "--max-num-seqs",
  "valuesPlaceholder": "128, 256",
  "valuesHint": "Comma-separated values; one serving per combination",
  "addVariable": "Add variable",
  "removeVariable": "Remove",
  "comboCount": "{count} combinations will run sequentially",
  "comboInvalid": "Sweep must expand to 2–12 combinations",
  "submit": "Start sweep",
  "submitting": "Starting…",
  "createError": "Failed to start the sweep",
  "targetRequired": "Pick a base serving",
  "flagInvalid": "Flags must look like --lower-kebab-case and be distinct",
  "colCombo": "Combination",
  "colStatus": "Status",
  "colDuration": "Duration",
  "colName": "Name",
  "colBase": "Base",
  "colPreset": "Preset",
  "colProgress": "Progress",
  "colCreatedAt": "Created",
  "cancelSweep": "Cancel sweep",
  "cancelSuccess": "Sweep cancelled",
  "detailTitle": "Sweep",
  "empty": "No sweeps yet",
  "external": "external"
}
```

and to `ko.json`:

```json
"benchmarkSweeps": {
  "listTitle": "스윕",
  "newTitle": "새 벤치마크 스윕",
  "newDescription": "한 번 제출하면 고정 부하 프리셋으로 모든 플래그 조합을 같은 리소스에서 순차 실행합니다.",
  "backToList": "벤치마크 목록으로",
  "nameLabel": "이름 (선택)",
  "targetLabel": "베이스 서빙",
  "targetPlaceholder": "조합마다 복제할 서빙 선택",
  "externalGroup": "외부 서빙",
  "deploymentGroup": "포털 배포",
  "presetLabel": "부하 프리셋",
  "presetChat": "채팅 — 짧은 입력 / 짧은 출력",
  "presetLongInput": "긴 입력 — RAG · 문서 요약",
  "presetLongOutput": "긴 출력 — 생성 중심",
  "presetDetail": "입력 {input} / 출력 {output} · 프롬프트 {prompts} · 동시성 {conc}",
  "variablesLabel": "스윕 변수 (serve 플래그 1–2개)",
  "flagPlaceholder": "--max-num-seqs",
  "valuesPlaceholder": "128, 256",
  "valuesHint": "쉼표로 구분한 값 목록 — 조합마다 서빙이 새로 뜹니다",
  "addVariable": "변수 추가",
  "removeVariable": "삭제",
  "comboCount": "{count}개 조합이 순차 실행됩니다",
  "comboInvalid": "조합 수는 2–12개여야 합니다",
  "submit": "스윕 시작",
  "submitting": "시작 중…",
  "createError": "스윕 시작에 실패했습니다",
  "targetRequired": "베이스 서빙을 선택하세요",
  "flagInvalid": "플래그는 --소문자-케밥 형식이며 중복될 수 없습니다",
  "colCombo": "조합",
  "colStatus": "상태",
  "colDuration": "소요 시간",
  "colName": "이름",
  "colBase": "베이스",
  "colPreset": "프리셋",
  "colProgress": "진행",
  "colCreatedAt": "생성",
  "cancelSweep": "스윕 취소",
  "cancelSuccess": "스윕을 취소했습니다",
  "detailTitle": "스윕",
  "empty": "아직 스윕이 없습니다",
  "external": "외부"
}
```

- [ ] **Step 2: the form page**

```tsx
// frontend/src/app/(app)/admin/benchmarks/sweeps/new/page.tsx
"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, Loader2, Play, Plus, X } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import {
  useBenchmarkPresets,
  useCreateBenchmarkSweep,
  useExternalServings,
  useModelDeployments,
} from "@/hooks/use-api";
import type { ExternalServing } from "@/hooks/use-api";
import type { CreateBenchmarkSweepRequest, SweepVariable } from "@/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const FLAG_RE = /^--[a-z0-9][a-z0-9-]*$/;
const PRESET_LABEL_KEY: Record<string, string> = {
  chat: "presetChat",
  long_input: "presetLongInput",
  long_output: "presetLongOutput",
};

type VarRow = { flag: string; values: string };

function parseValues(raw: string): (number | string)[] {
  return raw
    .split(",")
    .map((v) => v.trim())
    .filter(Boolean)
    .map((v) => (v !== "" && !Number.isNaN(Number(v)) ? Number(v) : v));
}

export default function NewSweepPage() {
  const t = useTranslations("benchmarkSweeps");
  const router = useRouter();
  const { data: deployments } = useModelDeployments();
  const { data: external } = useExternalServings();
  const { data: presets } = useBenchmarkPresets();
  const createMut = useCreateBenchmarkSweep();

  const [name, setName] = useState("");
  const [target, setTarget] = useState("");
  const [preset, setPreset] = useState("chat");
  const [rows, setRows] = useState<VarRow[]>([{ flag: "", values: "" }]);

  const servings = external?.servings ?? [];
  const readyDeployments = (deployments ?? []).filter((d) => d.ready_replicas > 0);
  const extKey = (s: ExternalServing) => `ext::${s.cluster_id ?? ""}::${s.namespace}::${s.deployment_name}`;

  const variables: SweepVariable[] = rows
    .filter((r) => r.flag.trim() && parseValues(r.values).length > 0)
    .map((r) => ({ flag: r.flag.trim(), values: parseValues(r.values) }));
  const comboCount = variables.length
    ? variables.reduce((n, v) => n * v.values.length, 1)
    : 0;
  const flagsValid =
    variables.length >= 1 &&
    variables.length === rows.length &&
    variables.every((v) => FLAG_RE.test(v.flag)) &&
    new Set(variables.map((v) => v.flag)).size === variables.length;
  const combosValid = comboCount >= 2 && comboCount <= 12;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!target) return toast.error(t("targetRequired"));
    if (!flagsValid) return toast.error(t("flagInvalid"));
    if (!combosValid) return toast.error(t("comboInvalid"));
    const body: CreateBenchmarkSweepRequest = {
      name: name.trim() || undefined,
      preset,
      variables,
    };
    if (target.startsWith("ext::")) {
      const s = servings.find((x) => extKey(x) === target);
      if (!s) return toast.error(t("targetRequired"));
      body.external_target = {
        cluster_id: s.cluster_id ?? null,
        namespace: s.namespace,
        deployment_name: s.deployment_name,
      };
    } else {
      body.deployment_id = target;
    }
    createMut.mutate(body, {
      onSuccess: (sweep) => router.push(`/admin/benchmarks/sweeps/${sweep.id}`),
      onError: (err) => toast.error(err instanceof Error ? err.message : t("createError")),
    });
  };

  return (
    <div className="space-y-6 max-w-3xl">
      <div>
        <Link
          href="/admin/benchmarks"
          className="text-sm text-muted-foreground hover:text-foreground inline-flex items-center gap-1"
        >
          <ArrowLeft className="size-3.5" />
          {t("backToList")}
        </Link>
        <h1 className="text-2xl font-bold mt-2">{t("newTitle")}</h1>
        <p className="text-muted-foreground mt-1">{t("newDescription")}</p>
      </div>

      <form onSubmit={handleSubmit}>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t("newTitle")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-5">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="sweep-name">{t("nameLabel")}</Label>
                <Input id="sweep-name" value={name} onChange={(e) => setName(e.target.value)} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="sweep-target">{t("targetLabel")}</Label>
                <select
                  id="sweep-target"
                  className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                  value={target}
                  onChange={(e) => setTarget(e.target.value)}
                >
                  <option value="">{t("targetPlaceholder")}</option>
                  <optgroup label={t("deploymentGroup")}>
                    {readyDeployments.map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.model_name} ({d.namespace})
                      </option>
                    ))}
                  </optgroup>
                  <optgroup label={t("externalGroup")}>
                    {servings.map((s) => (
                      <option key={extKey(s)} value={extKey(s)}>
                        {s.deployment_name} ({s.namespace})
                      </option>
                    ))}
                  </optgroup>
                </select>
              </div>
            </div>

            <div className="space-y-2">
              <Label>{t("presetLabel")}</Label>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                {Object.entries(presets ?? {}).map(([key, p]) => (
                  <button
                    key={key}
                    type="button"
                    onClick={() => setPreset(key)}
                    className={`rounded-lg border p-3 text-left text-sm transition-colors ${
                      preset === key ? "border-primary ring-2 ring-primary/30" : "hover:bg-muted/40"
                    }`}
                  >
                    <div className="font-medium">{t(PRESET_LABEL_KEY[key] ?? key)}</div>
                    <div className="text-xs text-muted-foreground mt-1">
                      {t("presetDetail", {
                        input: p.random_input_len,
                        output: p.random_output_len,
                        prompts: p.num_prompts,
                        conc: p.max_concurrency,
                      })}
                    </div>
                  </button>
                ))}
              </div>
            </div>

            <div className="space-y-2">
              <Label>{t("variablesLabel")}</Label>
              {rows.map((row, i) => (
                <div key={i} className="flex items-center gap-2">
                  <Input
                    className="font-mono"
                    placeholder={t("flagPlaceholder")}
                    value={row.flag}
                    onChange={(e) =>
                      setRows(rows.map((r, j) => (j === i ? { ...r, flag: e.target.value } : r)))
                    }
                  />
                  <Input
                    className="font-mono"
                    placeholder={t("valuesPlaceholder")}
                    value={row.values}
                    onChange={(e) =>
                      setRows(rows.map((r, j) => (j === i ? { ...r, values: e.target.value } : r)))
                    }
                  />
                  {rows.length > 1 && (
                    <Button type="button" variant="ghost" size="sm" onClick={() => setRows(rows.filter((_, j) => j !== i))}>
                      <X className="size-4" />
                    </Button>
                  )}
                </div>
              ))}
              <div className="flex items-center justify-between">
                <p className="text-xs text-muted-foreground">{t("valuesHint")}</p>
                {rows.length < 2 && (
                  <Button type="button" variant="outline" size="sm" onClick={() => setRows([...rows, { flag: "", values: "" }])}>
                    <Plus className="size-3.5 mr-1" />
                    {t("addVariable")}
                  </Button>
                )}
              </div>
              <p className={`text-sm ${combosValid ? "text-muted-foreground" : "text-destructive"}`}>
                {comboCount > 0 && (combosValid ? t("comboCount", { count: comboCount }) : t("comboInvalid"))}
              </p>
            </div>

            <div className="flex justify-end">
              <Button type="submit" disabled={createMut.isPending}>
                {createMut.isPending ? (
                  <Loader2 className="size-4 mr-1 animate-spin" />
                ) : (
                  <Play className="size-4 mr-1" />
                )}
                {createMut.isPending ? t("submitting") : t("submit")}
              </Button>
            </div>
          </CardContent>
        </Card>
      </form>
    </div>
  );
}
```

- [ ] **Step 3: Verify**

Run: `cd frontend && npx tsc --noEmit`
Expected: clean. (If `useModelDeployments`/`useExternalServings` types differ, mirror their usage in `admin/benchmarks/new/page.tsx:72-76`.)

- [ ] **Step 4: Commit**

```bash
git add "frontend/src/app/(app)/admin/benchmarks/sweeps" frontend/messages/en.json frontend/messages/ko.json
git commit -m "feat(frontend): sweep submit form — target, preset cards, variable grid"
```

---

### Task 9: Sweep detail (auto-comparison) + sweeps tab on the list page

**Files:**
- Create: `frontend/src/app/(app)/admin/benchmarks/sweeps/[id]/page.tsx`
- Modify: `frontend/src/app/(app)/admin/benchmarks/page.tsx` (tabs: runs | sweeps; header button to the sweep form)

**Interfaces:**
- Consumes: `useBenchmarkSweep`, `useBenchmarkSweeps`, `useCancelBenchmarkSweep` (Task 7); `PERF_METRICS`, `getAt`, `fmt`, `pickBestWorst` from `@/lib/bench-metrics`; i18n namespace `benchmarkSweeps` (Task 8).

- [ ] **Step 1: detail page**

```tsx
// frontend/src/app/(app)/admin/benchmarks/sweeps/[id]/page.tsx
"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, Loader2, OctagonX } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import { useBenchmarkSweep, useCancelBenchmarkSweep } from "@/hooks/use-api";
import { getAt, fmt, pickBestWorst, type MetricSpec } from "@/lib/bench-metrics";
import type { BenchmarkRun } from "@/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

const STATUS_STYLES: Record<BenchmarkRun["status"], string> = {
  provisioning: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
  queued: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  pending: "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300",
  running: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
  succeeded: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400",
  failed: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",
  cancelled: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400",
};

// The sweep table's headline metrics (subset of PERF_METRICS, same key/paths).
const SWEEP_METRICS: MetricSpec[] = [
  { key: "p99_ttft_ms", path: ["p99_ttft_ms"], direction: "lower" },
  { key: "p99_tpot_ms", path: ["p99_tpot_ms"], direction: "lower" },
  { key: "output_throughput", path: ["output_throughput"], direction: "higher" },
  { key: "request_throughput", path: ["request_throughput"], direction: "higher" },
  { key: "completed", path: ["completed"], direction: "higher" },
  { key: "duration", path: ["duration"], direction: "lower" },
];

function metricValue(run: BenchmarkRun, spec: MetricSpec): number | null {
  const metrics = (run.result as { metrics?: Record<string, unknown> } | null)?.metrics ?? null;
  const v = getAt(metrics, spec.path);
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function comboLabel(run: BenchmarkRun): string {
  const combo = run.sweep_combo ?? {};
  return Object.entries(combo)
    .map(([flag, value]) => `${flag}=${value}`)
    .join("  ");
}

export default function SweepDetailPage() {
  const t = useTranslations("benchmarkSweeps");
  const ts = useTranslations("benchmarkStatus");
  const params = useParams();
  const id = String(params.id);
  const { data: sweep, isLoading } = useBenchmarkSweep(id);
  const cancelMut = useCancelBenchmarkSweep();

  if (isLoading || !sweep) {
    return (
      <div className="flex justify-center py-16">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const runs = sweep.runs ?? [];
  const bestWorst = SWEEP_METRICS.map((spec) =>
    pickBestWorst(runs.map((r) => metricValue(r, spec)), spec.direction),
  );

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <Link
            href="/admin/benchmarks"
            className="text-sm text-muted-foreground hover:text-foreground inline-flex items-center gap-1"
          >
            <ArrowLeft className="size-3.5" />
            {t("backToList")}
          </Link>
          <h1 className="text-2xl font-bold mt-2">
            {t("detailTitle")}: {sweep.name || comboHeader(sweep.variables)}
          </h1>
          <p className="text-muted-foreground mt-1 text-sm">
            {t("colPreset")}: <span className="font-mono">{sweep.preset}</span>
            {" · "}
            {sweep.external_source
              ? `${sweep.external_source.deployment_name} (${t("external")})`
              : sweep.deployment_id}
          </p>
        </div>
        {sweep.status === "running" && (
          <Button
            variant="destructive"
            disabled={cancelMut.isPending}
            onClick={() =>
              cancelMut.mutate(sweep.id, { onSuccess: () => toast.success(t("cancelSuccess")) })
            }
          >
            <OctagonX className="size-4 mr-1" />
            {t("cancelSweep")}
          </Button>
        )}
      </div>

      <div className="rounded-lg border overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("colCombo")}</TableHead>
              <TableHead>{t("colStatus")}</TableHead>
              {SWEEP_METRICS.map((m) => (
                <TableHead key={m.key} className="text-right font-mono text-xs">
                  {m.key}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {runs.map((run) => (
              <TableRow key={run.id}>
                <TableCell className="font-mono text-xs">
                  <Link href={`/admin/benchmarks/${run.id}`} className="hover:underline">
                    {comboLabel(run) || `#${run.sweep_index}`}
                  </Link>
                  {run.status === "failed" && run.error_message && (
                    <p className="text-xs text-destructive mt-1 max-w-[320px] truncate">
                      {run.error_message}
                    </p>
                  )}
                </TableCell>
                <TableCell>
                  <Badge className={STATUS_STYLES[run.status]}>{ts(run.status)}</Badge>
                </TableCell>
                {SWEEP_METRICS.map((spec, mi) => {
                  const v = metricValue(run, spec);
                  const runIdx = runs.indexOf(run);
                  const { bestIdx, worstIdx } = bestWorst[mi];
                  return (
                    <TableCell
                      key={spec.key}
                      className={cn(
                        "text-right font-mono text-xs",
                        runIdx === bestIdx && "text-green-700 dark:text-green-400 font-semibold",
                        runIdx === worstIdx && "text-red-700 dark:text-red-400",
                      )}
                    >
                      {fmt(v)}
                    </TableCell>
                  );
                })}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

function comboHeader(variables: { flag: string }[]): string {
  return variables.map((v) => v.flag).join(" × ");
}
```

- [ ] **Step 2: list page — tabs + header button**

In `frontend/src/app/(app)/admin/benchmarks/page.tsx`:

1. Imports: add `import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";`, `import { useBenchmarkSweeps } from "@/hooks/use-api";` (merge into the existing use-api import), `import type { BenchmarkSweep } from "@/types";`, and `Layers` to the lucide import.
2. Header buttons: next to the `runBenchmark` link, add

```tsx
          <Link href="/admin/benchmarks/sweeps/new">
            <Button variant="outline">
              <Layers className="size-4 mr-1" />
              {tw("newTitle")}
            </Button>
          </Link>
```

where `const tw = useTranslations("benchmarkSweeps");` is added beside the existing `useTranslations` calls.
3. Wrap the filter bar + runs table in tabs and add the sweeps tab:

```tsx
      <Tabs defaultValue="runs">
        <TabsList>
          <TabsTrigger value="runs">{t("pageTitle")}</TabsTrigger>
          <TabsTrigger value="sweeps">{tw("listTitle")}</TabsTrigger>
        </TabsList>
        <TabsContent value="runs" className="mt-4 space-y-6">
          {/* existing filter <div> + table <div> move here unchanged */}
        </TabsContent>
        <TabsContent value="sweeps" className="mt-4">
          <SweepsTable />
        </TabsContent>
      </Tabs>
```

4. Add the `SweepsTable` component in the same file:

```tsx
function SweepsTable() {
  const tw = useTranslations("benchmarkSweeps");
  const localeTag = useLocaleTag();
  const { data: sweeps, isLoading } = useBenchmarkSweeps();

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-10">
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
      </div>
    );
  }
  if (!sweeps || sweeps.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center rounded-lg border border-dashed py-12 text-muted-foreground">
        <FlaskConical className="size-6 mb-2" />
        <span className="text-sm">{tw("empty")}</span>
      </div>
    );
  }
  const done = (s: BenchmarkSweep) =>
    Object.entries(s.progress?.by_status ?? {})
      .filter(([k]) => ["succeeded", "failed", "cancelled"].includes(k))
      .reduce((n, [, v]) => n + v, 0);
  return (
    <div className="rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{tw("colName")}</TableHead>
            <TableHead>{tw("colBase")}</TableHead>
            <TableHead>{tw("colPreset")}</TableHead>
            <TableHead>{tw("colProgress")}</TableHead>
            <TableHead>{tw("colStatus")}</TableHead>
            <TableHead>{tw("colCreatedAt")}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {sweeps.map((s) => (
            <TableRow key={s.id} className="hover:bg-muted/40">
              <TableCell className="font-medium">
                <Link href={`/admin/benchmarks/sweeps/${s.id}`} className="hover:underline">
                  {s.name || s.variables.map((v) => v.flag).join(" × ")}
                </Link>
              </TableCell>
              <TableCell className="text-sm">
                {s.external_source ? `${s.external_source.deployment_name} (${tw("external")})` : s.deployment_id}
              </TableCell>
              <TableCell className="font-mono text-xs">{s.preset}</TableCell>
              <TableCell className="font-mono text-xs">
                {done(s)}/{s.progress?.total ?? "-"}
              </TableCell>
              <TableCell>
                <Badge variant={s.status === "running" ? "default" : "secondary"}>{s.status}</Badge>
              </TableCell>
              <TableCell className="text-sm">{formatDateTime(s.created_at, localeTag)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
```

- [ ] **Step 3: Verify**

Run: `cd frontend && npx tsc --noEmit && npm run build 2>&1 | tail -5`
Expected: tsc clean; build succeeds

- [ ] **Step 4: Commit**

```bash
git add "frontend/src/app/(app)/admin/benchmarks"
git commit -m "feat(frontend): sweep detail auto-comparison table + sweeps tab on benchmarks list"
```

---

### Task 10: Final verification

**Files:** none (verification only)

- [ ] **Step 1: Backend full suite + lint**

Run: `cd backend && .venv/bin/python -m pytest -q 2>&1 | tail -3 && .venv/bin/ruff check app tests 2>&1 | tail -3`
Expected: 0 NEW failures vs origin/main baseline; 0 NEW ruff findings

- [ ] **Step 2: Frontend typecheck + build**

Run: `cd frontend && npx tsc --noEmit && npm run build 2>&1 | tail -5`
Expected: clean

- [ ] **Step 3: i18n parity**

Run: `cd frontend && node -e "const en=require('./messages/en.json'),ko=require('./messages/ko.json');const dk=(o,p)=>Object.entries(o).flatMap(([k,v])=>typeof v==='object'?dk(v,p+k+'.'):[p+k]);const e=new Set(dk(en,'')),k=new Set(dk(ko,''));console.log('en-only:',[...e].filter(x=>!k.has(x)));console.log('ko-only:',[...k].filter(x=>!e.has(x)))"`
Expected: both arrays empty (or unchanged vs origin/main)

- [ ] **Step 4: Manual QA (needs a cluster)**

- Submit a 2×2 sweep (`--max-num-seqs` [128, 256] × `--gpu-memory-utilization` [0.9, 0.95], preset `chat`) against a small template deployment.
- Confirm: exactly one bench Job exists at any time; combos run in order; detail page fills in and highlights best/worst; a combo forced to OOM shows `failed` and the sweep still finishes `completed`; cancel mid-sweep cancels the active Job and all queued combos.
- `alembic upgrade head` applied cleanly on the dev DB before testing.

- [ ] **Step 5: Wrap up**

Use the superpowers:finishing-a-development-branch skill (PR to `main` from `feat/benchmark-sweeps`).
