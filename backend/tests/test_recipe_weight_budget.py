import pytest

from app.services.kv_capacity import GIB, estimate
from app.services.kv_metadata import pinned_reference


@pytest.fixture
def qwen():
    return pinned_reference({}, {}, "Qwen/Qwen3-32B", "9216db5781bf21249d130ec9da846c4624c16137")


@pytest.mark.parametrize("override", ["--kv-cache-memory-bytes=32GiB", "--num-gpu-blocks-override=8192"])
def test_explicit_pool_and_resident_weights_have_to_fit_the_same_gpu(qwen, override):
    result = estimate(qwen, "vllm", [override], vram_gb=80)
    assert result["resident_weight_floor_bytes_per_gpu"] == qwen["weight_bytes"]
    assert result["budget_bytes_per_gpu"] == [32 * GIB, 32 * GIB]
    assert result["budget_exceeds_vram"] and "capacity_range" not in result
    assert result["capacity_tokens"] is None and result["required_bytes_per_gpu"] == 8 * GIB
    # Explicit byte budgets override vLLM's utilization fraction, not the physical device.
    result = estimate(qwen, "vllm", ["--gpu-memory-utilization=.4", "--kv-cache-memory-bytes=8GiB"], vram_gb=80)
    assert not result.get("budget_exceeds_vram") and result["capacity_tokens"] == 32768


def test_widened_dense_projection_weights_alone_can_exceed_vram(qwen):
    result = estimate(qwen, "vllm", ["--dtype=float32"], vram_gb=80)
    assert result["weights_exceed_vram"]
    assert result["weight_load_multiplier"] == 2 and result["resident_weight_floor_bytes_per_gpu"] > 124e9
    assert result["budget_bytes_per_gpu"] == [0, 0] and result["capacity_range"] == [0, 0]
    with_kv = estimate(qwen, "vllm", ["--dtype=float32", "--kv-cache-memory-bytes=1GiB"], vram_gb=80)
    assert with_kv["budget_exceeds_vram"] and with_kv["capacity_tokens"] is None
    enough = estimate(qwen, "vllm", ["--dtype=float32", "--tensor-parallel-size=2"], vram_gb=80)
    assert enough["resident_weight_floor_bytes_per_gpu"] == result["resident_weight_floor_bytes_per_gpu"] // 2
    assert enough["capacity_range"][1] > 0 and not enough.get("weights_exceed_vram")


def test_dense_cpu_offload_is_per_gpu_and_can_cross_the_last_whole_parameter(qwen):
    plain = estimate(qwen, "vllm", [], vram_gb=80)
    result = estimate(qwen, "vllm", ["--cpu-offload-gb=8"], vram_gb=80)
    largest = 2 * 25600 * 5120 * 2  # fused gate_up_proj, BF16
    moved = result["weight_offload"]
    assert moved == {"requested_bytes_per_gpu": 8 * GIB, "min_bytes_per_gpu": 8 * GIB,
                     "max_bytes_per_gpu": 8 * GIB + largest - 1}
    assert result["budget_bytes_per_gpu"][1] - plain["budget_bytes_per_gpu"][1] == moved["max_bytes_per_gpu"]
    assert plain["capacity_range"] == [0, 55216] and result["capacity_range"] == [0, 89984]
    split = estimate(qwen, "vllm", ["-tp", "2", "--cpu-offload-gb=8"], vram_gb=80)
    assert split["weight_offload"]["max_bytes_per_gpu"] == 8 * GIB + largest // 2 - 1
    assert split["budget_bytes_per_gpu"][1] == 80_000_000_000 - qwen["weight_bytes"] // 2 + 8 * GIB + largest // 2 - 1
    # A limit and a named subset restrict the optimistic credit to eligible tensors.
    only_norm = estimate(qwen, "vllm", ["--cpu-offload-gb=8", "--cpu-offload-params=q_norm"], vram_gb=80)
    assert only_norm["weight_offload"]["max_bytes_per_gpu"] == 64 * 128 * 2
    none = estimate(qwen, "vllm", ["--cpu-offload-gb=8", "--cpu-offload-params=experts"], vram_gb=80)
    assert none["weight_offload"]["max_bytes_per_gpu"] == 0 and none["capacity_range"] == plain["capacity_range"]


def test_explicit_pool_requires_enough_resident_weight_relief(qwen):
    flags = ["--kv-cache-memory-bytes=32GiB"]
    too_small = estimate(qwen, "vllm", [*flags, "--cpu-offload-gb=8"], vram_gb=80)
    assert too_small["budget_exceeds_vram"]
    larger = estimate(qwen, "vllm", [*flags, "--cpu-offload-gb=32"], vram_gb=80)
    assert not larger.get("budget_exceeds_vram") and larger["weight_fit_unverified"]
    assert larger["budget_bytes_per_gpu"] == [32 * GIB] * 2
    assert larger["capacity_tokens"] == 131072


def test_unknown_placement_and_matching_observations_preserve_offload_configuration(qwen):
    arg = "--cpu-offload-gb=8"
    for engine, extra in (("vllm", ["--offload-group-size=4"]), ("sglang", [])):
        output = estimate(qwen, engine, [arg, *extra], vram_gb=80)
        assert output["budget_source"] == "unknown" and "capacity_range" not in output
        assert output["weight_offload"] == {"requested_bytes_per_gpu": 8 * GIB,
                                            "min_bytes_per_gpu": None, "max_bytes_per_gpu": None}
    via_startup = estimate(qwen, "vllm", [arg], vram_gb=80, observed_budget=8 * GIB)
    assert via_startup["budget_source"] == "startup" and via_startup["capacity_tokens"] == 32768
    assert via_startup["weight_offload"]["max_bytes_per_gpu"] is None


def test_unknown_precision_is_conditional_and_known_container_format_does_not_imply_quantization(qwen):
    no_dtype = {**qwen, "config": {k: v for k, v in qwen["config"].items() if k != "torch_dtype"}}
    assert estimate(no_dtype, "vllm", [], vram_gb=80)["budget_source"] == "unknown"
    quantized = {**qwen, "weight_format": "awq"}
    unknown = estimate(quantized, "vllm", ["--dtype=float32", "--kv-cache-memory-bytes=4GiB"], vram_gb=80)
    assert unknown["weight_fit_unverified"] and "weights_exceed_vram" not in unknown
    known = estimate({**qwen, "weight_format": "safetensors"}, "vllm", ["--dtype=float32"], vram_gb=80)
    assert known["weights_exceed_vram"]


def test_known_exact_expert_offload_exposes_the_moved_bytes():
    meta = pinned_reference({}, {}, "deepseek-ai/DeepSeek-V4.1-Flash", "314113f8a7dadb388215396ba5fbee2c610a04d6")
    result = estimate(meta, "vllm", ["--cpu-offload-gb=0.5", "--cpu-offload-params=routed_experts.w13_weight"],
                      vram_gb=252, gpu_type="DGX-STATION-GB300")
    actual = result["weight_residency"]["excluded_bytes"]["routed_experts"]
    assert result["weight_offload"] == {"requested_bytes_per_gpu": GIB // 2,
                                        "min_bytes_per_gpu": actual, "max_bytes_per_gpu": actual}


@pytest.mark.parametrize(("name", "revision", "vision", "audio"), [
    ("moonshotai/Kimi-K3.1-Pro", "d9e99b0792a5e93b78ea9bea62a68167e36ddd9e", 894717952, 0),
    ("zai-org/Glyph", "074a0727f3c5fe18c02d303ffd8515ae65da12a7", 1784996864, 0),
    ("amd/Muse-Spark-Open-816B-A42B-Quark-MXFP4", "17159bf7d2e7d6c79961efe1530ff40aea608ae2", 3856274432, 235680768),
])
def test_language_only_profile_excludes_only_optional_header_payloads(name, revision, vision, audio):
    meta = pinned_reference({}, {}, name, revision)
    flags = ["-tp", "8", "--language-model-only", "--gpu-memory-utilization=0.9"]
    out = estimate(meta, "vllm", flags, vram_gb=288)
    assert out["weight_residency"]["excluded_bytes"] == {"vision": vision, "audio": audio}
    floor = (meta["weight_bytes"] - vision - audio + 7) // 8
    assert out["resident_weight_floor_bytes_per_gpu"] == floor
    assert out["budget_bytes_per_gpu"] == [0, 259_200_000_000 - floor]
    # A custom checkpoint can have a different encoder; its full index is not a GPU weight floor.
    no_components = {k: v for k, v in meta.items() if k != "weight_components"}
    unknown = estimate(no_components, "vllm", flags, vram_gb=288)
    assert unknown["device_only_weight_bound"] and unknown["weight_fit_unverified"]
    assert unknown["budget_bytes_per_gpu"] == [0, 259_200_000_000]


def test_kimi_b300_dspark_with_language_only_still_returns_a_bound_and_checks_draft_weights():
    from tests.test_dspark_recipe_capacity import size

    result = size(4, extra=["--language-model-only"])
    assert not result["missing"] and result["capacity_range"][1] > 0
    remaining = 288_000_000_000 - result["resident_weight_floor_bytes_per_gpu"]
    # This fixed pool plus the target fits; the separate drafter and verification buffers do not.
    impossible = size(4, extra=["--language-model-only", f"--kv-cache-memory-bytes={remaining - 1}"])
    assert impossible["budget_exceeds_vram"] and impossible["capacity_tokens"] is None
