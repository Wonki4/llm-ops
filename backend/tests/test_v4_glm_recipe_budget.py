"""Weights that are inactive or host-resident cannot consume a recipe's GPU KV budget."""
import json

import pytest

from app.services.kv_capacity import Scenario, estimate
from app.services.kv_metadata import _resolved_weight_components, pinned_reference

V41 = "deepseek-ai/DeepSeek-V4.1-Flash"
V41_REV = "314113f8a7dadb388215396ba5fbee2c610a04d6"
GLM = "zai-org/GLM-5.3-Flash"
GLM_REV = "46a050bec13002ad29ee795c16e61cf4fa3c4a9c"


def test_float_revision_reuses_verified_sizes_only_after_the_public_job_resolves_the_same_weights():
    ref = pinned_reference({}, {}, V41, V41_REV)
    live = {k: ref[k] for k in ("revision", "config", "weight_bytes", "config_digest")}
    live.update(source="hub", engine_versions={"vllm": "0.31.0"})
    merged = _resolved_weight_components({}, {}, V41, live)
    assert merged == {**live, "weight_components": ref["weight_components"]}
    for change in ({"weight_bytes": 42}, {"config_digest": "mismatch"}, {"revision": "main"}, {"source": "mount"}):
        assert "weight_components" not in _resolved_weight_components({}, {}, V41, {**live, **change})
    mirror = {"env": [{"name": "HF_ENDPOINT", "value": "https://mirror.example"}]}
    assert "weight_components" not in _resolved_weight_components({}, mirror, V41, live)


def size_v41(*options, meta=None):
    if meta is None:
        meta = pinned_reference({}, {}, V41, V41_REV)
    return estimate(meta, "vllm", ["--model", V41, "--tensor-parallel-size=2",
                    "--gpu-memory-utilization=0.9", "--max-num-seqs=64", *options],
                    vram_gb=180, gpu_type="B200", scenario=Scenario(context=32768))


@pytest.mark.parametrize("extra", [[], ['--engram-config={"cpu_offload":true}'], ["--engram-config.cpu_offload=true"]])
def test_v41_cpu_tables_are_removed_from_the_vram_charge_only_once(extra):
    result = size_v41(*extra)
    excluded = result["weight_residency"]["excluded_bytes"]
    assert excluded["engram_embeddings"] == 202_758_032_400
    assert excluded["draft"] == 7_932_874_632
    assert result["budget_bytes_per_gpu"] == [0, 162_000_000_000 - (510_286_023_000 - sum(excluded.values())) // 2]
    assert not result["missing"] and result["capacity_range"][1] > 0


def test_v41_device_tables_can_leave_no_cache_and_a_disabled_encoder_frees_bytes():
    resident = size_v41('--engram-config={"cpu_offload":false}')
    assert resident["budget_bytes_per_gpu"] == [0, 0] and resident["capacity_range"] == [0, 0]
    with_vision, no_vision = size_v41(), size_v41("--language-model-only")
    assert no_vision["weight_residency"]["excluded_bytes"]["vision"] == 970_506_240
    assert no_vision["budget_bytes_per_gpu"][1] - with_vision["budget_bytes_per_gpu"][1] == 970_506_240 // 2
    with_images = size_v41('--limit-mm-per-prompt={"image":4}')
    assert with_images["weight_residency"] == with_vision["weight_residency"]


@pytest.mark.parametrize(("size", "params", "expected"), [
    (60, ["routed_experts.w13_weight", "routed_experts.w2_weight"], 65_682_800_640),
    (60, ["routed_experts.w2_weight"], 29 * 2_264_924_160),
    (0.5, ["routed_experts.w13_weight"], 4_529_848_320),
    (1000, ["routed_experts.w2_weight"], 40 * 2_264_924_160),
])
def test_v41_station_offloads_whole_selected_parameters_at_tp1(size, params, expected):
    meta = pinned_reference({}, {}, V41, V41_REV)
    argv = ["--offload-backend", "uva", "--cpu-offload-gb", str(size), "--cpu-offload-params",
            *params, "--gpu-memory-utilization", "0.97"]
    result = estimate(meta, "vllm", argv, gpu_type="DGX-STATION-GB300", vram_gb=252)
    weights = result["weight_residency"]
    assert weights["excluded_bytes"]["routed_experts"] == expected
    assert weights["resident_weight_bytes"] == meta["weight_bytes"] - sum(weights["excluded_bytes"].values())
    assert result["budget_bytes_per_gpu"] == [0, max(0, 244_440_000_000 - weights["resident_weight_bytes"])]
    assert result["capacity_range"][1] > 0 if size >= 60 else result["capacity_range"][1] == 0


def test_v41_unknown_offload_filters_preserve_uncertainty():
    meta = pinned_reference({}, {}, V41, V41_REV)
    argv = ["--cpu-offload-gb=60", "--cpu-offload-params", "other_layer"]
    result = estimate(meta, "vllm", argv, gpu_type="DGX-STATION-GB300", vram_gb=252)
    assert result["budget_source"] == "unknown" and "capacity_range" not in result


def test_v41_native_drafter_is_charged_once_and_receives_the_configured_width():
    ordinary = size_v41()
    spec = json.dumps({"method":"dspark", "num_speculative_tokens":5})
    draft = size_v41("--speculative-config", spec)
    assert "draft" not in draft["weight_residency"]["excluded_bytes"]
    assert ordinary["budget_bytes_per_gpu"][1] - draft["budget_bytes_per_gpu"][1] == 7_932_874_632 // 2
    assert draft["integrated_speculation"]["draft_tokens"] == 5
    assert not draft["missing"] and draft["capacity_range"][1] > 0


def test_stored_gpu_pool_has_priority_and_missing_weight_evidence_is_reported():
    meta = pinned_reference({}, {}, V41, V41_REV)
    meta.pop("weight_components")
    incomplete = size_v41(meta=meta)
    assert incomplete["budget_source"] == "unknown" and "capacity_range" not in incomplete
    explicit = size_v41("--kv-cache-memory-bytes=20G", meta=meta)
    assert explicit["budget_bytes_per_gpu"] == [20 << 30, 20 << 30]
    assert explicit["capacity_range"][1] > 0 and explicit["required_bytes_per_gpu"] > 0


def test_glm_loads_target_layers_and_skips_unconfigured_prediction_head():
    meta = pinned_reference({}, {}, GLM, GLM_REV)
    result = estimate(meta, "vllm", ["--tensor-parallel-size=8", "--gpu-memory-utilization=0.9"],
                      gpu_type="B200", vram_gb=180)
    weights = result["weight_residency"]
    assert weights["excluded_bytes"] == {"draft": 7_493_399_168}
    assert weights["resident_weight_bytes"] == 328_326_771_576 - 7_493_399_168
    assert result["capacity_range"][1] > 0 and result["required_bytes_per_gpu"] > 0
    assert result["payload_lower_bound"] and not result["missing"]
