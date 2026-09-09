# Recipe vLLM / SGLang Engine Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recipes and portal deployments carry an `engine` (vLLM or SGLang) plus a structured `engine_args` dict, the K8s manifests launch the right server, and the recipe form exposes each engine's main flags as dedicated fields.

**Architecture:** A new pure module `backend/app/services/serving_engines.py` owns the engine catalogue (default images, launch command/flags), the generic `engine_args` → CLI-flag renderer, and key validation; the manifest builder and the benchmark serve-argv sites call it. One Alembic migration adds `engine` / `engine_args` to both tables. The frontend keeps the curated per-engine field list in `frontend/src/lib/serving-engines.ts` and renders it inside the existing full-page recipe form.

**Tech Stack:** FastAPI + pydantic v2 + SQLAlchemy 2 + Alembic (backend, run with `uv run pytest` from `backend/`); Next.js 15 + React + next-intl + TanStack Query (frontend, `npx tsc --noEmit -p .` and `npx eslint` from `frontend/`).

**Spec:** `docs/superpowers/specs/2026-09-09-recipe-engine-split-design.md`

## Global Constraints

- Engine values are exactly `"vllm"` and `"sglang"`; default `"vllm"`.
- `engine_args` keys must match `^[a-z0-9][a-z0-9-]*$` (bare flag name, no leading dashes); values are `str | int | float | bool`.
- Rendered flag order: engine base args, then `engine_args` sorted by key, then `vllm_extra_args`.
- Default images: `vllm/vllm-openai:latest`, `lmsysorg/sglang:latest`.
- SGLang container command: `python3 -m sglang.launch_server --model-path <path> --host 0.0.0.0 --port 8000`. vLLM container args stay byte-for-byte `--model <path> --port 8000 [...]` with no `command`.
- `vllm_extra_args` keeps its name everywhere; it is the engine-neutral "extra args" list.
- Air-gap rule (project memory): no new CDN or external runtime dependency in the frontend.
- Commit messages end with the attribution trailer given in the session (Co-Authored-By + Claude-Session lines).
- All work stays on the current branch; do not create branches or push.

---

### Task 1: Migration 046 + ORM columns

**Files:**
- Create: `backend/migrations/versions/046_serving_engine.py`
- Modify: `backend/app/db/models/custom_serving_recipe.py` (after the `vllm_extra_args` column)
- Modify: `backend/app/db/models/custom_model_deployment.py:48` (after the `vllm_extra_args` column)
- Test: `backend/tests/test_serving_engines.py` (new file; later tasks append to it)

**Interfaces:**
- Produces: `CustomServingRecipe.engine: str`, `CustomServingRecipe.engine_args: dict | None`, same two attributes on `CustomModelDeployment`. Rows created in memory (no session flush) have `engine=None` until flushed.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_serving_engines.py`:

```python
"""Serving engine catalogue, engine_args rendering, and launch helpers."""

import types

import pytest

from app.db.models.custom_model_deployment import CustomModelDeployment
from app.db.models.custom_serving_recipe import CustomServingRecipe


def test_engine_columns_exist_on_both_tables():
    for model in (CustomServingRecipe, CustomModelDeployment):
        cols = model.__table__.columns
        assert cols["engine"].nullable is False
        assert cols["engine"].server_default.arg == "vllm"
        assert cols["engine_args"].nullable is True
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `backend/`): `uv run pytest tests/test_serving_engines.py -q`
Expected: FAIL with `KeyError: 'engine'`

- [ ] **Step 3: Add the ORM columns**

In `backend/app/db/models/custom_serving_recipe.py`, directly after the `vllm_extra_args` line:

```python
    engine: Mapped[str] = mapped_column(String(16), nullable=False, default="vllm", server_default="vllm")
    engine_args: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
```

Also change the class docstring's first line to `"""A named, reusable vLLM/SGLang serving configuration.` and the phrase `vLLM flags` to `engine flags`.

In `backend/app/db/models/custom_model_deployment.py`, directly after the `vllm_extra_args` line (line 48):

```python
    engine: Mapped[str] = mapped_column(String(16), nullable=False, default="vllm", server_default="vllm")
    engine_args: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
```

Both files already import `String`, `JSONB`, `Mapped`, `mapped_column`.

- [ ] **Step 4: Write the migration**

Create `backend/migrations/versions/046_serving_engine.py`:

```python
"""Serving engine (vLLM / SGLang) + structured engine args.

Adds to custom_serving_recipe and custom_model_deployment:
- engine (varchar 16, not null, default 'vllm'): which OpenAI-compatible
  server the image runs. Existing rows become vLLM, matching old behaviour.
- engine_args (jsonb, nullable): {"flag-name": value} rendered generically
  into CLI flags ahead of vllm_extra_args.

Revision ID: 046_serving_engine
Revises: 045_llmd_direct_route
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "046_serving_engine"
down_revision = "045_llmd_direct_route"
branch_labels = None
depends_on = None

_TABLES = ("custom_serving_recipe", "custom_model_deployment")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("engine", sa.String(16), nullable=False, server_default="vllm"))
        op.add_column(table, sa.Column("engine_args", JSONB, nullable=True))


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "engine_args")
        op.drop_column(table, "engine")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_serving_engines.py -q`
Expected: `1 passed`

- [ ] **Step 6: Commit**

```bash
git add backend/migrations/versions/046_serving_engine.py backend/app/db/models/custom_serving_recipe.py backend/app/db/models/custom_model_deployment.py backend/tests/test_serving_engines.py
git commit -m "feat(serving): engine + engine_args columns on recipes and deployments"
```

---

### Task 2: Engine catalogue + launch rendering

**Files:**
- Create: `backend/app/services/serving_engines.py`
- Modify: `backend/app/services/model_deployment_manifests.py:1-12` (imports/constants) and `:75-96` (container block in `build_deployment`)
- Test: `backend/tests/test_serving_engines.py`

**Interfaces:**
- Produces (all in `app.services.serving_engines`):
  - `ServingEngine = Literal["vllm", "sglang"]`, `ENGINES: tuple[str, ...]`, `DEFAULT_ENGINE = "vllm"`, `SERVING_PORT = 8000`
  - `DEFAULT_IMAGES: dict[str, str]`, `default_image(engine: str) -> str`
  - `engine_of(dep) -> str` (None → `"vllm"`)
  - `validate_engine_args(args: dict | None) -> dict | None` (raises `ValueError`)
  - `render_engine_args(args: dict | None) -> list[str]`
  - `engine_flags(dep) -> list[str]`
  - `container_launch(dep) -> tuple[list[str] | None, list[str]]`
  - `serve_argv(dep, port: int = SERVING_PORT) -> list[str]`
- `model_deployment_manifests.VLLM_PORT` remains importable (alias of `SERVING_PORT`).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_serving_engines.py`:

```python
from app.services.model_deployment_manifests import build_deployment
from app.services.serving_engines import (
    container_launch,
    default_image,
    engine_of,
    render_engine_args,
    serve_argv,
    validate_engine_args,
)


def _dep(**kw):
    base = dict(
        model_name="m", namespace="ns", image="img", replicas=1, gpu_count=1,
        gpu_resource_key="nvidia.com/gpu", cpu_request=None, cpu_limit=None,
        memory_request=None, memory_limit=None, node_selector=None, tolerations=None,
        pvc_name=None, pvc_mount_path=None, model_path="/models/m", vllm_extra_args=None,
        env=None, ingress_host="h", ingress_path="/", ingress_class="nginx",
        engine="vllm", engine_args=None,
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def _container(manifest: dict) -> dict:
    return manifest["spec"]["template"]["spec"]["containers"][0]


def test_render_engine_args_sorted_and_typed():
    args = {
        "tp-size": 4, "dtype": "bfloat16", "trust-remote-code": True,
        "disable-radix-cache": False, "served-model-name": "", "mem-fraction-static": 0.88,
    }
    assert render_engine_args(args) == [
        "--dtype", "bfloat16", "--mem-fraction-static", "0.88", "--tp-size", "4", "--trust-remote-code",
    ]


def test_render_engine_args_empty():
    assert render_engine_args(None) == []
    assert render_engine_args({}) == []
    assert render_engine_args({"max-num-seqs": 0}) == ["--max-num-seqs", "0"]


def test_engine_of_defaults_to_vllm():
    assert engine_of(types.SimpleNamespace()) == "vllm"
    assert engine_of(types.SimpleNamespace(engine=None)) == "vllm"
    assert engine_of(types.SimpleNamespace(engine="sglang")) == "sglang"


def test_default_image_per_engine():
    assert default_image("vllm") == "vllm/vllm-openai:latest"
    assert default_image("sglang") == "lmsysorg/sglang:latest"


def test_validate_engine_args():
    assert validate_engine_args(None) is None
    assert validate_engine_args({"tp-size": 4, "dtype": "auto", "x": True, "f": 0.5}) == {
        "tp-size": 4, "dtype": "auto", "x": True, "f": 0.5,
    }
    for bad in ({"--tp-size": 4}, {"TP": 4}, {"a b": 1}, {"-x": 1}):
        with pytest.raises(ValueError):
            validate_engine_args(bad)
    with pytest.raises(ValueError):
        validate_engine_args({"tp-size": [4]})


def test_vllm_container_matches_legacy_args():
    c = _container(build_deployment(_dep(vllm_extra_args=["--max-model-len", "8192"])))
    assert c["name"] == "vllm"
    assert "command" not in c
    assert c["args"] == ["--model", "/models/m", "--port", "8000", "--max-model-len", "8192"]


def test_vllm_container_engine_args_before_extra_args():
    c = _container(build_deployment(_dep(engine_args={"tensor-parallel-size": 2}, vllm_extra_args=["--x"])))
    assert c["args"] == ["--model", "/models/m", "--port", "8000", "--tensor-parallel-size", "2", "--x"]


def test_sglang_container_sets_command_and_model_path():
    c = _container(build_deployment(_dep(engine="sglang", engine_args={"tp-size": 2}, vllm_extra_args=["--log-level", "info"])))
    assert c["name"] == "sglang"
    assert c["command"] == ["python3", "-m", "sglang.launch_server"]
    assert c["args"] == [
        "--model-path", "/models/m", "--host", "0.0.0.0", "--port", "8000",
        "--tp-size", "2", "--log-level", "info",
    ]
    assert c["readinessProbe"]["httpGet"] == {"path": "/health", "port": 8000}


def test_container_launch_in_memory_row_without_engine_is_vllm():
    dep = _dep()
    del dep.engine
    del dep.engine_args
    command, args = container_launch(dep)
    assert command is None
    assert args == ["--model", "/models/m", "--port", "8000"]


def test_serve_argv_vllm_matches_legacy():
    assert serve_argv(_dep(vllm_extra_args=["--a"]), 8000) == ["vllm", "serve", "/models/m", "--port", "8000", "--a"]


def test_serve_argv_sglang():
    assert serve_argv(_dep(engine="sglang", engine_args={"tp-size": 2}), 8000) == [
        "python3", "-m", "sglang.launch_server", "--model-path", "/models/m",
        "--host", "0.0.0.0", "--port", "8000", "--tp-size", "2",
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_serving_engines.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.serving_engines'`

- [ ] **Step 3: Create the catalogue module**

Create `backend/app/services/serving_engines.py`:

```python
"""Serving engine catalogue: how vLLM and SGLang are launched, and how the
structured ``engine_args`` dict becomes CLI flags.

Pure functions, no I/O. The API layer validates ``engine`` / ``engine_args``
with these helpers; the manifest builder and benchmark serve-argv sites render
with them. The curated list of "main" flags lives in the frontend; the backend
only checks key shape and value type so new flags need no server change.
"""

from __future__ import annotations

import re
from typing import Literal

ServingEngine = Literal["vllm", "sglang"]
ENGINES: tuple[str, ...] = ("vllm", "sglang")
DEFAULT_ENGINE = "vllm"
SERVING_PORT = 8000

DEFAULT_IMAGES: dict[str, str] = {
    "vllm": "vllm/vllm-openai:latest",
    "sglang": "lmsysorg/sglang:latest",
}

# command=None means "use the image entrypoint" (the vllm-openai image starts
# the OpenAI server itself and takes --model). SGLang images have no serving
# entrypoint, so the command is explicit.
_LAUNCH: dict[str, dict] = {
    "vllm": {
        "command": None,
        "model_flag": "--model",
        "base_args": ["--port", str(SERVING_PORT)],
    },
    "sglang": {
        "command": ["python3", "-m", "sglang.launch_server"],
        "model_flag": "--model-path",
        "base_args": ["--host", "0.0.0.0", "--port", str(SERVING_PORT)],
    },
}

_ARG_KEY = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def default_image(engine: str) -> str:
    return DEFAULT_IMAGES.get(engine, DEFAULT_IMAGES[DEFAULT_ENGINE])


def engine_of(dep) -> str:
    """Engine name for a row. In-memory rows built without the column (benchmark
    clones, tests) read as None and are treated as vLLM."""
    return getattr(dep, "engine", None) or DEFAULT_ENGINE


def validate_engine_args(args: dict | None) -> dict | None:
    """Raise ValueError on a malformed key or non-scalar value; else return args."""
    for key, value in (args or {}).items():
        if not isinstance(key, str) or not _ARG_KEY.match(key):
            raise ValueError(f"engine_args key {key!r} must match {_ARG_KEY.pattern} (no leading dashes)")
        if not isinstance(value, (str, int, float, bool)):
            raise ValueError(f"engine_args[{key!r}] must be a string, number or boolean")
    return args


def render_engine_args(args: dict | None) -> list[str]:
    """``{"tp-size": 4, "trust-remote-code": True}`` → ``["--tp-size", "4", "--trust-remote-code"]``.

    Keys are sorted so manifests are deterministic. ``True`` renders as a bare
    flag; ``False``, ``None`` and ``""`` are omitted; numbers use ``str()``.
    """
    out: list[str] = []
    for key in sorted(args or {}):
        value = args[key]
        if value is None or value is False or value == "":
            continue
        if value is True:
            out.append(f"--{key}")
        else:
            out.extend([f"--{key}", str(value)])
    return out


def engine_flags(dep) -> list[str]:
    """Structured args first, then the free-text extra args (so a power user can
    still override a structured value by repeating the flag)."""
    return render_engine_args(getattr(dep, "engine_args", None)) + list(dep.vllm_extra_args or [])


def container_launch(dep) -> tuple[list[str] | None, list[str]]:
    """(command, args) for the serving container of a K8s Deployment."""
    spec = _LAUNCH[engine_of(dep)]
    args = [spec["model_flag"], dep.model_path, *spec["base_args"], *engine_flags(dep)]
    return spec["command"], args


def serve_argv(dep, port: int = SERVING_PORT) -> list[str]:
    """Full argv that starts the server from a shell (self-serving bench jobs)."""
    flags = engine_flags(dep)
    if engine_of(dep) == "sglang":
        return [
            "python3", "-m", "sglang.launch_server", "--model-path", dep.model_path,
            "--host", "0.0.0.0", "--port", str(port), *flags,
        ]
    return ["vllm", "serve", dep.model_path, "--port", str(port), *flags]
```

- [ ] **Step 4: Use it in the manifest builder**

In `backend/app/services/model_deployment_manifests.py` replace lines 8-10:

```python
from app.db.models.custom_model_deployment import CustomModelDeployment

VLLM_PORT = 8000
```

with:

```python
from app.db.models.custom_model_deployment import CustomModelDeployment
from app.services.serving_engines import SERVING_PORT, container_launch, engine_of

VLLM_PORT = SERVING_PORT  # kept for existing imports
```

In `build_deployment`, replace:

```python
    # vLLM command/args
    args = ["--model", dep.model_path, "--port", str(VLLM_PORT)]
    if dep.vllm_extra_args:
        args.extend(dep.vllm_extra_args)
```

with:

```python
    # Engine-specific launch (vLLM relies on the image entrypoint; SGLang sets
    # an explicit command). See app.services.serving_engines.
    command, args = container_launch(dep)
```

and replace the container dict so it starts:

```python
    container: dict = {
        "name": engine_of(dep),
        "image": dep.image,
        "args": args,
        "ports": [{"containerPort": VLLM_PORT, "name": "http"}],
        "resources": resources,
        "env": env_items,
        "volumeMounts": volume_mounts,
        "readinessProbe": {
            "httpGet": {"path": "/health", "port": VLLM_PORT},
            "initialDelaySeconds": 60,
            "periodSeconds": 10,
            "timeoutSeconds": 5,
            "failureThreshold": 30,
        },
    }
    if command:
        container["command"] = command

    pod_spec: dict = {
        "containers": [container],
        "volumes": volumes,
    }
```

(The old inline `"containers": [{...}]` literal is removed; the rest of `pod_spec` handling is unchanged.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_serving_engines.py tests/test_self_serving_bench.py -q`
Expected: all pass (the bench tests still see `--model /models/m --port 8000`).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/serving_engines.py backend/app/services/model_deployment_manifests.py backend/tests/test_serving_engines.py
git commit -m "feat(serving): engine catalogue + engine_args rendering; SGLang container launch"
```

---

### Task 3: Benchmarks use the engine helpers

**Files:**
- Modify: `backend/app/services/benchmark_serving.py:45-99` (`build_ephemeral_deployment`)
- Modify: `backend/app/api/benchmarks.py:45-50` (imports), `:157-179` (`_serving_snapshot`), `:566` and `:809` (`serve_argv` lines)
- Test: `backend/tests/test_serving_engines.py`

**Interfaces:**
- Consumes: `engine_of`, `serve_argv` from Task 2.
- Produces: `build_ephemeral_deployment(...)` returns a row with `engine` and `engine_args` copied from `base`; `overrides["engine_args"]` replaces the dict. `_serving_snapshot` gains `"engine"` (real value) and `"engine_args"`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_serving_engines.py`:

```python
from app.api.benchmarks import _serving_snapshot
from app.services.benchmark_serving import build_ephemeral_deployment


def test_ephemeral_deployment_copies_engine_and_args():
    base = _dep(engine="sglang", engine_args={"tp-size": 2, "dtype": "bfloat16"})
    eph = build_ephemeral_deployment(base, name="bench-x", namespace="ns", overrides=None)
    assert eph.engine == "sglang"
    assert eph.engine_args == {"tp-size": 2, "dtype": "bfloat16"}
    assert eph.engine_args is not base.engine_args  # copied, not shared


def test_ephemeral_deployment_engine_args_override():
    base = _dep(engine="vllm", engine_args={"tensor-parallel-size": 2})
    eph = build_ephemeral_deployment(base, name="bench-x", namespace="ns", overrides={"engine_args": {"tensor-parallel-size": 4}})
    assert eph.engine_args == {"tensor-parallel-size": 4}


def test_serving_snapshot_records_engine():
    snap = _serving_snapshot(_dep(engine="sglang", engine_args={"tp-size": 2}))
    assert snap["engine"] == "sglang"
    assert snap["engine_args"] == {"tp-size": 2}
    assert _serving_snapshot(_dep())["engine"] == "vllm"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_serving_engines.py -q`
Expected: 3 FAIL (`AttributeError: ... has no attribute 'engine'` for the ORM object; snapshot returns `"vllm"` and has no `engine_args`).

- [ ] **Step 3: Copy engine fields in the ephemeral builder**

In `backend/app/services/benchmark_serving.py`, add to the imports (after the `model_deployment_manifests` import block):

```python
from app.services.serving_engines import engine_of
```

In `build_ephemeral_deployment`, inside the `CustomModelDeployment(` call, after the `env=dict(base.env or {}),` line add:

```python
        engine=engine_of(base),
        engine_args=dict(getattr(base, "engine_args", None) or {}) or None,
```

After the `if ov.get("vllm_extra_args") is not None:` block add:

```python
    if ov.get("engine_args") is not None:
        dep.engine_args = dict(ov["engine_args"])
```

- [ ] **Step 4: Use `serve_argv` and record the engine in benchmarks.py**

In `backend/app/api/benchmarks.py` add to the imports (after the `model_deployment_manifests` import block):

```python
from app.services.serving_engines import engine_of, serve_argv as engine_serve_argv
```

In `_serving_snapshot`, replace `"engine": "vllm",` with:

```python
        "engine": engine_of(dep),
        "engine_args": dict(getattr(dep, "engine_args", None) or {}),
```

Replace both occurrences (lines ~566 and ~809) of:

```python
serve_argv = ["vllm", "serve", eph.model_path, "--port", str(VLLM_PORT), *(eph.vllm_extra_args or [])]
```

with:

```python
serve_argv = engine_serve_argv(eph, VLLM_PORT)
```

- [ ] **Step 5: Run the benchmark and engine tests**

Run: `uv run pytest tests/test_serving_engines.py tests/test_self_serving_bench.py tests/test_benchmark_manifests.py tests/test_benchmark_endpoint.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/benchmark_serving.py backend/app/api/benchmarks.py backend/tests/test_serving_engines.py
git commit -m "feat(benchmarks): ephemeral servings carry engine; serve argv from engine catalogue"
```

---

### Task 4: Recipe API accepts and returns engine fields

**Files:**
- Modify: `backend/app/api/serving_recipes.py:1-60`
- Test: `backend/tests/test_serving_recipes.py`

**Interfaces:**
- Consumes: `ServingEngine`, `validate_engine_args` from Task 2.
- Produces: `RecipeBody.engine: ServingEngine = "vllm"`, `RecipeBody.engine_args: dict[str, str | int | float | bool] | None = None`; `_serialize` returns `"engine"` and `"engine_args"`.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_serving_recipes.py`, update `_recipe()` so `base` also contains `engine="vllm", engine_args=None,` (add after `env=None,`). Then append:

```python
def test_serialize_includes_engine_fields():
    out = _serialize(_recipe(engine="sglang", engine_args={"tp-size": 2}))
    assert out["engine"] == "sglang"
    assert out["engine_args"] == {"tp-size": 2}
    assert _serialize(_recipe())["engine"] == "vllm"


async def test_create_sglang_recipe_round_trips_engine_args(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(scalar=None))
    body = {**_BODY, "engine": "sglang", "engine_args": {"tp-size": 2, "trust-remote-code": True, "mem-fraction-static": 0.85}}
    async with client_for_user(super_user) as client:
        resp = await client.post("/api/admin/serving-recipes", json=body)
    assert resp.status_code == 201
    out = resp.json()
    assert out["engine"] == "sglang"
    assert out["engine_args"] == {"tp-size": 2, "trust-remote-code": True, "mem-fraction-static": 0.85}


async def test_create_defaults_engine_to_vllm(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(scalar=None))
    async with client_for_user(super_user) as client:
        resp = await client.post("/api/admin/serving-recipes", json=_BODY)
    assert resp.status_code == 201
    assert resp.json()["engine"] == "vllm"
    assert resp.json()["engine_args"] is None


async def test_create_invalid_engine_422(client_for_user, super_user, mock_db):
    async with client_for_user(super_user) as client:
        resp = await client.post("/api/admin/serving-recipes", json={**_BODY, "engine": "tgi"})
    assert resp.status_code == 422


async def test_create_bad_engine_args_key_422(client_for_user, super_user, mock_db):
    async with client_for_user(super_user) as client:
        resp = await client.post("/api/admin/serving-recipes", json={**_BODY, "engine_args": {"--tp-size": 2}})
    assert resp.status_code == 422
    assert "--tp-size" in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_serving_recipes.py -q`
Expected: the five new tests FAIL (`KeyError: 'engine'` / status 201 with no engine key / 201 instead of 422).

- [ ] **Step 3: Extend the request model and serializer**

In `backend/app/api/serving_recipes.py`:

Change the module docstring to `"""Serving recipe CRUD (Super User only). Reusable vLLM/SGLang serving templates."""`.

Change the pydantic import to `from pydantic import BaseModel, Field, field_validator` and add after the `app.db.session` import:

```python
from app.services.serving_engines import ServingEngine, validate_engine_args
```

In `RecipeBody`, after `env: dict[str, str] | None = None` add:

```python
    engine: ServingEngine = "vllm"
    engine_args: dict[str, str | int | float | bool] | None = None

    @field_validator("engine_args")
    @classmethod
    def _check_engine_args(cls, v: dict | None) -> dict | None:
        return validate_engine_args(v)
```

In `_serialize`, after `"env": r.env,` add:

```python
        "engine": r.engine or "vllm",
        "engine_args": r.engine_args,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_serving_recipes.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/serving_recipes.py backend/tests/test_serving_recipes.py
git commit -m "feat(recipes): engine + engine_args in recipe API"
```

---

### Task 5: Deployment API accepts and returns engine fields

**Files:**
- Modify: `backend/app/api/model_deployments.py:26` (imports), `:33` (`DEFAULT_VLLM_IMAGE`), `:36-77` (request models), `:90-124` (`_serialize`), `:274-300` (create)
- Test: `backend/tests/test_model_deployments_engine.py` (new)

**Interfaces:**
- Consumes: `ServingEngine`, `validate_engine_args`, `default_image`, `DEFAULT_IMAGES` from Task 2.
- Produces: `CreateDeploymentRequest.engine`, `.engine_args`, `.image: str | None` (None → `default_image(engine)`); `UpdateDeploymentRequest.engine`, `.engine_args`; `_serialize` returns both fields.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_model_deployments_engine.py`:

```python
"""Engine fields on the deployment API request models and serializer."""

import types
import uuid

import pytest
from pydantic import ValidationError

from app.api.model_deployments import CreateDeploymentRequest, UpdateDeploymentRequest, _serialize


def _row(**kw):
    base = dict(
        id=uuid.uuid4(), model_name="m", cluster_id=None, namespace="ns", image="img", replicas=1,
        gpu_count=1, gpu_resource_key="nvidia.com/gpu", cpu_request=None, cpu_limit=None,
        memory_request=None, memory_limit=None, node_selector=None, tolerations=None,
        pvc_name=None, pvc_mount_path=None, model_path="/m", vllm_extra_args=None, env=None,
        ingress_host="h", ingress_path="/", ingress_class="nginx", status="Pending",
        status_message=None, ready_replicas=0, service_cluster_ip=None, litellm_model_id=None,
        last_synced_at=None, created_by=None, updated_by=None, created_at=None, updated_at=None,
        engine="vllm", engine_args=None,
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_create_request_defaults():
    req = CreateDeploymentRequest(model_name="m", model_path="/m", ingress_host="h")
    assert req.engine == "vllm"
    assert req.engine_args is None
    assert req.image is None


def test_create_request_accepts_sglang_args():
    req = CreateDeploymentRequest(model_name="m", model_path="/m", ingress_host="h", engine="sglang", engine_args={"tp-size": 2})
    assert req.engine == "sglang"
    assert req.engine_args == {"tp-size": 2}


def test_create_request_rejects_bad_engine_and_keys():
    with pytest.raises(ValidationError):
        CreateDeploymentRequest(model_name="m", model_path="/m", ingress_host="h", engine="tgi")
    with pytest.raises(ValidationError):
        CreateDeploymentRequest(model_name="m", model_path="/m", ingress_host="h", engine_args={"--tp-size": 2})


def test_update_request_engine_fields_optional():
    assert UpdateDeploymentRequest().model_dump(exclude_unset=True) == {}
    with pytest.raises(ValidationError):
        UpdateDeploymentRequest(engine_args={"Bad Key": 1})


def test_serialize_includes_engine_fields():
    out = _serialize(_row(engine="sglang", engine_args={"tp-size": 2}))
    assert out["engine"] == "sglang"
    assert out["engine_args"] == {"tp-size": 2}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_model_deployments_engine.py -q`
Expected: FAIL (`AttributeError: 'CreateDeploymentRequest' object has no attribute 'engine'`, image default is the vLLM string, `KeyError: 'engine'`).

- [ ] **Step 3: Extend the request models, serializer and create handler**

In `backend/app/api/model_deployments.py`:

Change the pydantic import to `from pydantic import BaseModel, Field, field_validator`. After the `model_deployment_manifests` import add:

```python
from app.services.serving_engines import DEFAULT_IMAGES, ServingEngine, default_image, validate_engine_args
```

Replace `DEFAULT_VLLM_IMAGE = "vllm/vllm-openai:latest"` with:

```python
DEFAULT_VLLM_IMAGE = DEFAULT_IMAGES["vllm"]  # kept for existing references
```

In `CreateDeploymentRequest`, change `image: str = DEFAULT_VLLM_IMAGE` to:

```python
    image: str | None = None  # None → default_image(engine)
```

and after `env: dict | None = None` add:

```python
    engine: ServingEngine = "vllm"
    engine_args: dict[str, str | int | float | bool] | None = None

    @field_validator("engine_args")
    @classmethod
    def _check_engine_args(cls, v: dict | None) -> dict | None:
        return validate_engine_args(v)
```

In `UpdateDeploymentRequest`, after `env: dict | None = None` add:

```python
    engine: ServingEngine | None = None
    engine_args: dict[str, str | int | float | bool] | None = None

    @field_validator("engine_args")
    @classmethod
    def _check_engine_args(cls, v: dict | None) -> dict | None:
        return validate_engine_args(v)
```

In `_serialize`, after `"env": d.env,` add:

```python
        "engine": d.engine or "vllm",
        "engine_args": d.engine_args,
```

In `create_deployment`, inside the `CustomModelDeployment(` call change `image=body.image,` to `image=body.image or default_image(body.engine),` and after `env=body.env,` add:

```python
        engine=body.engine,
        engine_args=body.engine_args,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_model_deployments_engine.py -q && uv run pytest -q`
Expected: new tests pass; full suite passes.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/model_deployments.py backend/tests/test_model_deployments_engine.py
git commit -m "feat(deployments): engine + engine_args on create/update; engine-aware default image"
```

---

### Task 6: Frontend types, engine catalogue, i18n, and minimal wiring

**Files:**
- Modify: `frontend/src/types/index.ts` (`ServingRecipe` ~751, `ServingRecipeInput`, `ModelDeployment` ~614, `CreateDeploymentBody` ~782)
- Create: `frontend/src/lib/serving-engines.ts`
- Modify: `frontend/src/components/serving-recipe-form.tsx` (`BLANK` + `toInput` only)
- Modify: `frontend/src/components/deploy-from-recipe-dialog.tsx` (body construction)
- Modify: `frontend/messages/en.json`, `frontend/messages/ko.json`

**Interfaces:**
- Produces (types): `ServingEngine = "vllm" | "sglang"`, `EngineArgs = Record<string, string | number | boolean>`; `engine: ServingEngine` and `engine_args: EngineArgs | null` on `ServingRecipe`, `ServingRecipeInput`, `ModelDeployment`, `CreateDeploymentBody`.
- Produces (`@/lib/serving-engines`): `ENGINES`, `ENGINE_DEFAULT_IMAGE`, `ENGINE_LABEL_KEY`, `EngineArgField`, `ENGINE_ARG_FIELDS`, `engineArgsToFlags(args)`, `argLabelKey(key)`.
- Produces (i18n, namespace `servingRecipes`): `engine`, `engineVllm`, `engineSglang`, `sectionEngineArgs`, `engineArgsHint`, `extraArgs`, `arg.<camelKey>` for every field key below. Namespace `adminDeployments`: `engine`, `extraArgs` relabelled to launch args.

- [ ] **Step 1: Extend the types**

In `frontend/src/types/index.ts`, just above `export interface ServingRecipe {` add:

```ts
export type ServingEngine = "vllm" | "sglang";
export type EngineArgs = Record<string, string | number | boolean>;
```

Inside `ServingRecipe`, after `env: Record<string, string> | null;` add:

```ts
  engine: ServingEngine;
  engine_args: EngineArgs | null;
```

`ServingRecipeInput` is `Omit<ServingRecipe, "id" | ...>` (line ~776), so it picks the new fields up automatically; no edit there.

Inside `ModelDeployment` and `CreateDeploymentBody`, after `env: Record<string, string> | null;` add the same two lines.

- [ ] **Step 2: Create the engine catalogue**

Create `frontend/src/lib/serving-engines.ts`:

```ts
import type { EngineArgs, ServingEngine } from "@/types";

/**
 * Curated "main" launch flags per engine, shown as dedicated fields in the
 * recipe form. `key` is the bare CLI flag name; the backend renders
 * `{key: value}` generically (`--key value`, `--key` for true), so adding a
 * field here needs no server change.
 */
export const ENGINES: ServingEngine[] = ["vllm", "sglang"];

export const ENGINE_DEFAULT_IMAGE: Record<ServingEngine, string> = {
  vllm: "vllm/vllm-openai:latest",
  sglang: "lmsysorg/sglang:latest",
};

export const ENGINE_LABEL_KEY: Record<ServingEngine, "engineVllm" | "engineSglang"> = {
  vllm: "engineVllm",
  sglang: "engineSglang",
};

export type EngineArgField =
  | { key: string; type: "int"; min?: number; placeholder?: string }
  | { key: string; type: "float"; step: number; min?: number; max?: number; placeholder?: string }
  | { key: string; type: "text"; placeholder?: string }
  | { key: string; type: "bool" }
  | { key: string; type: "select"; options: string[] }; // "" option = unset

const DTYPES = ["", "auto", "bfloat16", "float16", "float32"];

export const ENGINE_ARG_FIELDS: Record<ServingEngine, EngineArgField[]> = {
  vllm: [
    { key: "tensor-parallel-size", type: "int", min: 1, placeholder: "1" },
    { key: "pipeline-parallel-size", type: "int", min: 1, placeholder: "1" },
    { key: "max-model-len", type: "int", min: 1, placeholder: "8192" },
    { key: "gpu-memory-utilization", type: "float", step: 0.01, min: 0, max: 1, placeholder: "0.9" },
    { key: "dtype", type: "select", options: DTYPES },
    { key: "quantization", type: "select", options: ["", "fp8", "awq", "gptq", "gptq_marlin", "bitsandbytes"] },
    { key: "kv-cache-dtype", type: "select", options: ["", "auto", "fp8"] },
    { key: "max-num-seqs", type: "int", min: 1, placeholder: "256" },
    { key: "max-num-batched-tokens", type: "int", min: 1, placeholder: "8192" },
    { key: "served-model-name", type: "text", placeholder: "my-model" },
    { key: "enable-prefix-caching", type: "bool" },
    { key: "enable-chunked-prefill", type: "bool" },
    { key: "trust-remote-code", type: "bool" },
  ],
  sglang: [
    { key: "tp-size", type: "int", min: 1, placeholder: "1" },
    { key: "dp-size", type: "int", min: 1, placeholder: "1" },
    { key: "context-length", type: "int", min: 1, placeholder: "8192" },
    { key: "mem-fraction-static", type: "float", step: 0.01, min: 0, max: 1, placeholder: "0.88" },
    { key: "dtype", type: "select", options: DTYPES },
    { key: "quantization", type: "select", options: ["", "fp8", "awq", "gptq", "bitsandbytes"] },
    { key: "kv-cache-dtype", type: "select", options: ["", "auto", "fp8_e5m2", "fp8_e4m3"] },
    { key: "max-running-requests", type: "int", min: 1, placeholder: "256" },
    { key: "chunked-prefill-size", type: "int", min: 1, placeholder: "8192" },
    { key: "served-model-name", type: "text", placeholder: "my-model" },
    { key: "disable-radix-cache", type: "bool" },
    { key: "trust-remote-code", type: "bool" },
    { key: "enable-torch-compile", type: "bool" },
  ],
};

/** i18n key for a flag's label: `tp-size` → `arg.tpSize`. */
export function argLabelKey(key: string): string {
  return "arg." + key.replace(/-([a-z0-9])/g, (_, c: string) => c.toUpperCase());
}

/** Mirror of the backend renderer, for display only (sorted keys; true → bare flag). */
export function engineArgsToFlags(args: EngineArgs | null | undefined): string[] {
  const out: string[] = [];
  for (const key of Object.keys(args ?? {}).sort()) {
    const value = (args as EngineArgs)[key];
    if (value === false || value === "" || value == null) continue;
    if (value === true) out.push(`--${key}`);
    else out.push(`--${key}`, String(value));
  }
  return out;
}
```

- [ ] **Step 3: Keep the form and deploy dialog compiling**

In `frontend/src/components/serving-recipe-form.tsx`, extend `BLANK` so it ends:

```ts
  pvc_name: null, pvc_mount_path: null, vllm_extra_args: null, env: null,
  engine: "vllm", engine_args: null,
};
```

(`toInput` spreads the recipe so it already carries the new fields.)

In `frontend/src/components/deploy-from-recipe-dialog.tsx`, inside the `body: CreateDeploymentBody = {` literal, after `env: recipe.env,` add:

```ts
      engine: recipe.engine,
      engine_args: recipe.engine_args,
```

- [ ] **Step 4: Add i18n keys**

Run this from `frontend/` to insert the keys into both message files (it appends inside the `servingRecipes` block and edits `adminDeployments`):

```bash
python3 - <<'EOF'
import json, pathlib, re

def camel(k): return re.sub(r"-([a-z0-9])", lambda m: m.group(1).upper(), k)

ARG_LABELS = {
  "en": {
    "tensor-parallel-size": "Tensor parallel size", "pipeline-parallel-size": "Pipeline parallel size",
    "max-model-len": "Max model length", "gpu-memory-utilization": "GPU memory utilization",
    "dtype": "dtype", "quantization": "Quantization", "kv-cache-dtype": "KV cache dtype",
    "max-num-seqs": "Max concurrent sequences", "max-num-batched-tokens": "Max batched tokens",
    "served-model-name": "Served model name", "enable-prefix-caching": "Prefix caching",
    "enable-chunked-prefill": "Chunked prefill", "trust-remote-code": "Trust remote code",
    "tp-size": "Tensor parallel size", "dp-size": "Data parallel size", "context-length": "Context length",
    "mem-fraction-static": "Static memory fraction", "max-running-requests": "Max running requests",
    "chunked-prefill-size": "Chunked prefill size", "disable-radix-cache": "Disable radix cache",
    "enable-torch-compile": "Torch compile",
  },
  "ko": {
    "tensor-parallel-size": "텐서 병렬 크기", "pipeline-parallel-size": "파이프라인 병렬 크기",
    "max-model-len": "최대 모델 길이", "gpu-memory-utilization": "GPU 메모리 사용 비율",
    "dtype": "dtype", "quantization": "양자화", "kv-cache-dtype": "KV 캐시 dtype",
    "max-num-seqs": "최대 동시 시퀀스 수", "max-num-batched-tokens": "최대 배치 토큰 수",
    "served-model-name": "서빙 모델명", "enable-prefix-caching": "프리픽스 캐싱",
    "enable-chunked-prefill": "청크 프리필", "trust-remote-code": "원격 코드 신뢰",
    "tp-size": "텐서 병렬 크기", "dp-size": "데이터 병렬 크기", "context-length": "컨텍스트 길이",
    "mem-fraction-static": "정적 메모리 비율", "max-running-requests": "최대 동시 요청 수",
    "chunked-prefill-size": "청크 프리필 크기", "disable-radix-cache": "Radix 캐시 비활성화",
    "enable-torch-compile": "Torch compile",
  },
}
RECIPE = {
  "en": {"engine": "Engine", "engineVllm": "vLLM", "engineSglang": "SGLang",
         "sectionEngineArgs": "Engine arguments",
         "engineArgsHint": "Leave a field empty to use the engine default. Anything not listed goes in extra args.",
         "extraArgs": "Extra args (one per line)"},
  "ko": {"engine": "엔진", "engineVllm": "vLLM", "engineSglang": "SGLang",
         "sectionEngineArgs": "엔진 인자",
         "engineArgsHint": "비워두면 엔진 기본값을 사용합니다. 목록에 없는 플래그는 추가 인자에 넣으세요.",
         "extraArgs": "추가 인자 (줄바꿈으로 구분)"},
}
DEPLOY = {
  "en": {"engine": "Engine", "extraArgs": "Launch args"},
  "ko": {"engine": "엔진", "extraArgs": "실행 인자"},
}

for lang in ("en", "ko"):
    p = pathlib.Path(f"messages/{lang}.json")
    data = json.loads(p.read_text(encoding="utf-8"))
    sr = data["servingRecipes"]
    sr.update(RECIPE[lang])
    sr["vllmArgs"] = {"en": "Extra args (one per line)", "ko": "추가 인자 (줄바꿈으로 구분)"}[lang]
    sr["arg"] = {camel(k): v for k, v in ARG_LABELS[lang].items()}
    data["adminDeployments"].update(DEPLOY[lang])
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(lang, "ok")
EOF
git diff --stat messages/
```

Check the diff stat: only the two message files change, and the change count is small (tens of lines, not the whole file). If `git diff` shows the whole file rewritten, the original formatting differs from `indent=2`; revert with `git checkout messages/` and instead insert the keys by hand with the same text.

- [ ] **Step 5: Typecheck and lint**

Run (from `frontend/`): `npx tsc --noEmit -p . && npx eslint src/lib/serving-engines.ts src/components/serving-recipe-form.tsx src/components/deploy-from-recipe-dialog.tsx src/types/index.ts`
Expected: no output (both clean). If `tsc` reports other places constructing `ServingRecipe`/`ModelDeployment`/`CreateDeploymentBody` literals without the new fields, add `engine: "vllm", engine_args: null` there.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/lib/serving-engines.ts frontend/src/components/serving-recipe-form.tsx frontend/src/components/deploy-from-recipe-dialog.tsx frontend/messages/en.json frontend/messages/ko.json
git commit -m "feat(frontend): serving engine types, engine catalogue, and i18n"
```

---

### Task 7: Recipe form engine toggle + structured engine args; list badge

**Files:**
- Modify: `frontend/src/components/serving-recipe-form.tsx` (Basics card, new Engine args card, Advanced card)
- Modify: `frontend/src/app/(app)/admin/recipes/page.tsx` (name cell)
- Modify: `frontend/src/components/deploy-from-recipe-dialog.tsx` (description line)

**Interfaces:**
- Consumes: everything from Task 6.
- Produces: form state field `form.engine`, `form.engine_args`; component `EngineArgsFields` (internal).

- [ ] **Step 1: Add the engine toggle to the Basics card**

In `serving-recipe-form.tsx` add imports:

```ts
import type { EngineArgs, ServingEngine, ServingRecipe, ServingRecipeInput } from "@/types";
import { Badge } from "@/components/ui/badge";
import {
  ENGINES, ENGINE_ARG_FIELDS, ENGINE_DEFAULT_IMAGE, ENGINE_LABEL_KEY, argLabelKey, type EngineArgField,
} from "@/lib/serving-engines";
```

(Replace the existing `import type { ServingRecipe, ServingRecipeInput } from "@/types";` line.)

Inside `ServingRecipeForm`, after the `optText` helper add:

```ts
  function switchEngine(next: ServingEngine) {
    setForm((f) => {
      if (f.engine === next) return f;
      const otherDefault = ENGINE_DEFAULT_IMAGE[f.engine];
      const image = !f.image.trim() || f.image === otherDefault ? ENGINE_DEFAULT_IMAGE[next] : f.image;
      // Structured args are engine-specific; free-text extra args are the user's and stay.
      return { ...f, engine: next, engine_args: null, image };
    });
  }

  function setArg(key: string, value: string | number | boolean | undefined) {
    setForm((f) => {
      const next: EngineArgs = { ...(f.engine_args ?? {}) };
      if (value === undefined || value === "" || value === false) delete next[key];
      else next[key] = value;
      return { ...f, engine_args: Object.keys(next).length ? next : null };
    });
  }
```

In the Basics card, make the engine control the first row by inserting before the `name` field:

```tsx
          <Field id="recipe-engine" label={t("engine")} span2>
            <div id="recipe-engine" role="radiogroup" className="inline-flex rounded-md border p-0.5">
              {ENGINES.map((e) => (
                <button
                  key={e}
                  type="button"
                  role="radio"
                  aria-checked={form.engine === e}
                  onClick={() => switchEngine(e)}
                  className={
                    "rounded px-3 py-1 text-sm transition-colors " +
                    (form.engine === e ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground")
                  }
                >
                  {t(ENGINE_LABEL_KEY[e])}
                </button>
              ))}
            </div>
          </Field>
```

- [ ] **Step 2: Add the Engine args card and move extra args into it**

Between the Storage card and the Advanced card insert:

```tsx
      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("sectionEngineArgs")}</CardTitle>
          <p className="text-xs text-muted-foreground">{t("engineArgsHint")}</p>
        </CardHeader>
        <CardContent className="space-y-4">
          <EngineArgsFields engine={form.engine} values={form.engine_args} onChange={setArg} />
          <Field id="recipe-args" label={t("extraArgs")}>
            <Area
              id="recipe-args" value={argsText} onChange={setArgsText}
              placeholder={form.engine === "sglang" ? "--log-level=info\n--schedule-policy=lpm" : "--max-model-len=8192\n--tensor-parallel-size=2"}
            />
          </Field>
        </CardContent>
      </Card>
```

Delete the `vllmArgs` `Field`/`Area` block from the Advanced card (env and node selector stay).

Add the component at the bottom of the file:

```tsx
function EngineArgsFields({
  engine, values, onChange,
}: { engine: ServingEngine; values: EngineArgs | null; onChange: (key: string, value: string | number | boolean | undefined) => void }) {
  const t = useTranslations("servingRecipes");
  const fields = ENGINE_ARG_FIELDS[engine];
  const scalar = fields.filter((f) => f.type !== "bool");
  const bools = fields.filter((f) => f.type === "bool");
  const get = (key: string) => values?.[key];

  const inputFor = (f: EngineArgField) => {
    const id = `recipe-arg-${f.key}`;
    switch (f.type) {
      case "int":
        return (
          <Input
            id={id} type="number" step={1} min={f.min} placeholder={f.placeholder}
            value={get(f.key) ?? ""}
            onChange={(e) => onChange(f.key, e.target.value === "" ? undefined : Number(e.target.value))}
          />
        );
      case "float":
        return (
          <Input
            id={id} type="number" step={f.step} min={f.min} max={f.max} placeholder={f.placeholder}
            value={get(f.key) ?? ""}
            onChange={(e) => onChange(f.key, e.target.value === "" ? undefined : Number(e.target.value))}
          />
        );
      case "text":
        return (
          <Input id={id} placeholder={f.placeholder} value={String(get(f.key) ?? "")} onChange={(e) => onChange(f.key, e.target.value)} />
        );
      case "select":
        return (
          <select
            id={id}
            value={String(get(f.key) ?? "")}
            onChange={(e) => onChange(f.key, e.target.value)}
            className="w-full h-9 rounded-md border border-input bg-transparent px-3 text-sm"
          >
            {f.options.map((o) => <option key={o} value={o}>{o === "" ? "—" : o}</option>)}
          </select>
        );
      default:
        return null;
    }
  };

  return (
    <>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {scalar.map((f) => (
          <div key={f.key} className="space-y-2">
            <Label htmlFor={`recipe-arg-${f.key}`}>
              {t(argLabelKey(f.key))}
              <span className="ml-2 font-mono text-[11px] text-muted-foreground">--{f.key}</span>
            </Label>
            {inputFor(f)}
          </div>
        ))}
      </div>
      <div className="flex flex-wrap gap-x-6 gap-y-2">
        {bools.map((f) => (
          <label key={f.key} className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={get(f.key) === true} onChange={(e) => onChange(f.key, e.target.checked)} />
            {t(argLabelKey(f.key))}
            <span className="font-mono text-[11px] text-muted-foreground">--{f.key}</span>
          </label>
        ))}
      </div>
    </>
  );
}
```

- [ ] **Step 3: Engine badge in the recipe list and deploy dialog**

In `frontend/src/app/(app)/admin/recipes/page.tsx` add `import { Badge } from "@/components/ui/badge";` and change the name cell to:

```tsx
                  <TableCell className="font-medium">
                    <span className="inline-flex items-center gap-2">
                      <Badge variant="secondary" className="font-mono text-[10px] uppercase">{r.engine}</Badge>
                      {r.name}
                    </span>
                  </TableCell>
```

In `deploy-from-recipe-dialog.tsx` change the `DialogDescription` text expression to:

```tsx
            {recipe ? `${recipe.engine} · ${recipe.model_path} · ${recipe.image} · ${recipe.gpu_count}×${recipe.gpu_resource_key}` : ""}
```

- [ ] **Step 4: Typecheck and lint**

Run (from `frontend/`): `npx tsc --noEmit -p . && npx eslint src/components/serving-recipe-form.tsx 'src/app/(app)/admin/recipes' src/components/deploy-from-recipe-dialog.tsx`
Expected: clean.

- [ ] **Step 5: Browser check with mocked API**

Start the dev server on a free port (3000 belongs to another project on this machine):

```bash
cd frontend && (PORT=3100 npx next dev -p 3100 > /tmp/recipe-next.log 2>&1 &) && sleep 4 && tail -2 /tmp/recipe-next.log
```

Then run this Playwright snippet (via the Playwright MCP `browser_run_code_unsafe` tool, or `npx playwright` if preferred). It sets the session cookie the middleware checks, mocks `/api/proxy/me` and the recipe list, creates an SGLang recipe and an edited vLLM recipe, and returns the captured request bodies:

```js
async (page) => {
  const ctx = page.context();
  await ctx.addCookies([{ name: 'litellm_session', value: 'dev', domain: 'localhost', path: '/' }]);
  const recipe = { id: 'r-1', name: 'llama-70b', description: null, model_path: '/models/llama', image: 'vllm/vllm-openai:latest',
    gpu_count: 4, gpu_resource_key: 'nvidia.com/gpu', cpu_request: null, cpu_limit: null, memory_request: null, memory_limit: null,
    node_selector: null, tolerations: null, pvc_name: null, pvc_mount_path: null, vllm_extra_args: ['--x'], env: null,
    engine: 'vllm', engine_args: { 'tensor-parallel-size': 4, 'enable-prefix-caching': true },
    created_by: null, updated_by: null, created_at: null, updated_at: null };
  await ctx.route('**/api/proxy/**', r => r.fulfill({ json: {} }));
  await ctx.route('**/api/proxy/me', r => r.fulfill({ json: { user_id: 'admin001', role: 'super_user', locale: 'ko', teams: [] } }));
  await ctx.route('**/api/proxy/admin/serving-recipes', r => r.fulfill({ json: { recipes: [recipe] } }));
  const captured = [];
  page.on('request', r => { if (/serving-recipes/.test(r.url()) && r.method() !== 'GET') captured.push({ m: r.method(), body: JSON.parse(r.postData()) }); });
  await page.setViewportSize({ width: 1440, height: 1000 });

  await page.goto('http://localhost:3100/admin/recipes/new', { waitUntil: 'load' }); await page.waitForTimeout(1500);
  await page.getByRole('radio', { name: 'SGLang' }).click();
  const imageAfterSwitch = await page.inputValue('#recipe-image');
  await page.fill('#recipe-name', 'qwen-sglang'); await page.fill('#recipe-model-path', '/models/qwen');
  await page.fill('#recipe-arg-tp-size', '2'); await page.fill('#recipe-arg-mem-fraction-static', '0.85');
  await page.selectOption('#recipe-arg-dtype', 'bfloat16');
  await page.getByLabel(/원격 코드 신뢰|Trust remote code/).check();
  await page.fill('#recipe-args', '--log-level=info');
  await page.screenshot({ path: '/tmp/recipe-sglang-form.png', fullPage: false });
  await page.getByRole('button', { name: '생성' }).click(); await page.waitForTimeout(1000);

  await page.goto('http://localhost:3100/admin/recipes/r-1', { waitUntil: 'load' }); await page.waitForTimeout(1500);
  const tpBefore = await page.inputValue('#recipe-arg-tensor-parallel-size');
  await page.fill('#recipe-arg-tensor-parallel-size', '8');
  await page.getByRole('button', { name: '저장' }).click(); await page.waitForTimeout(1000);
  return JSON.stringify({ imageAfterSwitch, tpBefore, captured }, null, 1);
}
```

Expected:
- `imageAfterSwitch` is `lmsysorg/sglang:latest`.
- First captured body: `m: "POST"`, `engine: "sglang"`, `image: "lmsysorg/sglang:latest"`, `engine_args: {"tp-size": 2, "mem-fraction-static": 0.85, "dtype": "bfloat16", "trust-remote-code": true}` (numbers, not strings), `vllm_extra_args: ["--log-level=info"]`.
- `tpBefore` is `"4"`; second body: `m: "PUT"`, `engine: "vllm"`, `engine_args: {"tensor-parallel-size": 8, "enable-prefix-caching": true}`.

Look at `/tmp/recipe-sglang-form.png` and confirm the toggle, the Engine args card with a 2-column grid and a checkbox row, and the extra args textarea below them. Stop the dev server afterwards: `kill $(lsof -nP -iTCP:3100 -sTCP:LISTEN -t)`.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/serving-recipe-form.tsx 'frontend/src/app/(app)/admin/recipes/page.tsx' frontend/src/components/deploy-from-recipe-dialog.tsx
git commit -m "feat(recipes): engine toggle and structured engine args in the recipe form"
```

---

### Task 8: Deployments list and detail show the engine

**Files:**
- Modify: `frontend/src/app/(app)/admin/deployments/page.tsx:101-109` (name cell)
- Modify: `frontend/src/app/(app)/admin/deployments/[id]/page.tsx:1-20` (imports), `:84-90` (header), `:133-138` (args block)

**Interfaces:**
- Consumes: `engineArgsToFlags` from Task 6; `ModelDeployment.engine`, `.engine_args`.

- [ ] **Step 1: Badge in the deployments list**

In `deployments/page.tsx`, inside the model-name `TableCell`, after the closing `</Link>` add:

```tsx
                        <Badge variant="secondary" className="ml-2 font-mono text-[10px] uppercase">{d.engine}</Badge>
```

(`Badge` is already imported there.)

- [ ] **Step 2: Engine badge and launch args on the detail page**

In `deployments/[id]/page.tsx` add `import { engineArgsToFlags } from "@/lib/serving-engines";`.

In the header, after `<StatusBadge status={dep.status} />` add:

```tsx
            <Badge variant="secondary" className="font-mono text-[10px] uppercase">{dep.engine}</Badge>
```

Replace the `vllm_extra_args` block:

```tsx
          {dep.vllm_extra_args && dep.vllm_extra_args.length > 0 && (
            <div className="mt-4 space-y-1">
              <div className="text-xs text-muted-foreground">{t("extraArgs")}</div>
              <code className="block rounded-md border bg-muted/40 p-2 text-xs font-mono">{dep.vllm_extra_args.join(" ")}</code>
            </div>
          )}
```

with:

```tsx
          {(() => {
            const flags = [...engineArgsToFlags(dep.engine_args), ...(dep.vllm_extra_args ?? [])];
            return flags.length > 0 ? (
              <div className="mt-4 space-y-1">
                <div className="text-xs text-muted-foreground">{t("extraArgs")}</div>
                <code className="block rounded-md border bg-muted/40 p-2 text-xs font-mono">{flags.join(" ")}</code>
              </div>
            ) : null;
          })()}
```

- [ ] **Step 3: Typecheck and lint**

Run (from `frontend/`): `npx tsc --noEmit -p . && npx eslint 'src/app/(app)/admin/deployments'`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add 'frontend/src/app/(app)/admin/deployments/page.tsx' 'frontend/src/app/(app)/admin/deployments/[id]/page.tsx'
git commit -m "feat(deployments): show engine and rendered launch args"
```

---

### Task 9: Full verification

**Files:** none new.

- [ ] **Step 1: Backend suite**

Run (from `backend/`): `uv run pytest -q`
Expected: all pass, no new warnings about the changed modules.

- [ ] **Step 2: Frontend typecheck, lint, build**

Run (from `frontend/`): `npx tsc --noEmit -p . && npx eslint src && npx next build 2>&1 | tail -15`
Expected: `tsc`/`eslint` clean; the build lists `/admin/recipes/new` and `/admin/recipes/[id]` as routes and finishes without errors. (If `next build` needs env vars this machine lacks, report that and rely on `tsc` + the Task 7 browser check.)

- [ ] **Step 3: Confirm the working tree is clean**

Run: `git status --short`
Expected: nothing outside the `litellm` submodule (that submodule's `uv.lock` / `.omc/` changes were pre-existing and are not part of this work).
