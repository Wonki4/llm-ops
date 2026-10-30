"""Re-apply a recipe to a deployment; rollout guard on create/update."""

import types
import uuid
from unittest.mock import AsyncMock, patch

from app.db.models.custom_model_deployment import CustomModelDeployment
from app.db.models.custom_serving_recipe import CustomServingRecipe
from app.services import recipe_reapply as rr
from app.services.storage_store import UnresolvedStorageError
from tests.test_storage_recipes_deployments import DEPLOY, _k8s, _phases, _refresh, _resolved, _result

RECIPE_ID = uuid.UUID("55555555-5555-5555-5555-555555555555")
SID = "11111111-1111-1111-1111-111111111111"


def _recipe(**kw):
    base = dict(
        id=RECIPE_ID, name="llama-fixed", description=None, model_path="/models/llama", image="vllm/vllm-openai:v0.10",
        gpu_count=2, gpu_resource_key="nvidia.com/gpu", cpu_request=None, cpu_limit=None, memory_request="64Gi",
        memory_limit=None, node_selector=None, tolerations=None, storages=[],
        vllm_extra_args=["--max-model-len", "16384"],
        env={"PYTHONHASHSEED": "0"}, engine="vllm", engine_args={"tensor-parallel-size": 2}, probes=None, gpu_type=None,
        serving_mode="aggregated", pd_config=None, runtime={"rollout": "replace"}, created_by=None, updated_by=None,
        created_at=None, updated_at=None,
    )
    base.update(kw)
    return CustomServingRecipe(**base)


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


# ─── rollout guard ────────────────────────────────────────────────────────────


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


# ─── recipe_reapply service ───────────────────────────────────────────────────


def test_recipe_values_only_recipe_fields():
    values = rr.recipe_values(_recipe(), _row())
    assert set(values) == set(rr.RECIPE_FIELDS)
    assert values["image"] == "vllm/vllm-openai:v0.10" and values["gpu_count"] == 2
    assert "replicas" not in values and "model_name" not in values and "ingress_host" not in values


def test_recipe_values_keeps_pool_replicas():
    dep = _row(serving_mode="pd", pd_config={"prefill": {"replicas": 3, "gpu_count": 8}, "decode": {"replicas": 2}})
    recipe = _recipe(
        serving_mode="pd", pd_config={"prefill": {"gpu_count": 4}, "decode": {"engine_args": {"max-num-seqs": 64}}}
    )
    values = rr.recipe_values(recipe, dep)
    assert values["pd_config"]["prefill"] == {"gpu_count": 4, "replicas": 3}
    assert values["pd_config"]["decode"] == {"engine_args": {"max-num-seqs": 64}, "replicas": 2}
    assert recipe.pd_config["prefill"] == {"gpu_count": 4}  # the recipe object is untouched


def test_changes_lists_only_differences_and_normalises_empties():
    dep = _row(
        env={},
        tolerations=None,
        storages=[
            {
                "storage_id": SID, "mount_path": "/models", "read_only": True,
                "volume_name": "v", "resolved": {"type": "pvc"},
            }
        ],
    )
    recipe = _recipe(
        env=None, tolerations=[], storages=[{"storage_id": SID, "mount_path": "/models", "read_only": True}],
        image=dep.image, gpu_count=1, memory_request=None, vllm_extra_args=dep.vllm_extra_args, engine_args=None,
        runtime=None, node_selector={"gpu": "a100"},
    )
    assert rr.changes(dep, rr.recipe_values(recipe, dep)) == []
    recipe.image = "vllm/vllm-openai:v0.10"
    assert rr.changes(dep, rr.recipe_values(recipe, dep)) == [
        {"field": "image", "current": "vllm/vllm-openai:v0.9", "proposed": "vllm/vllm-openai:v0.10"}
    ]


# ─── GET /recipe-diff ─────────────────────────────────────────────────────────


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
    assert {c["field"] for c in body["changes"]} == {
        "image", "gpu_count", "memory_request", "vllm_extra_args", "env", "engine_args", "runtime", "node_selector",
    }
    assert body["rollout"] == {
        "mode": "replace", "extra_gpus": 0, "gpu_resource_key": "nvidia.com/gpu", "blocked": None,
    }
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
    assert resp.json()["rollout"] == {
        "mode": "surge", "extra_gpus": 2, "gpu_resource_key": "nvidia.com/gpu", "blocked": None,
    }


async def test_recipe_diff_flags_reregister_when_served_name_changes(client_for_user, super_user, mock_db):
    dep = _row()
    recipe = _recipe(vllm_extra_args=["--served-model-name", "llama-v2"])
    mock_db.execute = AsyncMock(return_value=_result(dep))
    mock_db.get = AsyncMock(return_value=recipe)
    async with client_for_user(super_user) as client:
        resp = await client.get(f"/api/model-deployments/{dep.id}/recipe-diff")
    assert resp.json()["reregister"] is True


async def test_recipe_diff_ignores_the_gpu_profiles_placement(client_for_user, super_user, mock_db):
    """The deployment carries the profile's node label; the recipe does not. Not a change."""
    profile = types.SimpleNamespace(
        name="h100", label_key=None, label_value="h100", gpu_resource_key="nvidia.com/gpu",
        node_selector=None, tolerations=None, enabled=True,
    )
    recipe = _recipe(runtime=None, node_selector=None)
    dep = _row(
        gpu_type="h100", node_selector={"gpu.type": "h100"}, image=recipe.image, gpu_count=recipe.gpu_count,
        memory_request=recipe.memory_request, vllm_extra_args=recipe.vllm_extra_args, env=recipe.env,
        engine_args=recipe.engine_args, runtime=None,
    )
    mock_db.execute = AsyncMock(return_value=_result(dep))
    mock_db.get = AsyncMock(return_value=recipe)
    with patch("app.api.model_deployments.resolve_gpu_type", AsyncMock(return_value=(profile, "gpu.type"))):
        async with client_for_user(super_user) as client:
            resp = await client.get(f"/api/model-deployments/{dep.id}/recipe-diff")
    assert resp.status_code == 200, resp.text
    assert resp.json()["changes"] == []
    assert resp.json()["rollout"]["gpu_resource_key"] == "nvidia.com/gpu"


async def test_recipe_diff_pd_identical_recipe_reports_no_changes(client_for_user, super_user, mock_db):
    """Pools on the row carry baked-in placement and resolved storages; the recipe's are bare."""
    bare = {"storage_id": SID, "mount_path": "/models", "read_only": True}
    resolved = {**bare, "volume_name": "v", "resolved": {"type": "pvc"}}
    recipe = _recipe(
        runtime=None, serving_mode="pd",
        pd_config={"prefill": {"gpu_count": 8, "storages": [bare]}, "decode": {"gpu_count": 8}},
    )
    dep = _row(
        serving_mode="pd", image=recipe.image, gpu_count=recipe.gpu_count, memory_request=recipe.memory_request,
        vllm_extra_args=recipe.vllm_extra_args, env=recipe.env, engine_args=recipe.engine_args, runtime=None,
        node_selector=None,
        pd_config={
            "prefill": {
                "replicas": 2, "gpu_count": 8, "node_selector": None, "tolerations": None,
                "gpu_resource_key": "nvidia.com/gpu", "storages": [resolved],
            },
            "decode": {
                "replicas": 1, "gpu_count": 8, "node_selector": None, "tolerations": None,
                "gpu_resource_key": "nvidia.com/gpu", "storages": [],
            },
        },
    )
    mock_db.execute = AsyncMock(return_value=_result(dep))
    mock_db.get = AsyncMock(return_value=recipe)
    with patch("app.api.model_deployments.resolve_gpu_type", AsyncMock(return_value=(None, "gpu.type"))):
        async with client_for_user(super_user) as client:
            resp = await client.get(f"/api/model-deployments/{dep.id}/recipe-diff")
    assert resp.status_code == 200, resp.text
    assert resp.json()["changes"] == []


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


async def test_recipe_diff_renders_each_manifest_side_by_side(client_for_user, super_user, mock_db):
    dep = _row()
    recipe = _recipe()
    mock_db.execute = AsyncMock(return_value=_result(dep))
    mock_db.get = AsyncMock(return_value=recipe)
    async with client_for_user(super_user) as client:
        resp = await client.get(f"/api/model-deployments/{dep.id}/recipe-diff")
    assert resp.status_code == 200, resp.text
    manifests = resp.json()["manifests"]
    assert [m["kind"] for m in manifests] == ["Deployment", "Service", "Ingress"]
    deployment, service, ingress = manifests
    assert deployment["changed"] > 0
    assert service["changed"] == 0 and ingress["changed"] == 0  # same owner label on both sides: no noise
    images = [
        (r["left"][1].strip(), r["right"][1].strip())
        for r in deployment["rows"]
        if r["op"] == "replace" and "vllm/vllm-openai" in r["left"][1]
    ]
    assert images == [("image: vllm/vllm-openai:v0.9", "image: vllm/vllm-openai:v0.10")]
    left = "\n".join(r["left"][1] for r in deployment["rows"] if r["left"])
    right = "\n".join(r["right"][1] for r in deployment["rows"] if r["right"])
    assert "8192" in left and "8192" not in right
    assert "16384" in right and "16384" not in left


async def test_recipe_diff_storage_that_does_not_resolve_blocks_without_manifests(client_for_user, super_user, mock_db):
    """The re-apply would fail on it (400), so the page shows why instead of a half-rendered diff."""
    dep = _row()
    recipe = _recipe(runtime=None, storages=[{"storage_id": SID, "mount_path": "/models", "read_only": True}])
    mock_db.execute = AsyncMock(return_value=_result(dep))
    mock_db.get = AsyncMock(return_value=recipe)
    err = UnresolvedStorageError(["weights"])
    with patch("app.api.model_deployments.resolve_entries", AsyncMock(side_effect=err)):
        async with client_for_user(super_user) as client:
            resp = await client.get(f"/api/model-deployments/{dep.id}/recipe-diff")
    assert resp.status_code == 200, resp.text
    assert resp.json()["manifests"] == []
    assert resp.json()["rollout"]["blocked"] == str(err)


async def test_recipe_diff_pd_compares_each_pool(client_for_user, super_user, mock_db):
    recipe = _recipe(
        runtime=None, serving_mode="pd",
        pd_config={"prefill": {"gpu_count": 8}, "decode": {"gpu_count": 8, "vllm_extra_args": ["--max-num-seqs=512"]}},
    )
    dep = _row(
        serving_mode="pd", image=recipe.image, gpu_count=recipe.gpu_count, memory_request=recipe.memory_request,
        vllm_extra_args=recipe.vllm_extra_args, env=recipe.env, engine_args=recipe.engine_args, runtime=None,
        node_selector=None,
        pd_config={"prefill": {"replicas": 1, "gpu_count": 8}, "decode": {"replicas": 1, "gpu_count": 8}},
    )
    mock_db.execute = AsyncMock(return_value=_result(dep))
    mock_db.get = AsyncMock(return_value=recipe)
    with patch("app.api.model_deployments.resolve_gpu_type", AsyncMock(return_value=(None, "gpu.type"))):
        async with client_for_user(super_user) as client:
            resp = await client.get(f"/api/model-deployments/{dep.id}/recipe-diff")
    assert resp.status_code == 200, resp.text
    pools = {m["name"]: m for m in resp.json()["manifests"] if m["kind"] == "Deployment"}
    assert sorted(pools) == ["llama-decode-deployment", "llama-prefill-deployment"]
    assert pools["llama-prefill-deployment"]["changed"] == 0
    decode = pools["llama-decode-deployment"]
    inserted = [r["right"][1].strip() for r in decode["rows"] if r["op"] != "equal" and r["right"]]
    assert "- --max-num-seqs=512" in inserted


# ─── POST /reapply ────────────────────────────────────────────────────────────


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


async def test_reapply_overlays_recipe_fields_and_records_event(client_for_user, super_user, mock_db):
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
    assert body["image"] == "vllm/vllm-openai:v0.10" and body["gpu_count"] == 2
    assert body["env"] == {"PYTHONHASHSEED": "0"}
    assert body["replicas"] == 3 and body["litellm_model_name"] == "llama-prod" and body["model_name"] == "llama"
    assert body["status"] == "Updating" and body["recipe_id"] == str(RECIPE_ID)
    k8s.create_or_patch.assert_awaited_once()
    manifests = k8s.create_or_patch.await_args.args[1]
    deployment = next(m for m in manifests if m["kind"] == "Deployment")
    assert deployment["spec"]["replicas"] == 3
    assert deployment["spec"]["template"]["spec"]["containers"][0]["image"] == "vllm/vllm-openai:v0.10"
    assert ev.await_args.args[2] == "RecipeApplied" and "llama-fixed" in ev.await_args.args[6]


async def test_reapply_reapplies_gpu_profile(client_for_user, super_user, mock_db):
    profile = types.SimpleNamespace(
        name="h100", label_key=None, label_value="h100", gpu_resource_key="nvidia.com/gpu",
        node_selector=None, tolerations=None, enabled=True,
    )
    dep = _row(gpu_type="h100", node_selector={"gpu.type": "h100"})
    recipe = _recipe(runtime=None, node_selector={"zone": "a"})
    mock_db.execute = AsyncMock(return_value=_result(dep))
    mock_db.get = AsyncMock(return_value=recipe)
    mock_db.refresh = AsyncMock(side_effect=_refresh)
    k8s = _k8s(live_labels={})
    a, b, c, _ = _patches(k8s)
    with (
        a, b, c,
        patch("app.api.model_deployments.resolve_gpu_type", AsyncMock(return_value=(profile, "gpu.type"))),
        patch("app.api.model_deployments.reconcile_deployments._record_event", AsyncMock()),
    ):
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
