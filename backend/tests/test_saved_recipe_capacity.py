import sys
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import Boolean, Column, Integer, String, Table, Uuid, create_engine
from sqlalchemy.orm import Session, registry

from app.services.recipe_capacity import _saved_pool_observation
from tests.test_recipe_kv_capacity import GIB, QWEN
from tests.test_recipe_preview import PD, _bound, _unbound
from tests.test_serving_recipes import _recipe, _result


def path(recipe):
    return f"/api/admin/serving-recipes/{recipe.id}/kv-capacity"


async def test_saved_recipe_reuses_target_and_reacts_to_gpu_and_engine_edits(admin_client, mock_db):
    cluster_id = uuid.uuid4()
    recipe = _recipe(model_path="/team/project", gpu_count=2, gpu_type="A100", vllm_extra_args=[
        "-tp", "2", "--kv-cache-memory-bytes=8G",
    ])
    mock_db.execute.return_value = _result(scalar=recipe)
    profile = SimpleNamespace(name="A100", label_value="A100-80GB", vram_gb=80)
    namespace = AsyncMock(return_value="research")
    read = AsyncMock(return_value={"config": QWEN})
    observed = AsyncMock(return_value=None)
    with (
        patch("app.api.serving_recipes.default_namespace", namespace),
        patch("app.api.serving_recipes.resolve_gpu_type", AsyncMock(return_value=(profile, "gpu"))),
        patch("app.services.recipe_capacity.metadata_for", read),
        patch("app.services.recipe_capacity._saved_pool_observation", observed),
    ):
        data = {"cluster_id": str(cluster_id), "capacity_scenario": {"context_tokens": 32768}}
        response = await admin_client.post(path(recipe), json=data)
        assert response.status_code == 200, response.text
        actual = response.json()
        first = actual["pools"][0]
        assert actual["namespace"] == "research" and actual["cluster_id"] == str(cluster_id)
        assert actual["recipe_id"] == str(recipe.id) and first["model_path"] == "/team/project"
        assert first["gpu_count"] == 2 and first["gpu_type"] == "A100" and first["gpu_profile_resolved"]
        assert first["capacity_tokens"] == 65536 and first["complete_requests"] == 2
        assert first["gpu_vram_gb"] == 80 and first["budget_bytes_per_gpu"] == [8 * GIB] * 2
        namespace.assert_awaited_once_with(mock_db, cluster_id)
        assert read.await_args.args[2]["nodeSelector"] == {"gpu": "A100-80GB"}
        assert observed.await_args.args[1] == recipe.id
        assert "observed" not in first

        recipe.vllm_extra_args[-1] = "--kv-cache-memory-bytes=4G"
        smaller = (await admin_client.post(path(recipe), json=data)).json()["pools"][0]
        assert smaller["capacity_tokens"] == 32768 and smaller["fingerprint"] != first["fingerprint"]
        for count in (1, 0):
            recipe.gpu_count = count
            invalid = (await admin_client.post(path(recipe), json=data)).json()["pools"][0]
            assert invalid["gpu_count"] == count and "requires 2 GPUs per Pod" in invalid["missing"][-1]
            assert invalid["required_bytes_per_replica"] == 8 * GIB
            assert invalid["capacity_tokens"] is None and invalid["complete_requests"] is None
            assert "capacity_range" not in invalid and "complete_requests_range" not in invalid
    mock_db.add.assert_not_called()


async def test_saved_recipe_unbound_storage_does_not_launch_a_metadata_job(admin_client, mock_db):
    recipe = _recipe(storages=[{"storage_id": str(uuid.uuid4()), "mount_path": "/models"}])
    mock_db.execute.return_value = _result(scalar=recipe)
    read = AsyncMock()
    with (
        patch("app.api.serving_recipes.resolve_entries", AsyncMock(side_effect=_unbound)),
        patch("app.services.recipe_capacity.metadata_for", read),
    ):
        response = await admin_client.post(path(recipe), json={"namespace": "isolated"})
    assert response.status_code == 200, response.text
    assert response.json()["warnings"] == ["storage_unresolved:llama-weights"]
    pool = response.json()["pools"][0]
    assert pool["capacity_tokens"] is None and "storage" in pool["missing"][0].lower()
    assert not pool["gpu_profile_resolved"] and pool["gpu_vram_gb"] is None
    read.assert_not_awaited()


async def test_saved_pd_uses_pool_resources_models_and_precision(admin_client, mock_db):
    pd = {
        role: {
            **PD["pd_config"][role], "gpu_count": count, "model_path": f"/models/{role}",
            "vllm_extra_args": ["-tp", str(count), "--kv-cache-memory-bytes=4G", "--kv-cache-dtype", precision],
        }
        for role, count, precision in (("prefill", 1, "bfloat16"), ("decode", 2, "fp8"))
    }
    pd["decode"]["vllm_extra_args"].append("--kv-offloading-size=3")
    recipe = _recipe(serving_mode="pd", pd_config=pd, vllm_extra_args=[])
    mock_db.execute.return_value = _result(scalar=recipe)
    with (
        patch("app.services.storage_store.resolve_entries", AsyncMock(side_effect=_bound)),
        patch("app.services.recipe_capacity.metadata_for", AsyncMock(return_value={"config": QWEN})),
    ):
        response = await admin_client.post(path(recipe), json={"namespace": "pools"})
    assert response.status_code == 200, response.text
    prefill, decode = response.json()["pools"]
    assert (prefill["role"], prefill["model_path"], prefill["gpu_count"]) == ("prefill", "/models/prefill", 1)
    assert (decode["role"], decode["model_path"], decode["gpu_count"]) == ("decode", "/models/decode", 2)
    assert prefill["replicas"] == 2 and prefill["capacity_tokens"] == 16384
    assert decode["replicas"] == 1 and decode["capacity_tokens"] == 65536
    assert "host_kv_offload" not in prefill
    assert decode["host_kv_offload"]["configured_bytes"] == 3 * GIB


async def test_saved_recipe_measured_zero_and_missing_metadata(admin_client, mock_db):
    recipe = _recipe(gpu_type="A100", vllm_extra_args=[])
    mock_db.execute.return_value = _result(scalar=recipe)
    recorded = {"capacity_tokens": 0, "budget_bytes_per_gpu": 0, "observed_at": None,
                "deployment_id": str(uuid.uuid4()), "pod_name": "engine-1", "active": False}
    profile = SimpleNamespace(name="A100", label_value="A100-80GB")
    read = AsyncMock(side_effect=[{"pending": True}, {"config": QWEN}])
    with (
        patch("app.api.serving_recipes.resolve_gpu_type", AsyncMock(return_value=(profile, "gpu"))),
        patch("app.services.recipe_capacity.metadata_for", read),
        patch("app.services.recipe_capacity._saved_pool_observation", AsyncMock(return_value=recorded)),
    ):
        pending = (await admin_client.post(path(recipe), json={})).json()["pools"][0]
        assert pending["pending"] and pending["capacity_tokens"] is None and pending["observed"] == recorded
        ready = (await admin_client.post(path(recipe), json={})).json()["pools"][0]
        assert ready["budget_source"] == "startup" and ready["capacity_tokens"] == 0 and ready["observed"] == recorded


async def test_saved_recipe_requires_admin(user_client, mock_db):
    assert (await user_client.post(path(_recipe()), json={})).status_code == 403
    mock_db.execute.assert_not_awaited()


async def test_saved_recipe_validates_target_and_cannot_accept_model_overrides(admin_client, mock_db):
    recipe = _recipe()
    mock_db.execute.return_value = _result(scalar=None)
    assert (await admin_client.post(path(recipe), json={})).status_code == 404
    for body in ({"model_path": "another/checkpoint"}, {"namespace": "bad/ns"}, {"cluster_id": "not-a-uuid"}):
        assert (await admin_client.post(path(recipe), json=body)).status_code == 422
    mock_db.execute.return_value = _result(scalar=recipe)
    mock_db.get.return_value = None
    assert (await admin_client.post(path(recipe), json={"cluster_id": str(uuid.uuid4())})).status_code == 404


async def test_observation_query_is_scoped_and_does_not_sum_pods():
    # The observation migration is independent of the recipe feature. Exercise the
    # actual joins and predicates against its column contract on an in-memory DB.
    mapper = registry()
    columns = [
        "role", "pod_uid", "fingerprint", "gpu_type", "image", "revision", "last_seen", "observed_at", "pod_name",
    ]
    table = Table(
        "custom_kv_observation", mapper.metadata,
        Column("deployment_id", Uuid, primary_key=True), Column("restart", Integer, primary_key=True),
        *(Column(name, String) for name in columns), Column("capacity_tokens", Integer),
        Column("budget_bytes", Integer), Column("active", Boolean),
    )
    observation = type("Observation", (), {"__tablename__": table.name})
    mapper.map_imperatively(observation, table)
    deployments = Table(
        "custom_model_deployment", mapper.metadata, Column("id", Uuid, primary_key=True),
        Column("cluster_id", Uuid), Column("recipe_id", Uuid), Column("namespace", String),
    )
    db = create_engine("sqlite://")
    mapper.metadata.create_all(db)
    recipe, cluster = uuid.uuid4(), uuid.uuid4()
    target = SimpleNamespace(cluster_id=cluster, namespace="serving")
    spec = {"args": [], "image": "immutable"}
    module = SimpleNamespace(Observation=observation, model_revision=lambda args: "rev", pinned=lambda *args: True)
    with Session(db) as session, patch.dict(sys.modules, {"app.services.kv_observations": module}):
        for index, (deployment_changes, observation_changes) in enumerate((
            ({}, {}), ({}, {"capacity_tokens": 200}), ({"recipe_id": uuid.uuid4()}, {}),
            ({"namespace": "other"}, {}), ({"cluster_id": uuid.uuid4()}, {}), ({}, {"role": "decode"}),
            ({}, {"fingerprint": "older"}), ({}, {"image": "another"}), ({}, {"revision": "new"}),
            ({}, {"gpu_type": "B200"}), ({}, {"capacity_tokens": None, "budget_bytes": None}),
        )):
            deployment_id = uuid.uuid4()
            session.execute(deployments.insert(), {
                "id": deployment_id, "cluster_id": cluster, "recipe_id": recipe,
                "namespace": target.namespace, **deployment_changes,
            })
            session.execute(table.insert(), {
                "deployment_id": deployment_id, "restart": 0, "role": "aggregated", "fingerprint": "fp",
                "gpu_type": "A100", "image": spec["image"], "revision": "rev", "last_seen": f"{index:02}",
                "pod_uid": str(index), "pod_name": f"pod-{index}", "active": True,
                "capacity_tokens": 100, "budget_bytes": 0, **observation_changes,
            })
        session.connection().connection.driver_connection.create_function("to_regclass", 1, lambda name: name)
        query = SimpleNamespace(scalar=AsyncMock(side_effect=session.scalar))
        row = await _saved_pool_observation(query, recipe, target, "aggregated", "fp", spec, "A100")
        assert row["pod_name"] == "pod-1" and row["capacity_tokens"] == 200 and row["budget_bytes_per_gpu"] == 0
        assert await _saved_pool_observation(query, recipe, target, "prefill", "fp", spec, "A100") is None
        module.pinned = lambda *args: False
        query.scalar.reset_mock()
        assert await _saved_pool_observation(query, recipe, target, "aggregated", "fp", spec, "A100") is None
        query.scalar.assert_not_awaited()
        module.pinned = lambda *args: True
        session.connection().connection.driver_connection.create_function("to_regclass", 1, lambda name: None)
        assert await _saved_pool_observation(query, recipe, target, "aggregated", "fp", spec, "A100") is None


async def test_saved_recipe_host_kv_does_not_increase_active_tp_capacity(admin_client, mock_db):
    recipe = _recipe(model_path="/team/project", gpu_count=2, gpu_type="A100",
                     vllm_extra_args=["-tp", "2", "--kv-cache-memory-bytes=8G"])
    mock_db.execute.return_value = _result(scalar=recipe)
    profile = SimpleNamespace(name="A100", label_value="A100-80GB", vram_gb=80)
    with (
        patch("app.api.serving_recipes.resolve_gpu_type", AsyncMock(return_value=(profile, "gpu"))),
        patch("app.services.recipe_capacity.metadata_for", AsyncMock(return_value={"config": QWEN})),
        patch("app.services.recipe_capacity._saved_pool_observation", AsyncMock(return_value=None)),
    ):
        baseline = (await admin_client.post(path(recipe), json={})).json()["pools"][0]
        recipe.vllm_extra_args.append("--kv-offloading-size=16")
        response = await admin_client.post(path(recipe), json={})
        assert response.status_code == 200, response.text
        pool = response.json()["pools"][0]
        assert "host_kv_offload" not in baseline
        assert pool["host_kv_offload"]["configured_bytes"] == 16 * GIB
        assert pool["host_kv_offload"]["scope"] == "all_workers"
        assert pool["capacity_tokens"] == baseline["capacity_tokens"] == 65536
        assert pool["complete_requests"] == baseline["complete_requests"] == 2
