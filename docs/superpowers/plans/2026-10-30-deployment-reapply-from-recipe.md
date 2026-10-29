# Deployment re-apply from recipe + zero-downtime rollout settings — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A "레시피에서 다시 적용" action on the deployment detail page that overlays the recipe's serving fields onto the deployment and rolls it without interruption, plus explicit rollout / graceful-termination settings in every serving Deployment manifest.

**Architecture:** Rollout settings live in the existing `runtime` JSONB (recipe + deployment, inherited per P/D pool) and are rendered by `model_deployment_manifests` through a new pure module `serving_rollout`. A new pure module `recipe_reapply` computes which deployment fields a recipe owns and the diff; two endpoints (`GET …/recipe-diff`, `POST …/reapply`) use it and share the existing PUT's apply path, extracted into `_apply_update`. The frontend adds a dialog, two hooks, three runtime form fields and one detail-page field.

**Tech Stack:** FastAPI + pydantic v2 + SQLAlchemy async (backend, pytest), Next.js 16 + TanStack Query + next-intl (frontend, tsc/eslint).

**Spec:** `docs/superpowers/specs/2026-10-30-deployment-reapply-from-recipe-design.md`

## Global Constraints

- Rollout runtime keys: `rollout` ∈ {`surge`, `replace`} (default `surge`); `termination_grace_seconds` 1..3600 (default 120); `prestop_sleep_seconds` 0..600 (default 10).
- Strategy is always `RollingUpdate`; `surge` → maxSurge 1 / maxUnavailable 0; `replace` → maxSurge 0 / maxUnavailable 1. Never `Recreate`.
- `replace` with exactly 1 replica (aggregated `replicas`, or any P/D pool's replicas) is rejected with HTTP 400; 0 replicas passes.
- Recipe-owned fields (the only ones re-apply overwrites): `image, model_path, engine, engine_args, vllm_extra_args, env, gpu_count, gpu_resource_key, cpu_request, cpu_limit, memory_request, memory_limit, node_selector, tolerations, storages, probes, runtime, pd_config`. `pd_config.prefill.replicas` / `pd_config.decode.replicas` keep the deployment's values.
- Never changed by re-apply: `model_name, litellm_model_name, cluster_id, namespace, replicas, ingress_host, ingress_path, ingress_class, gpu_type, serving_mode, router_stack_id`.
- Backend test baseline: 21 pre-existing failures need Postgres; run targeted files. venv: `backend/.venv/bin/python -m pytest`.
- Commit messages end with the session's attribution lines; commit only, do not push until the user asks.
- PR #247 (`feat/runtime-extra-ports`) also edits `Runtime`, `validate_runtime`, `pool-config-cards.tsx` and `RuntimeOptions`; keep the new fields in separate lines/blocks so the later rebase is mechanical.

## Review Focus

1. A deployment whose `runtime` is `null` must render the defaults (strategy, grace 120, preStop sleep 10) — pinned in Task 2 `test_build_deployment_renders_rollout_defaults`.
2. P/D: a pool with its own `runtime.rollout` overrides the base; the other pool keeps the base — pinned in Task 2 `test_pd_pool_runtime_overrides_rollout`.
3. Re-apply must re-run the GPU profile after overwriting `node_selector`, or the profile's node label disappears — pinned in Task 4 `test_reapply_reapplies_gpu_profile`.
4. Re-apply must not drop a P/D pool's replica count when the recipe's `pd_config` has none — pinned in Task 4 `test_recipe_values_keeps_pool_replicas`.
5. A recipe of the other serving mode must be refused before anything is written — pinned in Task 4 `test_reapply_refuses_other_serving_mode`.

---

### Task 1: Rollout settings — schema and pure helpers

**Files:**
- Create: `backend/app/services/serving_rollout.py`
- Modify: `backend/app/services/pd_serving.py:74-84` (`Runtime`), `:340-350` (`validate_runtime`)
- Test: `backend/tests/test_serving_rollout.py`

**Interfaces:**
- Consumes: `pd_serving.role_view(dep, role).replicas / .gpu_count / .pool`, `pd_serving.is_pd(dep)`, `pd_serving.ROLES`.
- Produces (module `app.services.serving_rollout`):
  - `SURGE = "surge"`, `REPLACE = "replace"`, `DEFAULT_TERMINATION_GRACE_SECONDS = 120`, `DEFAULT_PRESTOP_SLEEP_SECONDS = 10`
  - `mode(runtime: dict | None) -> str`
  - `strategy(runtime: dict | None) -> dict`
  - `termination_grace_seconds(runtime: dict | None) -> int`
  - `lifecycle(runtime: dict | None) -> dict | None` — `{"preStop": {"exec": {"command": ["sh", "-c", "sleep <n>"]}}}`, `None` when the sleep is 0
  - `check_rollout(dep) -> None` — raises `ValueError` (`"<what>: rollout 'replace' needs at least 2 replicas (got 1)"`, `what` = `"replicas"` or the P/D role)
  - `surge_gpus(dep) -> int` — GPUs needed on top of the running set during a rollout: aggregated `gpu_count` when mode is `surge`, else 0; P/D: sum over roles of the role's `gpu_count` when that pool's runtime mode is `surge`
  - `Runtime` gains `rollout: Literal["surge", "replace"] | None = None`, `termination_grace_seconds: int | None = Field(None, ge=1, le=3600)`, `prestop_sleep_seconds: int | None = Field(None, ge=0, le=600)`

- [ ] **Step 1: Write the failing tests** in `backend/tests/test_serving_rollout.py`

```python
"""Rollout / graceful-termination settings: schema, defaults, manifest rendering, guard."""

import types

import pytest

from app.services import serving_rollout as ro
from app.services.pd_serving import validate_runtime
from tests.test_pd_serving import _dep as _pd_dep


def _dep(**kw):
    base = dict(
        id="00000000-0000-0000-0000-000000000001", model_name="m", namespace="ns", image="img", replicas=1,
        gpu_count=4, gpu_resource_key="nvidia.com/gpu", cpu_request=None, cpu_limit=None, memory_request=None,
        memory_limit=None, node_selector=None, tolerations=None, storages=[], model_path="/w/m",
        vllm_extra_args=None, env=None, engine="vllm", engine_args=None, probes=None, runtime=None,
        serving_mode="aggregated", pd_config=None, ingress_host="x", ingress_path="/", ingress_class="nginx",
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_defaults_when_runtime_is_empty():
    assert ro.mode(None) == "surge"
    assert ro.strategy(None) == {"type": "RollingUpdate", "rollingUpdate": {"maxSurge": 1, "maxUnavailable": 0}}
    assert ro.termination_grace_seconds(None) == 120
    assert ro.lifecycle(None) == {"preStop": {"exec": {"command": ["sh", "-c", "sleep 10"]}}}
    assert ro.lifecycle({}) == ro.lifecycle(None)


def test_replace_strategy_and_overrides():
    assert ro.strategy({"rollout": "replace"}) == {
        "type": "RollingUpdate", "rollingUpdate": {"maxSurge": 0, "maxUnavailable": 1},
    }
    assert ro.termination_grace_seconds({"termination_grace_seconds": 300}) == 300
    assert ro.lifecycle({"prestop_sleep_seconds": 25})["preStop"]["exec"]["command"] == ["sh", "-c", "sleep 25"]
    assert ro.lifecycle({"prestop_sleep_seconds": 0}) is None


def test_validate_runtime_accepts_rollout_fields():
    value = {"rollout": "replace", "termination_grace_seconds": 60, "prestop_sleep_seconds": 0}
    assert validate_runtime(value) == value
    assert validate_runtime({"rollout": "surge"}) == {"rollout": "surge"}


@pytest.mark.parametrize(
    "bad",
    [{"rollout": "recreate"}, {"termination_grace_seconds": 0}, {"termination_grace_seconds": 3601},
     {"prestop_sleep_seconds": -1}, {"prestop_sleep_seconds": 601}],
)
def test_validate_runtime_rejects_bad_rollout_fields(bad):
    with pytest.raises(ValueError):
        validate_runtime(bad)


def test_check_rollout_blocks_replace_with_one_replica():
    with pytest.raises(ValueError, match="replicas: rollout 'replace' needs at least 2 replicas"):
        ro.check_rollout(_dep(replicas=1, runtime={"rollout": "replace"}))
    ro.check_rollout(_dep(replicas=2, runtime={"rollout": "replace"}))
    ro.check_rollout(_dep(replicas=0, runtime={"rollout": "replace"}))
    ro.check_rollout(_dep(replicas=1))
    ro.check_rollout(_dep(replicas=1, runtime={"rollout": "surge"}))


def test_check_rollout_pd_checks_each_pool():
    dep = _pd_dep(runtime={"rollout": "replace"})  # prefill replicas 2, decode replicas 1
    with pytest.raises(ValueError, match="decode: rollout 'replace'"):
        ro.check_rollout(dep)
    dep = _pd_dep(runtime={"rollout": "replace"})
    dep.pd_config["decode"]["replicas"] = 2
    ro.check_rollout(dep)


def test_surge_gpus():
    assert ro.surge_gpus(_dep(gpu_count=4)) == 4
    assert ro.surge_gpus(_dep(gpu_count=4, runtime={"rollout": "replace"})) == 0
    assert ro.surge_gpus(_pd_dep()) == 16  # prefill 8 + decode 8
    dep = _pd_dep()
    dep.pd_config["decode"]["runtime"] = {"rollout": "replace"}
    assert ro.surge_gpus(dep) == 8
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_serving_rollout.py -q`
Expected: FAIL with `ModuleNotFoundError: app.services.serving_rollout`.

- [ ] **Step 3: Add the three fields to `Runtime` in `pd_serving.py`** (after `extra_resources`, each on its own line; import `Literal` from `typing`). `validate_runtime` needs no change: `exclude_none=True` already drops unset fields.

- [ ] **Step 4: Create `backend/app/services/serving_rollout.py`** with the constants and functions in Interfaces. `check_rollout` and `surge_gpus` import `pd_serving` at module level (no cycle: `pd_serving` never imports this module). For P/D read each pool's runtime from `role_view(dep, role).pool.runtime`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_serving_rollout.py tests/test_pd_serving.py -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/serving_rollout.py backend/app/services/pd_serving.py backend/tests/test_serving_rollout.py
git commit -m "feat(serving): rollout / graceful-termination settings in runtime"
```

---

### Task 2: Render strategy, terminationGracePeriodSeconds and preStop in the manifests

**Files:**
- Modify: `backend/app/services/model_deployment_manifests.py:127-230` (`build_container`, `build_pod_spec`, `_deployment`, `build_deployment`), `:233-300` (`build_pd_deployments`)
- Test: `backend/tests/test_serving_rollout.py`

**Interfaces:**
- Consumes: Task 1 helpers; `_runtime(row)`.
- Produces: `_deployment(..., strategy: dict)` keyword (required); every Deployment manifest has `spec.strategy`, `spec.template.spec.terminationGracePeriodSeconds`, and the serving container has `lifecycle` unless preStop sleep is 0.

- [ ] **Step 1: Append the failing tests** to `backend/tests/test_serving_rollout.py`

```python
from app.services.model_deployment_manifests import build_deployment, build_pd_deployments


def test_build_deployment_renders_rollout_defaults():
    m = build_deployment(_dep())
    assert m["spec"]["strategy"] == {"type": "RollingUpdate", "rollingUpdate": {"maxSurge": 1, "maxUnavailable": 0}}
    pod = m["spec"]["template"]["spec"]
    assert pod["terminationGracePeriodSeconds"] == 120
    assert pod["containers"][0]["lifecycle"] == {"preStop": {"exec": {"command": ["sh", "-c", "sleep 10"]}}}


def test_build_deployment_renders_runtime_rollout_overrides():
    m = build_deployment(_dep(replicas=2, runtime={"rollout": "replace", "termination_grace_seconds": 45, "prestop_sleep_seconds": 0}))
    assert m["spec"]["strategy"]["rollingUpdate"] == {"maxSurge": 0, "maxUnavailable": 1}
    pod = m["spec"]["template"]["spec"]
    assert pod["terminationGracePeriodSeconds"] == 45
    assert "lifecycle" not in pod["containers"][0]


def test_pd_pool_runtime_overrides_rollout():
    dep = _pd_dep(runtime={"termination_grace_seconds": 200})
    dep.pd_config["decode"]["replicas"] = 2
    dep.pd_config["decode"]["runtime"] = {"rollout": "replace"}
    prefill, decode = build_pd_deployments(dep)
    assert prefill["spec"]["strategy"]["rollingUpdate"] == {"maxSurge": 1, "maxUnavailable": 0}
    assert decode["spec"]["strategy"]["rollingUpdate"] == {"maxSurge": 0, "maxUnavailable": 1}
    for m in (prefill, decode):
        pod = m["spec"]["template"]["spec"]
        assert pod["terminationGracePeriodSeconds"] == 200
        assert pod["containers"][0]["lifecycle"]["preStop"]["exec"]["command"] == ["sh", "-c", "sleep 10"]
    # the decode init sidecar gets no preStop hook
    assert "lifecycle" not in decode["spec"]["template"]["spec"]["initContainers"][0]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_serving_rollout.py -q -k "build_deployment or pd_pool"`
Expected: FAIL with `KeyError: 'strategy'`.

- [ ] **Step 3: Implement in `model_deployment_manifests.py`**: import `serving_rollout`; `build_container` sets `container["lifecycle"]` from `serving_rollout.lifecycle(_runtime(dep))` when not None; `build_pod_spec` sets `pod_spec["terminationGracePeriodSeconds"] = serving_rollout.termination_grace_seconds(_runtime(dep))`; `_deployment` takes `strategy: dict` and writes `spec["strategy"]`; `build_deployment` passes `strategy=serving_rollout.strategy(_runtime(dep))`; `build_pd_deployments` passes `strategy=serving_rollout.strategy(_runtime(pool))`.

- [ ] **Step 4: Run the manifest-related suites**

Run: `cd backend && .venv/bin/python -m pytest tests/test_serving_rollout.py tests/test_serving_probes.py tests/test_pd_serving.py tests/test_storage_manifests.py tests/test_recipe_import.py tests/test_recipe_preview.py tests/test_self_serving_bench.py tests/test_benchmark_manifests.py tests/test_llmd_links.py -q`
Expected: all PASS (the importer reads `containers[0]` fields it knows and ignores `lifecycle`; verify, and if a test asserts an exact container dict, add the key to its expectation).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/model_deployment_manifests.py backend/tests/test_serving_rollout.py
git commit -m "feat(serving): explicit RollingUpdate strategy, termination grace and preStop sleep in serving manifests"
```

---

### Task 3: Rollout guard in the API and `_apply_update` extraction

**Files:**
- Modify: `backend/app/api/model_deployments.py:658-816` (`create_deployment`, `update_deployment`)
- Test: `backend/tests/test_deployment_reapply.py` (new)

**Interfaces:**
- Consumes: `serving_rollout.check_rollout`; test helpers `_k8s, _phases, _refresh, _resolved, _result, DEPLOY` from `tests/test_storage_recipes_deployments.py`.
- Produces: `async def _apply_update(db, k8s, litellm, dep, updates: dict, user) -> dict` — the body of today's `update_deployment` from `registered_as = …` to the `return`, with `check_rollout(dep)` (→ 400) inserted after the P/D validation. `update_deployment` becomes: load row → `k8s_for_cluster` → `return await _apply_update(db, k8s, litellm, dep, body.model_dump(exclude_unset=True), user)`. `create_deployment` calls `check_rollout(dep)` (→ 400) right before `db.add(dep)`.

- [ ] **Step 1: Write the failing tests** in `backend/tests/test_deployment_reapply.py`

```python
"""Re-apply a recipe to a deployment; rollout guard on create/update."""

import types
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.db.models.custom_model_deployment import CustomModelDeployment
from tests.test_storage_recipes_deployments import DEPLOY, _k8s, _phases, _refresh, _resolved, _result

RECIPE_ID = uuid.UUID("55555555-5555-5555-5555-555555555555")


def _row(**kw):
    base = dict(
        id=uuid.uuid4(), model_name="llama", litellm_model_name=None, cluster_id=None, namespace="ns",
        recipe_id=RECIPE_ID, image="vllm/vllm-openai:v0.9", replicas=2, gpu_count=1,
        gpu_resource_key="nvidia.com/gpu", cpu_request=None, cpu_limit=None, memory_request=None,
        memory_limit=None, node_selector={"gpu": "a100"}, tolerations=None, storages=[], model_path="/models/llama",
        vllm_extra_args=["--max-model-len", "8192"], env=None, engine="vllm", engine_args=None, probes=None,
        gpu_type=None, serving_mode="aggregated", pd_config=None, runtime=None, pd_status=None,
        router_stack_id=None, router_stack_created=False, ingress_host="h", ingress_path="/",
        ingress_class="nginx", status="Ready", status_message=None, ready_replicas=2, service_cluster_ip=None,
        litellm_model_id=None, last_synced_at=None, created_by=None, updated_by=None, created_at=None, updated_at=None,
    )
    base.update(kw)
    return CustomModelDeployment(**base)


def _patches(k8s):
    return (
        patch("app.api.model_deployments.k8s_for_cluster", AsyncMock(return_value=k8s)),
        patch("app.api.model_deployments.resolve_entries", AsyncMock(side_effect=_resolved)),
        _phases({}),
        patch("app.api.model_deployments.resolve_gpu_type", AsyncMock(return_value=(None, None))),
    )


async def test_create_rejects_replace_with_one_replica(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(None))
    k8s = _k8s()
    a, b, c, d = _patches(k8s)
    with a, b, c, d:
        async with client_for_user(super_user) as client:
            resp = await client.post(
                "/api/model-deployments", json={**DEPLOY, "replicas": 1, "runtime": {"rollout": "replace"}}
            )
    assert resp.status_code == 400 and "needs at least 2 replicas" in resp.text
    k8s.create_or_patch.assert_not_awaited()


async def test_update_rejects_scaling_a_replace_rollout_to_one(client_for_user, super_user, mock_db):
    dep = _row(replicas=2, runtime={"rollout": "replace"})
    mock_db.execute = AsyncMock(return_value=_result(dep))
    mock_db.refresh = AsyncMock(side_effect=_refresh)
    k8s = _k8s(live_labels={})
    a, b, c, d = _patches(k8s)
    with a, b, c, d:
        async with client_for_user(super_user) as client:
            resp = await client.put(f"/api/model-deployments/{dep.id}", json={"replicas": 1})
    assert resp.status_code == 400 and "needs at least 2 replicas" in resp.text
    k8s.create_or_patch.assert_not_awaited()


async def test_update_still_reapplies_manifests(client_for_user, super_user, mock_db):
    dep = _row()
    mock_db.execute = AsyncMock(return_value=_result(dep))
    mock_db.refresh = AsyncMock(side_effect=_refresh)
    k8s = _k8s(live_labels={})
    a, b, c, d = _patches(k8s)
    with a, b, c, d:
        async with client_for_user(super_user) as client:
            resp = await client.put(f"/api/model-deployments/{dep.id}", json={"image": "vllm/vllm-openai:v0.10"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["image"] == "vllm/vllm-openai:v0.10" and resp.json()["status"] == "Updating"
    k8s.create_or_patch.assert_awaited_once()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_deployment_reapply.py -q`
Expected: the two guard tests FAIL (201 / 200 instead of 400); the regression test PASSES.

- [ ] **Step 3: Implement** the extraction and the two `check_rollout` calls as described in Interfaces (`except ValueError as e: raise HTTPException(status_code=400, detail=str(e))`). Import `from app.services.serving_rollout import check_rollout`.

- [ ] **Step 4: Run the deployment API suites**

Run: `cd backend && .venv/bin/python -m pytest tests/test_deployment_reapply.py tests/test_deploy_namespace.py tests/test_storage_recipes_deployments.py tests/test_model_deployments_engine.py tests/test_cluster_placement.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/model_deployments.py backend/tests/test_deployment_reapply.py
git commit -m "feat(deployments): refuse replace rollouts at one replica; share the update apply path"
```

---

### Task 4: Recipe re-apply — service, diff endpoint, reapply endpoint

**Files:**
- Create: `backend/app/services/recipe_reapply.py`
- Modify: `backend/app/api/model_deployments.py` (two routes after `reregister_deployment`, before the delete route)
- Test: `backend/tests/test_deployment_reapply.py`

**Interfaces:**
- Consumes: `serving_rollout.check_rollout / surge_gpus / mode`; `_apply_update` (Task 3); `_registration_key(dep)`; `reconcile_deployments._record_event(db, dep, event_type, severity, from_status, to_status, message)`; `CustomServingRecipe`.
- Produces (module `app.services.recipe_reapply`, pure):
  - `RECIPE_FIELDS: tuple[str, ...]` — the 18 fields from Global Constraints, in that order.
  - `recipe_values(recipe, dep) -> dict` — `{field: getattr(recipe, field)}` for `RECIPE_FIELDS`, except `pd_config`: a deep copy of the recipe's with `prefill.replicas` / `decode.replicas` set from `dep.pd_config` when the deployment has them (and `pd_config` stays `None` for aggregated); `storages` projected to `{storage_id, mount_path, read_only}` entries.
  - `changes(dep, values: dict) -> list[dict]` — `[{"field", "current", "proposed"}]` for fields whose normalised values differ; normalise: `None`, `{}`, `[]` are equal; `storages` compared as sorted tuples of `(storage_id, mount_path, read_only)`; dicts/lists compared structurally.
  - `proposed_row(dep, values) -> types.SimpleNamespace` — a copy of the deployment's column values with `values` overlaid (for `surge_gpus`, `check_rollout`, `_registration_key`).
- Produces (API):
  - `GET /api/model-deployments/{deployment_id}/recipe-diff?recipe_id=` → `{"recipe": {"id", "name"}, "changes": [...], "rollout": {"mode", "extra_gpus", "gpu_resource_key", "blocked"}, "reregister": bool}`; 404 when neither a query nor the row names a recipe or the recipe does not exist; 409 when `recipe.serving_mode != dep.serving_mode`.
  - `POST /api/model-deployments/{deployment_id}/reapply` body `ReapplyRequest(recipe_id: str | None = None)` → same resolution/409 → `updates = recipe_values(recipe, dep)`; `updates["gpu_type"] = dep.gpu_type` so `_apply_update` re-runs the profile (only when `dep.gpu_type` is set); `dep.recipe_id = recipe.id`; `await _apply_update(...)`; then `_record_event(db, dep, "RecipeApplied", "info", None, None, f"Re-applied recipe '{recipe.name}': {', '.join(changed fields) or 'no field changes'}")`; return the apply result.
  - Shared helper `async def _recipe_for(db, dep, recipe_id: str | None) -> CustomServingRecipe` doing the 404/409 logic.

- [ ] **Step 1: Append the failing tests** to `backend/tests/test_deployment_reapply.py`

```python
from app.db.models.custom_serving_recipe import CustomServingRecipe
from app.services import recipe_reapply as rr


def _recipe(**kw):
    base = dict(
        id=RECIPE_ID, name="llama-fixed", description=None, model_path="/models/llama", image="vllm/vllm-openai:v0.10",
        gpu_count=2, gpu_resource_key="nvidia.com/gpu", cpu_request=None, cpu_limit=None, memory_request="64Gi",
        memory_limit=None, node_selector=None, tolerations=None, storages=[], vllm_extra_args=["--max-model-len", "16384"],
        env={"PYTHONHASHSEED": "0"}, engine="vllm", engine_args={"tensor-parallel-size": 2}, probes=None, gpu_type=None,
        serving_mode="aggregated", pd_config=None, runtime={"rollout": "replace"}, created_by=None, updated_by=None,
        created_at=None, updated_at=None,
    )
    base.update(kw)
    return CustomServingRecipe(**base)


def test_recipe_values_only_recipe_fields():
    values = rr.recipe_values(_recipe(), _row())
    assert set(values) == set(rr.RECIPE_FIELDS)
    assert values["image"] == "vllm/vllm-openai:v0.10" and values["gpu_count"] == 2
    assert "replicas" not in values and "model_name" not in values and "ingress_host" not in values


def test_recipe_values_keeps_pool_replicas():
    dep = _row(serving_mode="pd", pd_config={"prefill": {"replicas": 3, "gpu_count": 8}, "decode": {"replicas": 2}})
    recipe = _recipe(serving_mode="pd", pd_config={"prefill": {"gpu_count": 4}, "decode": {"engine_args": {"max-num-seqs": 64}}})
    values = rr.recipe_values(recipe, dep)
    assert values["pd_config"]["prefill"] == {"gpu_count": 4, "replicas": 3}
    assert values["pd_config"]["decode"] == {"engine_args": {"max-num-seqs": 64}, "replicas": 2}
    assert recipe.pd_config["prefill"] == {"gpu_count": 4}  # the recipe object is untouched


def test_changes_lists_only_differences_and_normalises_empties():
    dep = _row(env={}, tolerations=None, storages=[{"storage_id": "11111111-1111-1111-1111-111111111111", "mount_path": "/models", "read_only": True, "volume_name": "v", "resolved": {"type": "pvc"}}])
    recipe = _recipe(env=None, tolerations=[], storages=[{"storage_id": "11111111-1111-1111-1111-111111111111", "mount_path": "/models", "read_only": True}], image=dep.image, gpu_count=1, memory_request=None, vllm_extra_args=dep.vllm_extra_args, engine_args=None, runtime=None, node_selector={"gpu": "a100"})
    assert rr.changes(dep, rr.recipe_values(recipe, dep)) == []
    recipe.image = "vllm/vllm-openai:v0.10"
    assert rr.changes(dep, rr.recipe_values(recipe, dep)) == [
        {"field": "image", "current": "vllm/vllm-openai:v0.9", "proposed": "vllm/vllm-openai:v0.10"}
    ]


def _db_with(dep, recipe):
    """db.execute → the deployment for CustomModelDeployment selects; db.get → the recipe."""
    db = MagicMock()
    db.execute = AsyncMock(return_value=_result(dep))
    db.get = AsyncMock(return_value=recipe)
    db.refresh = AsyncMock(side_effect=_refresh)
    db.flush = AsyncMock()
    db.add = MagicMock()
    return db


async def test_recipe_diff_reports_changes_rollout_and_reregister(client_for_user, super_user, mock_db):
    dep = _row()
    recipe = _recipe()
    mock_db.execute = AsyncMock(return_value=_result(dep))
    mock_db.get = AsyncMock(return_value=recipe)
    async with client_for_user(super_user) as client:
        resp = await client.get(f"/api/model-deployments/{dep.id}/recipe-diff")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["recipe"] == {"id": str(RECIPE_ID), "name": "llama-fixed"}
    assert {c["field"] for c in body["changes"]} == {"image", "gpu_count", "memory_request", "vllm_extra_args", "env", "engine_args", "runtime", "node_selector"}
    assert body["rollout"] == {"mode": "replace", "extra_gpus": 0, "gpu_resource_key": "nvidia.com/gpu", "blocked": None}
    assert body["reregister"] is False


async def test_recipe_diff_surge_gpus_and_blocked(client_for_user, super_user, mock_db):
    dep = _row(replicas=1)
    recipe = _recipe()  # rollout replace → blocked at 1 replica
    mock_db.execute = AsyncMock(return_value=_result(dep))
    mock_db.get = AsyncMock(return_value=recipe)
    async with client_for_user(super_user) as client:
        resp = await client.get(f"/api/model-deployments/{dep.id}/recipe-diff")
    assert resp.json()["rollout"]["blocked"].startswith("replicas: rollout 'replace'")
    recipe.runtime = None  # surge → one extra pod of the proposed gpu_count
    async with client_for_user(super_user) as client:
        resp = await client.get(f"/api/model-deployments/{dep.id}/recipe-diff")
    assert resp.json()["rollout"] == {"mode": "surge", "extra_gpus": 2, "gpu_resource_key": "nvidia.com/gpu", "blocked": None}


async def test_recipe_diff_flags_reregister_when_served_name_changes(client_for_user, super_user, mock_db):
    dep = _row()
    recipe = _recipe(vllm_extra_args=["--served-model-name", "llama-v2"])
    mock_db.execute = AsyncMock(return_value=_result(dep))
    mock_db.get = AsyncMock(return_value=recipe)
    async with client_for_user(super_user) as client:
        resp = await client.get(f"/api/model-deployments/{dep.id}/recipe-diff")
    assert resp.json()["reregister"] is True


async def test_recipe_diff_404_without_recipe_and_409_on_other_mode(client_for_user, super_user, mock_db):
    dep = _row(recipe_id=None)
    mock_db.execute = AsyncMock(return_value=_result(dep))
    mock_db.get = AsyncMock(return_value=None)
    async with client_for_user(super_user) as client:
        resp = await client.get(f"/api/model-deployments/{dep.id}/recipe-diff")
        assert resp.status_code == 404
        mock_db.get = AsyncMock(return_value=_recipe(serving_mode="pd", pd_config={"prefill": {}, "decode": {}}))
        resp = await client.get(f"/api/model-deployments/{dep.id}/recipe-diff?recipe_id={RECIPE_ID}")
        assert resp.status_code == 409 and "serving mode" in resp.text


async def test_reapply_refuses_other_serving_mode(client_for_user, super_user, mock_db):
    dep = _row()
    mock_db.execute = AsyncMock(return_value=_result(dep))
    mock_db.get = AsyncMock(return_value=_recipe(serving_mode="pd", pd_config={"prefill": {}, "decode": {}}))
    k8s = _k8s(live_labels={})
    a, b, c, d = _patches(k8s)
    with a, b, c, d:
        async with client_for_user(super_user) as client:
            resp = await client.post(f"/api/model-deployments/{dep.id}/reapply", json={})
    assert resp.status_code == 409
    k8s.create_or_patch.assert_not_awaited()


async def test_reapply_overlays_recipe_fields_keeps_instance_fields_and_records_event(client_for_user, super_user, mock_db):
    dep = _row(replicas=3, litellm_model_name="llama-prod")
    recipe = _recipe(runtime=None)
    mock_db.execute = AsyncMock(return_value=_result(dep))
    mock_db.get = AsyncMock(return_value=recipe)
    mock_db.refresh = AsyncMock(side_effect=_refresh)
    k8s = _k8s(live_labels={})
    a, b, c, d = _patches(k8s)
    with a, b, c, d, patch("app.api.model_deployments.reconcile_deployments._record_event", AsyncMock()) as ev:
        async with client_for_user(super_user) as client:
            resp = await client.post(f"/api/model-deployments/{dep.id}/reapply", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["image"] == "vllm/vllm-openai:v0.10" and body["gpu_count"] == 2 and body["env"] == {"PYTHONHASHSEED": "0"}
    assert body["replicas"] == 3 and body["litellm_model_name"] == "llama-prod" and body["model_name"] == "llama"
    assert body["status"] == "Updating" and body["recipe_id"] == str(RECIPE_ID)
    k8s.create_or_patch.assert_awaited_once()
    manifests = k8s.create_or_patch.await_args.args[1]
    deployment = next(m for m in manifests if m["kind"] == "Deployment")
    assert deployment["spec"]["replicas"] == 3
    assert deployment["spec"]["template"]["spec"]["containers"][0]["image"] == "vllm/vllm-openai:v0.10"
    assert ev.await_args.args[2] == "RecipeApplied" and "llama-fixed" in ev.await_args.args[6]


async def test_reapply_reapplies_gpu_profile(client_for_user, super_user, mock_db):
    profile = types.SimpleNamespace(name="h100", label_value="h100", gpu_resource_key="nvidia.com/gpu", node_selector=None, tolerations=None, enabled=True)
    dep = _row(gpu_type="h100", node_selector={"gpu.type": "h100"})
    recipe = _recipe(runtime=None, node_selector={"zone": "a"})
    mock_db.execute = AsyncMock(return_value=_result(dep))
    mock_db.get = AsyncMock(return_value=recipe)
    mock_db.refresh = AsyncMock(side_effect=_refresh)
    k8s = _k8s(live_labels={})
    a, b, c, _ = _patches(k8s)
    with a, b, c, patch("app.api.model_deployments.resolve_gpu_type", AsyncMock(return_value=(profile, "gpu.type"))), \
         patch("app.api.model_deployments.reconcile_deployments._record_event", AsyncMock()):
        async with client_for_user(super_user) as client:
            resp = await client.post(f"/api/model-deployments/{dep.id}/reapply", json={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["node_selector"] == {"zone": "a", "gpu.type": "h100"}


async def test_reapply_links_the_deployment_to_a_chosen_recipe(client_for_user, super_user, mock_db):
    dep = _row(recipe_id=None)
    recipe = _recipe(runtime=None)
    mock_db.execute = AsyncMock(return_value=_result(dep))
    mock_db.get = AsyncMock(return_value=recipe)
    mock_db.refresh = AsyncMock(side_effect=_refresh)
    k8s = _k8s(live_labels={})
    a, b, c, d = _patches(k8s)
    with a, b, c, d, patch("app.api.model_deployments.reconcile_deployments._record_event", AsyncMock()):
        async with client_for_user(super_user) as client:
            resp = await client.post(f"/api/model-deployments/{dep.id}/reapply", json={"recipe_id": str(RECIPE_ID)})
    assert resp.status_code == 200, resp.text
    assert resp.json()["recipe_id"] == str(RECIPE_ID)
    mock_db.get.assert_awaited_with(CustomServingRecipe, RECIPE_ID)
```

Check `test_reapply_reapplies_gpu_profile`'s expected selector against `resolve_placement`'s actual merge order before relying on it: run it once and, if the profile key is written differently (e.g. the profile's own label key), fix the expectation to what `apply_profile` produces for that profile, not the implementation. The point pinned is that `gpu.type: h100` is present after re-apply.

- [ ] **Step 2: Run them to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_deployment_reapply.py -q`
Expected: the new tests FAIL (`ModuleNotFoundError: app.services.recipe_reapply`; 404/405 on the routes).

- [ ] **Step 3: Create `backend/app/services/recipe_reapply.py`** with `RECIPE_FIELDS`, `recipe_values`, `changes`, `proposed_row` per Interfaces (use `copy.deepcopy` for `pd_config`; `proposed_row` copies every column in `CustomModelDeployment.__table__.columns` via `getattr`, then overlays).

- [ ] **Step 4: Add `ReapplyRequest`, `_recipe_for`, the GET and POST routes** to `model_deployments.py` per Interfaces. Resolve the recipe with `db.get(CustomServingRecipe, uuid.UUID(recipe_id))`. In the diff route build `proposed = proposed_row(dep, values)`; `blocked` = `str(e)` from `check_rollout(proposed)` or `None`; `extra_gpus = surge_gpus(proposed)`; `mode = serving_rollout.mode(proposed.runtime)`; `reregister = _registration_key(dep) != _registration_key(proposed)` (both non-None, else `False`). The POST route must run `_recipe_for` before `k8s_for_cluster`.

- [ ] **Step 5: Run the tests**

Run: `cd backend && .venv/bin/python -m pytest tests/test_deployment_reapply.py tests/test_deploy_namespace.py tests/test_storage_recipes_deployments.py -q`
Expected: all PASS.

- [ ] **Step 6: Lint and commit**

Run: `cd backend && .venv/bin/ruff check app/services/recipe_reapply.py app/services/serving_rollout.py app/api/model_deployments.py tests/test_deployment_reapply.py tests/test_serving_rollout.py`
Expected: no new findings.

```bash
git add backend/app/services/recipe_reapply.py backend/app/api/model_deployments.py backend/tests/test_deployment_reapply.py
git commit -m "feat(deployments): recipe-diff and reapply endpoints — overlay the recipe's serving fields and roll"
```

---

### Task 5: Frontend types, hooks, runtime form fields, i18n

**Files:**
- Modify: `frontend/src/types/index.ts:943-948` (`RuntimeOptions`), after `ModelDeploymentEvent` (new `RecipeDiff`)
- Modify: `frontend/src/hooks/use-api.ts:1387-1393` (after `useReregisterDeployment`)
- Modify: `frontend/src/components/pool-config-cards.tsx:43-80` (state/summary), `:214-231` (runtime section), `:255-265` (exported defaults)
- Modify: `frontend/messages/ko.json`, `frontend/messages/en.json` (`servingRecipes` block near `runtimeExtraResourcesHint`)

**Interfaces:**
- Produces (types):
  ```ts
  export type RolloutMode = "surge" | "replace";
  export interface RuntimeOptions { …; rollout?: RolloutMode | null; termination_grace_seconds?: number | null; prestop_sleep_seconds?: number | null; }
  export interface RecipeDiffChange { field: string; current: unknown; proposed: unknown }
  export interface RecipeDiff {
    recipe: { id: string; name: string };
    changes: RecipeDiffChange[];
    rollout: { mode: RolloutMode; extra_gpus: number; gpu_resource_key: string; blocked: string | null };
    reregister: boolean;
  }
  ```
- Produces (hooks): `useRecipeDiff(id: string, recipeId: string | null, enabled: boolean)` → `useQuery<RecipeDiff>` keyed `["model-deployments", id, "recipe-diff", recipeId]`, GET `/api/model-deployments/${id}/recipe-diff` with `?recipe_id=` when given; `useReapplyDeployment(id: string)` → `useMutation` POST `/api/model-deployments/${id}/reapply` with `{ recipe_id }`, invalidating `["model-deployments"]`.
- Produces (form): `export const ROLLOUT_DEFAULTS = { rollout: "surge" as RolloutMode, termination_grace_seconds: 120, prestop_sleep_seconds: 10 }` from `pool-config-cards.tsx`; `export function rolloutSummary(runtime: RuntimeOptions | null | undefined): string` → `"surge · grace 120s · preStop 10s"` with defaults filled in (used by Task 6).
- i18n keys (`servingRecipes`): `runtimeRollout` ("롤아웃 방식" / "Rollout"), `runtimeRolloutSurge` ("새 파드 먼저 (GPU 여유 필요)" / "New pod first (needs spare GPUs)"), `runtimeRolloutReplace` ("옛 파드 먼저 내림 (replicas ≥ 2)" / "Old pod first (replicas ≥ 2)"), `runtimeRolloutHint` ("새 파드 먼저: 롤아웃 동안 replica 하나만큼 GPU가 추가로 필요합니다. 옛 파드 먼저: 추가 GPU 없이 롤아웃하지만 그동안 용량이 replica 하나만큼 줄고, replicas가 1이면 중단되므로 막습니다." / English equivalent), `runtimeTerminationGrace` ("종료 유예 (초)" / "Termination grace (s)"), `runtimePrestopSleep` ("preStop 대기 (초)" / "preStop sleep (s)"), `runtimeGracefulHint` ("파드를 내릴 때 preStop 동안 기다린 뒤 SIGTERM을 보내고, 유예 시간 안에 진행 중인 요청을 끝내게 합니다. 기본 10초 / 120초." / English equivalent).

- [ ] **Step 1: Add the types and hooks.**

- [ ] **Step 2: Runtime section in `pool-config-cards.tsx`**: a native `<select id={id("rollout")}>` (same classes as the deploy dialog's selects) with the two options, value `runtime.rollout ?? "surge"`, writing `rollout: v === "surge" ? null : "replace"` through `setRuntime`; two `<Input type="number">` for the seconds with placeholders `120` and `10`, blank → `null`; `setRuntime`'s empty check also treats `rollout`, `termination_grace_seconds`, `prestop_sleep_seconds` as content; `runtimeSummary` appends `rollout=replace`, `grace=<n>s`, `preStop=<n>s` when set. Add `ROLLOUT_DEFAULTS` and `rolloutSummary` next to the probe defaults.

- [ ] **Step 3: Add the i18n keys** to both locale files, right after `runtimeExtraResourcesHint`.

- [ ] **Step 4: Type-check and lint**

Run: `cd frontend && npx tsc --noEmit && npx eslint src/components/pool-config-cards.tsx src/hooks/use-api.ts src/types/index.ts`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/hooks/use-api.ts frontend/src/components/pool-config-cards.tsx frontend/messages/ko.json frontend/messages/en.json
git commit -m "feat(recipes): rollout / termination settings in the runtime section; recipe-diff and reapply hooks"
```

---

### Task 6: Re-apply dialog and the detail page button

**Files:**
- Create: `frontend/src/components/reapply-from-recipe-dialog.tsx`
- Modify: `frontend/src/app/(app)/admin/deployments/[id]/page.tsx:5` (icons), `:93` (state), `:146-155` (buttons), `:209-212` (config card: rollout field)
- Modify: `frontend/messages/ko.json`, `frontend/messages/en.json` (`adminDeployments` block, after `litellmReregisterNeedsReady`)

**Interfaces:**
- Consumes: `useRecipeDiff`, `useReapplyDeployment`, `useServingRecipes`, `rolloutSummary` (Task 5); `Dialog*` from `@/components/ui/dialog`; `RefreshCw` from `lucide-react`.
- Produces: `export function ReapplyFromRecipeDialog({ dep, open, onClose }: { dep: ModelDeployment; open: boolean; onClose: () => void })`.
- Dialog behaviour: `recipeId` state seeded from `dep.recipe_id`; a native `<select id="reapply-recipe">` listing `useServingRecipes()` filtered to `r.serving_mode === dep.serving_mode` (option label `r.name`); diff loads when `open && recipeId`; while loading show a spinner; table with three columns (field / current / proposed, values rendered with `formatValue(v)`: strings as-is, arrays of strings joined by a space, everything else `JSON.stringify`, null/undefined as "—"); empty list → `reapplyNoChanges`; rollout line: `surge` → `reapplySurgeNote {count, key}`, `replace` → `reapplyReplaceNote`; `reregister` → amber `reapplyReregisterNote`; `blocked` → destructive text with the server message and the submit button disabled; submit → `mutate({ recipe_id: recipeId })`, success toast `reapplySuccess`, close; error toast with the server message.
- i18n keys (`adminDeployments`): `reapplyButton` ("레시피에서 다시 적용" / "Re-apply from recipe"), `reapplyTitle` ("레시피에서 다시 적용" / "Re-apply from recipe"), `reapplyDesc` ("레시피의 서빙 설정을 이 배포에 덮어쓰고 롤링 업데이트합니다. 모델 이름, LiteLLM 이름, replicas, 네임스페이스, 인그레스, GPU 타입은 그대로 둡니다." / English), `reapplyRecipe` ("레시피" / "Recipe"), `reapplyPickRecipe` ("레시피를 고르세요" / "Pick a recipe"), `reapplyNoRecipes` ("같은 서빙 모드의 레시피가 없습니다." / "No recipe with this serving mode."), `reapplyNoChanges` ("바뀌는 항목이 없습니다. 적용하면 파드만 다시 굴립니다." / "Nothing changes; applying only rolls the pods."), `reapplyColField` ("항목" / "Field"), `reapplyColCurrent` ("현재" / "Current"), `reapplyColProposed` ("적용 후" / "After"), `reapplySurgeNote` ("롤아웃 동안 {key} {count}개가 추가로 필요합니다. 새 파드가 Ready 되기 전까지 옛 파드가 계속 서비스합니다." / "Needs {count} extra {key} during the rollout; the old pod keeps serving until the new one is Ready."), `reapplyReplaceNote` ("추가 GPU 없이 롤아웃합니다. 그동안 용량이 replica 하나만큼 줄어듭니다." / "Rolls without extra GPUs; capacity drops by one replica meanwhile."), `reapplyReregisterNote` ("LiteLLM이 보는 이름이나 키가 바뀝니다. 적용 직후 LiteLLM 항목이 삭제되고 배포가 다시 Ready가 되면 다시 등록됩니다. 그 사이에는 LiteLLM 호출이 실패합니다." / English), `reapplySubmit` ("적용" / "Apply"), `reapplySuccess` ("레시피를 적용했습니다. 롤아웃이 끝나면 Ready가 됩니다." / "Recipe applied; Ready once the rollout completes."), `reapplyFailed` ("적용 실패" / "Apply failed"), `rollout` ("롤아웃" / "Rollout").

- [ ] **Step 1: Create the dialog component** per Interfaces.

- [ ] **Step 2: Wire the detail page**: `const [reapplyOpen, setReapplyOpen] = useState(false)`; a `Button variant="outline" size="sm"` with `RefreshCw` and `t("reapplyButton")` before the save-as-recipe button; `<ReapplyFromRecipeDialog dep={dep} open={reapplyOpen} onClose={() => setReapplyOpen(false)} />` rendered next to the buttons; in the config card add `<Field label={t("rollout")} mono>{rolloutSummary(dep.runtime)}</Field>` after the ingress host field (both serving modes).

- [ ] **Step 3: Add the i18n keys** to both locale files.

- [ ] **Step 4: Type-check, lint**

Run: `cd frontend && npx tsc --noEmit && npx eslint "src/app/(app)/admin/deployments/[id]/page.tsx" src/components/reapply-from-recipe-dialog.tsx`
Expected: no errors.

- [ ] **Step 5: Browser check** (Next dev server on port 3100 from this worktree, `BACKEND_INTERNAL_URL=http://localhost:8002`, `litellm_session` cookie from the scratchpad `cookie.txt`): open a deployment detail page → the rollout field shows `surge · grace 120s · preStop 10s`; click "레시피에서 다시 적용" → the dialog lists the recipe, the change table and the rollout note. The old backend container does not have the new routes, so the diff will 404 there: rebuild the backend image first (`docker compose up -d --build backend`) or verify against a local uvicorn on 8002. Record what was verified.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/reapply-from-recipe-dialog.tsx "frontend/src/app/(app)/admin/deployments/[id]/page.tsx" frontend/messages/ko.json frontend/messages/en.json
git commit -m "feat(deployments): re-apply from recipe dialog with diff preview and rollout note"
```

---

### Task 7: Full verification

- [ ] **Step 1: Backend full suite** — `cd backend && .venv/bin/python -m pytest -q 2>&1 | tail -5`; compare the failure list with `fail-main.txt` in the scratchpad: only the 21 baseline failures may remain.
- [ ] **Step 2: Frontend** — `cd frontend && npx tsc --noEmit && npx eslint .`
- [ ] **Step 3: Ruff** — `cd backend && .venv/bin/ruff check app tests` must show no findings outside the pre-existing ones in `k8s.py` and `test_serving_engines.py`.
- [ ] **Step 4:** Report the results to the user; do not push or open a PR until asked.
