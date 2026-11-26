"""Bundled metadata is a source of model dimensions, never a recipe capacity result."""

import pytest

from app.services.kv_capacity import Scenario, estimate
from app.services.kv_metadata import pinned_reference

MODEL = "Qwen/Qwen3-32B"
REVISION = "9216db5781bf21249d130ec9da846c4624c16137"


def test_explicit_recipe_settings_drive_capacity_for_the_same_checkpoint():
    meta = pinned_reference({}, {}, MODEL, REVISION)
    assert meta["revision"] == REVISION and meta["source"] == "pinned_reference"
    assert meta["weight_bytes"] == 65_524_246_528
    for gib, tp, expected in [(8, 1, 32768), (12, 1, 49152), (8, 2, 65536)]:
        result = estimate(
            meta,
            "vllm",
            [f"--kv-cache-memory-bytes={gib}G", f"--tensor-parallel-size={tp}"],
            scenario=Scenario(32768),
        )
        assert result["capacity_tokens"] == expected
        assert result["required_bytes_per_gpu"] == 8 * (1 << 30) // tp


@pytest.mark.parametrize(
    "model,revision,pod,container",
    [
        (MODEL, "main", {}, {}),
        (MODEL, "b" * 40, {}, {}),
        ("other/Qwen3-32B", REVISION, {}, {}),
        ("/models/Qwen3-32B", REVISION, {}, {}),
        (MODEL, REVISION, {}, {"env": [{"name": "HF_ENDPOINT", "value": "https://mirror.example"}]}),
        (MODEL, REVISION, {}, {"envFrom": [{"configMapRef": {"name": "hub-config"}}]}),
        (
            MODEL,
            REVISION,
            {"volumes": [{"name": "models", "persistentVolumeClaim": {"claimName": "weights"}}]},
            {"volumeMounts": [{"name": "models", "mountPath": "/models"}]},
        ),
    ],
)
def test_local_or_unpinned_models_follow_the_existing_metadata_reader(model, revision, pod, container):
    assert pinned_reference(pod, container, model, revision) is None


def test_kimi_fast_safetensors_has_a_profile_based_upper_bound():
    meta = pinned_reference({}, {}, "moonshotai/Kimi-K3.1-Pro", "d9e99b0792a5e93b78ea9bea62a68167e36ddd9e")
    result = estimate(meta, "vllm", ["--tensor-parallel-size=8", "--load-format=fastsafetensors"], vram_gb=288)
    assert result["budget_source"] == "profile_upper_bound"
    assert result["capacity_tokens"] is None
    assert result["capacity_range"][1] > 0


def test_unknown_cache_layout_preserves_recipe_gpu_budget():
    reference = pinned_reference({}, {}, MODEL, REVISION)
    meta = {**reference, "config": {**reference["config"], "model_type": "unrecognized_test_decoder"}}
    result = estimate(meta, "vllm", ["--tensor-parallel-size=8", "--gpu-memory-utilization=0.9"], vram_gb=141)
    assert result["budget_source"] == "profile_upper_bound"
    assert result["budget_bytes_per_gpu"] == [0, 126_900_000_000 - meta["weight_bytes"] // 8]
    assert result["capacity_tokens"] is None and result["missing"]


def test_observed_zero_budget_survives_missing_metadata():
    result = estimate({}, "vllm", [], observed_budget=0)
    assert result["budget_source"] == "startup"
    assert result["budget_bytes_per_gpu"] == [0, 0]
    assert result["capacity_tokens"] is None
