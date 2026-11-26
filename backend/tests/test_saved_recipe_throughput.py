import sys
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.kv_metadata import pinned_reference
from app.services.recipe_throughput_history import confirmed_rpm_range, matching_recipe_benchmark
from tests.test_serving_recipes import _recipe, _result

MODEL = "Qwen/Qwen3-32B"
REV = "9216db5781bf21249d130ec9da846c4624c16137"
WORKLOAD = {"input_tokens": 1024, "output_tokens": 128, "concurrency": 4, "shared_prefix_percent": 50}


async def test_saved_throughput_uses_effective_config_and_keeps_private_inputs_off_the_wire(admin_client, mock_db):
    recipe = _recipe(model_path=MODEL, gpu_type="H200", gpu_count=2, hf_repo_id=MODEL,
                     vllm_extra_args=["--tensor-parallel-size=2", "--revision", REV, "--max-model-len=8192",
                                      "--api-key", "private-test-value", "--kv-cache-memory-bytes=8G"])
    mock_db.execute.return_value = _result(scalar=recipe)
    profile = SimpleNamespace(name="H200", label_value="H200", vram_gb=141)
    read = AsyncMock(return_value=pinned_reference({}, {}, MODEL, REV))
    url = f"/api/admin/serving-recipes/{recipe.id}/throughput"
    with (
        patch("app.api.serving_recipes.resolve_gpu_type", AsyncMock(return_value=(profile, "gpu"))),
        patch("app.services.recipe_capacity.metadata_for", read),
        patch("app.services.recipe_capacity._saved_pool_observation", AsyncMock(return_value=None)),
    ):
        response = await admin_client.post(url, json={**WORKLOAD, "namespace": "preview-check"})
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["status"] == "estimated", result
        assert result["scenario"] == WORKLOAD and result["namespace"] == "preview-check"
        assert result["recipe_id"] == str(recipe.id) and result["pools"][0]["gpu_count"] == 2
        assert len(result["rpm"]) == 2 and 0 < result["rpm"][0] <= result["rpm"][1]
        assert result["input_tpm"] == pytest.approx([r * WORKLOAD["input_tokens"] for r in result["rpm"]])
        cold, warm = (result["cache_scenarios"][key] for key in ("cold", "warm"))
        assert cold["reused_input_tokens"] == 0 and warm["reused_input_tokens"] == 512
        assert result["rpm"] == [min(cold["rpm"][0], warm["rpm"][0]), max(cold["rpm"][1], warm["rpm"][1])]
        assert "private-test-value" not in response.text and '"flags"' not in response.text
        too_long = (await admin_client.post(url, json={**WORKLOAD, "input_tokens": 8192})).json()
        assert too_long["status"] == "infeasible" and too_long["reason"] == "context_limit"
        suggestion = too_long["shorter_workload"]
        assert suggestion == {"status": "estimated", "reason": None, "scenario": {**WORKLOAD, "input_tokens": 8064}}
        # Checking the alternative uses the same model evidence and does not submit a second read.
        assert read.await_count == 2
        retried = (await admin_client.post(url, json=suggestion["scenario"])).json()
        assert retried["status"] == suggestion["status"] and retried["rpm"][0] > 0
    mock_db.add.assert_not_called()


async def test_physical_memory_blocker_prevents_shorter_request_suggestion(admin_client, mock_db):
    recipe = _recipe(model_path=MODEL, hf_repo_id=MODEL, gpu_type="H200", gpu_count=1,
                     vllm_extra_args=["--revision", REV, "--max-model-len=8192", "--kv-cache-memory-bytes=250G"])
    mock_db.execute.return_value = _result(scalar=recipe)
    read = AsyncMock(return_value=pinned_reference({}, {}, MODEL, REV))
    with (
        patch("app.api.serving_recipes.resolve_gpu_type",
              AsyncMock(return_value=(SimpleNamespace(name="H200", label_value="H200", vram_gb=141), "gpu"))),
        patch("app.services.recipe_capacity.metadata_for", read),
        patch("app.services.recipe_capacity._saved_pool_observation", AsyncMock(return_value=None)),
    ):
        res = await admin_client.post(f"/api/admin/serving-recipes/{recipe.id}/throughput",
                                      json={**WORKLOAD, "input_tokens": 100_000})
        assert res.status_code == 200
        data = res.json()
        assert data["reason"] == "gpu_memory" and data["rpm"] is None
        assert data.get("shorter_workload") is None
        assert data["pools"][0]["gpu_vram_gb"] == 141
        assert read.await_count == 1
    mock_db.add.assert_not_called()


async def test_kv_limited_alternative_is_sized_again_until_one_request_fits(admin_client, mock_db):
    recipe = _recipe(model_path=MODEL, hf_repo_id=MODEL, gpu_type="H200", gpu_count=1,
                     vllm_extra_args=["--revision", REV, "--max-model-len=32768", "--kv-cache-memory-bytes=256MiB"])
    mock_db.execute.return_value = _result(scalar=recipe)
    read = AsyncMock(return_value=pinned_reference({}, {}, MODEL, REV))
    with (
        patch("app.api.serving_recipes.resolve_gpu_type",
              AsyncMock(return_value=(SimpleNamespace(name="H200", label_value="H200", vram_gb=141), "gpu"))),
        patch("app.services.recipe_capacity.metadata_for", read),
        patch("app.services.recipe_capacity._saved_pool_observation", AsyncMock(return_value=None)),
    ):
        url = f"/api/admin/serving-recipes/{recipe.id}/throughput"
        data = (await admin_client.post(url, json={**WORKLOAD, "input_tokens": 16384})).json()
        assert data["reason"] == "kv_insufficient" and data["shorter_workload"]["status"] == "estimated"
        alternative = data["shorter_workload"]["scenario"]
        assert alternative == {**WORKLOAD, "input_tokens": 512}
        assert read.await_count == 1
        assert (await admin_client.post(url, json=alternative)).json()["status"] == "estimated"


async def test_throughput_requires_admin(user_client):
    url = "/api/admin/serving-recipes/00000000-0000-0000-0000-000000000001/throughput"
    assert (await user_client.post(url, json=WORKLOAD)).status_code == 403


async def test_throughput_requires_valid_workload(admin_client):
    url = "/api/admin/serving-recipes/00000000-0000-0000-0000-000000000001/throughput"
    for change in ({"input_tokens": 0}, {"output_tokens": -1}, {"shared_prefix_percent": 101},
                   {"concurrency": True}, {"input_tokens": 1.1}, {"gpu_type": "H200"}):
        assert (await admin_client.post(url, json={**WORKLOAD, **change})).status_code == 422


def test_history_uses_only_repeated_confirmations_at_matching_load_and_actual_lengths():
    params = {"random_input_len": 1024, "random_output_len": 128, "max_concurrency": 4, "cache_ratio": 50}
    points = [
        {"step": "confirmation", "passed": True, "target_rate": 1, "completed": 30,
         "all_rates": {"rpm": rate, "input_tpm": rate * 1024, "output_tpm": rate * 128}}
        for rate in (58, 60, 59)
    ]
    search = {"confirmed": True, "scope": "deployment", "best": {"target_rate": 1, "repetitions": 3}, "probes": points}
    result = {"capacity_search": search}
    assert confirmed_rpm_range(params, result, WORKLOAD) == [58, 60]
    assert confirmed_rpm_range({**params, "cache_ratio": 0}, result, WORKLOAD) is None
    assert confirmed_rpm_range(params, {"capacity_search": {**search, "scope": "endpoint"}}, WORKLOAD) is None
    assert confirmed_rpm_range(params, {"capacity_search": {**search, "probes": points[:1]}}, WORKLOAD) is None
    points[-1]["all_rates"]["output_tpm"] *= 0.5
    assert confirmed_rpm_range(params, result, WORKLOAD) is None


async def test_benchmark_history_requires_immutable_recipe_image_weights_and_deployment(monkeypatch):
    container = {"name": "vllm", "args": ["--revision", REV]}
    manifests = [{"kind": "Deployment", "spec": {"template": {"spec": {"containers": [container]}}}}]
    pool = {"role": "aggregated", "replicas": 1, "metadata_source": "pinned_reference", "engine": "vllm",
            "image": "engine@sha256:" + "f" * 64, "revision": REV}
    params = {"random_input_len": 1024, "random_output_len": 128, "max_concurrency": 4, "cache_ratio": 50,
              "_capacity_identity": "original"}
    probes = [{"step": "confirmation", "target_rate": 2, "passed": True, "completed": 10,
               "all_rates": {"rpm": r, "input_tpm": r * 1024, "output_tpm": r * 128}} for r in (119, 121)]
    search = {"confirmed": True, "scope": "deployment", "best": {"target_rate": 2, "repetitions": 2}, "probes": probes}
    run = SimpleNamespace(id=uuid.uuid4(), params=params, result={"capacity_search": search})
    dep = SimpleNamespace(gpu_type="B200", runtime=None, tolerations=[], namespace="test", cluster_id=None)
    db = AsyncMock()
    db.execute.return_value = [(run, dep)]
    monkeypatch.setitem(sys.modules, "app.services.capacity_identity",
                        SimpleNamespace(deployment_identity=lambda _: "original"))
    monkeypatch.setattr("app.api.benchmarks._serving_snapshot", lambda _: {})
    async def history():
        return await matching_recipe_benchmark(db, uuid.uuid4(), dep, [pool], WORKLOAD, manifests)
    result = await history()
    assert result["method"] == "historical" and result["rpm"] == [119, 121]
    assert "cache_scenarios" not in result
    assert result["benchmark_id"] == str(run.id)
    params["_capacity_identity"] = "different-deployment"
    assert await history() is None
    params["_capacity_identity"] = "original"
    container["args"] = ["--revision=main"]
    assert await history() is None
    container["args"] = ["--revision", REV, "--speculative-config={}"]
    assert await history() is None


def test_shorter_workload_respects_all_pool_limits_and_never_increases_a_field():
    from app.services.recipe_capacity import _shorter_workload

    assert _shorter_workload(WORKLOAD, [{"max_model_len": 1000}, {"max_model_len": 800}]) == {
        **WORKLOAD, "input_tokens": 672,
    }
    assert _shorter_workload(WORKLOAD, [{"max_model_len": 8}]) == {
        **WORKLOAD, "input_tokens": 1, "output_tokens": 7,
    }
    assert _shorter_workload(WORKLOAD, [{"max_model_len": 1}]) is None
    assert _shorter_workload({**WORKLOAD, "input_tokens": 1, "output_tokens": 1}, []) is None
