"""CPU-only end-to-end roofline scenarios, using explicit repeatable hardware and model evidence."""
from copy import deepcopy

import pytest

from app.services import recipe_throughput as forecast


@pytest.fixture(autouse=True)
def evidence(monkeypatch):
    monkeypatch.setattr(forecast, "gpu_evidence", lambda name: {
        "bf16_tflops": 100, "bandwidth_gbps": 1000, "source_url": "https://vendor.example/gpu",
    } if name == "Test GPU" else None)
    monkeypatch.setattr(forecast, "model_evidence", lambda meta, name: meta.get("evidence"))
    monkeypatch.setattr(forecast, "workload_kind", lambda meta, name: meta.get("task", "text"))


def replica(**overrides):
    pool = {
        "role": "aggregated", "model_path": "test/model", "gpu_count": 2, "gpu_type": "Test GPU",
        "tp": 2, "pp": 1, "engine": "vllm", "flags": {}, "max_num_seqs": 64,
        "max_model_len": 131072, "block_tokens": 16,
        "budget_bytes_per_gpu": [0, 25_000_000_000], "complete_requests_range": [0, 128],
        "metadata": {"weight_bytes": 6_000_000_000,
                     "config": {"num_hidden_layers": 32, "num_attention_heads": 32, "hidden_size": 3072},
                     "evidence": {"kind": "dense", "active_params": 3e9, "total_params": 3e9,
                                  "source_url": "https://publisher.example/model"}},
        "components": [{"unit": "token", "kind": "full_attention", "layers": 32, "bytes_per_gpu": 32768}],
    }
    return {**pool, **overrides}


def estimate(pool=None, *, pools=None, **params):
    scenario = {"input_tokens": 8192, "output_tokens": 1024, "concurrency": 8, "shared_prefix_percent": 0, **params}
    return forecast.estimate_throughput(pools if pools is not None else [pool or replica()], scenario)


def test_rates_share_same_requests_and_full_logical_token_accounting():
    result = estimate(input_tokens=100000, output_tokens=1000, shared_prefix_percent=90)
    assert result["status"] == "estimated" and 0 < result["rpm"][0] < result["rpm"][1]
    assert result["input_tpm"] == [r * 100000 for r in result["rpm"]]
    assert result["output_tpm"] == [r * 1000 for r in result["rpm"]]
    assert [s["url"] for s in result["sources"]] == [
        "https://vendor.example/gpu", "https://publisher.example/model", forecast.ROOFLINE,
    ]
    assert result["scope"] == "replica" and "analytical" in result["assumptions"]


def test_cold_and_warm_cache_and_length_controls():
    base = estimate()
    warm = estimate(shared_prefix_percent=90)
    assert warm["rpm"][0] == base["rpm"][0] and warm["rpm"][1] > base["rpm"][1]
    assert "prefix_reuse" in warm["assumptions"]
    disabled = estimate(replica(flags={"no-enable-prefix-caching": "true"}), shared_prefix_percent=90)
    assert "prefix_disabled" in disabled["assumptions"] and disabled["rpm"] == base["rpm"]
    assert estimate(output_tokens=4096)["rpm"][1] < base["rpm"][1]
    assert estimate(input_tokens=32768)["rpm"][1] < base["rpm"][1]
    assert estimate(concurrency=32)["rpm"][1] > base["rpm"][1]
    assert estimate(replica(replicas=8))["rpm"] == base["rpm"]


def test_conditional_ranges_compare_same_cache_state_at_both_efficiencies():
    pool = replica(block_tokens=256)
    workload = {"input_tokens": 100000, "output_tokens": 1000}
    result = estimate(pool, shared_prefix_percent=90, **workload)
    cases = result["cache_scenarios"]
    cold, warm = cases["cold"], cases["warm"]
    assert cold["rpm"] == estimate(pool, shared_prefix_percent=0, **workload)["rpm"]
    assert cold["reused_input_tokens"] == 0 and warm["reused_input_tokens"] == 89856
    assert cold["rpm"][0] < warm["rpm"][0] and cold["rpm"][1] < warm["rpm"][1]
    assert result["rpm"] == [cold["rpm"][0], warm["rpm"][1]]
    for case in (cold, warm):
        assert case["effective_concurrency"] == 8
        assert case["input_tpm"] == [n * 100000 for n in case["rpm"]]
        assert case["output_tpm"] == [n * 1000 for n in case["rpm"]]
        prefill = case["phase_seconds"]["prefill"]
        decode = case["phase_seconds"]["decode"]
        assert case["rpm"] == pytest.approx([60 * 8 / (prefill[k] + decode[k]) for k in (1, 0)])
    assert warm["phase_seconds"]["decode"] == cold["phase_seconds"]["decode"]
    assert warm["phase_seconds"]["prefill"][1] < cold["phase_seconds"]["prefill"][1]


def test_warm_condition_needs_a_reusable_block_and_preserves_the_final_prompt_logit():
    for engine in ("vllm", "sglang"):
        pool = replica(engine=engine, block_tokens=16)
        assert "warm" not in estimate(pool, input_tokens=16, shared_prefix_percent=100)["cache_scenarios"]
        assert "warm" not in estimate(pool, input_tokens=100, shared_prefix_percent=15)["cache_scenarios"]
        full = estimate(pool, input_tokens=32, shared_prefix_percent=100)
        assert full["cache_scenarios"]["warm"]["reused_input_tokens"] == 16
    for flag in ("disable-prefix-caching", "no-enable-prefix-caching", "disable-radix-cache"):
        result = estimate(replica(flags={flag: "true"}), shared_prefix_percent=90)
        assert list(result["cache_scenarios"]) == ["cold"]
        assert result["cache_scenarios"]["cold"]["rpm"] == result["rpm"]


def test_pair_only_offers_warm_prefix_if_prefill_can_reuse_it():
    decode = replica(role="decode")
    prefill = replica(role="prefill", flags={"disable-prefix-caching": "true"})
    cold = estimate(pools=[prefill, decode], shared_prefix_percent=90)
    assert list(cold["cache_scenarios"]) == ["cold"]
    prefill["flags"] = {}
    decode["flags"] = {"disable-prefix-caching": "true"}
    result = estimate(pools=[prefill, decode], shared_prefix_percent=90)
    warm = result["cache_scenarios"]["warm"]
    assert warm["reused_input_tokens"] == 7360 and warm["effective_concurrency"] == 8
    assert warm["rpm"][0] >= result["cache_scenarios"]["cold"]["rpm"][0]
    assert all("phase_seconds" not in case for case in result["cache_scenarios"].values())


def test_explicit_prefix_match_uses_token_granularity_with_engine_selected_cache_pages():
    pool = replica(block_tokens=16, flags={"prefix-match-unit": "128"})
    result = estimate(pool, input_tokens=4096, shared_prefix_percent=90)
    assert result["cache_scenarios"]["warm"]["reused_input_tokens"] == 3584
    pool.update(budget_bytes_per_gpu=[0, 0])
    assert estimate(pool, shared_prefix_percent=90)["reason"] == "gpu_memory"
    assert estimate(pool, shared_prefix_percent=0)["reason"] == "gpu_memory"


def test_dynamic_fp8_weights_and_drafter_scheduler_change_the_effective_recipe():
    static = replica()
    static["metadata"]["config"]["dtype"] = "bfloat16"
    quantized = {**static, "flags": {"quantization": "fp8"}}
    baseline, converted = estimate(static), estimate(quantized)
    assert "runtime_fp8" in converted["assumptions"]
    assert converted["rpm"][1] > baseline["rpm"][1]
    active = estimate(replica(integrated_speculation={"draft_tokens": 7, "active_requests": 2}))
    assert active["effective_concurrency"] == [2, 2]


def test_kv_and_scheduler_cap_admitted_requests_with_warm_shared_pages():
    pool = replica(max_num_seqs=12, complete_requests_range=[0, 4], budget_bytes_per_gpu=[0, 470],
                   required_bytes_per_gpu=800, shared_required_bytes_per_gpu=450, requests=8)
    result = estimate(pool, shared_prefix_percent=90)
    assert result["effective_concurrency"] == [4, 8]
    assert "scheduler_capped" in result["assumptions"]
    assert estimate(replica(max_num_seqs=3), concurrency=8)["effective_concurrency"] == [3, 3]


@pytest.mark.parametrize(("pool", "reason", "status"), [
    (replica(gpu_count=0), "no_gpu", "unavailable"),
    (replica(gpu_type="custom"), "gpu_unknown", "unavailable"),
    (replica(max_model_len=8192), "context_limit", "infeasible"),
    (replica(complete_requests_range=[0, 0]), "kv_insufficient", "infeasible"),
    (replica(budget_bytes_per_gpu=[0, 0]), "gpu_memory", "infeasible"),
    (replica(gpu_count=1, tp=2), "invalid_recipe", "infeasible"),
    (replica(metadata={"task": "pooling"}), "unsupported_workload", "unavailable"),
    (replica(flags={"runner": "pooling"}), "unsupported_workload", "unavailable"),
    (replica(flags={"speculative-config": '{"method":"eagle3"}'}), "draft_unknown", "unavailable"),
    (replica(metadata={}), "model_unknown", "unavailable"),
    (replica(flags={"data-parallel-size": "8"}), "unsupported_topology", "unavailable"),
])
def test_missing_and_infeasible_never_emit_fabricated_rates(pool, reason, status):
    result = estimate(pool)
    assert (result["status"], result["reason"]) == (status, reason)
    assert result["rpm"] is result["input_tpm"] is result["output_tpm"] is None


def test_kv_unknown_remains_an_explicit_assumption():
    result = estimate(replica(complete_requests_range=None, capacity_tokens=None))
    assert result["status"] == "estimated" and "kv_unverified" in result["assumptions"]


def test_upper_bound_slots_and_unverified_weight_placement_are_conditional():
    uncertain = estimate(replica(complete_requests_range=[0, 6]))
    assert uncertain["effective_concurrency"] == [6, 6] and "kv_unverified" in uncertain["assumptions"]
    fitted = replica(complete_requests_range=[8, 8])
    assert "kv_unverified" not in estimate(fitted)["assumptions"]
    assert "kv_unverified" in estimate({**fitted, "weight_fit_unverified": True})["assumptions"]


@pytest.mark.parametrize("memory_error", [
    {"budget_exceeds_vram": True}, {"weights_exceed_vram": True}, {"budget_bytes_per_gpu": [0, 0]},
])
def test_physical_memory_errors_take_priority_over_workload_and_pd_diagnostics(memory_error):
    broken = replica(flags={"dtype": "float32"}, max_model_len=1, **memory_error)
    for pools in ([broken], [replica(role="prefill", metadata={}), {**broken, "role": "decode"}]):
        result = estimate(pools=pools)
        assert (result["status"], result["reason"], result["rpm"]) == ("infeasible", "gpu_memory", None)


def test_fp32_throughput_requires_compatible_hardware_evidence():
    result = estimate(replica(flags={"dtype": "float32"}))
    assert (result["status"], result["reason"], result["rpm"]) == ("unavailable", "unsupported_precision", None)


def test_explicit_kv_and_weight_interval_share_physical_device_memory():
    pool = replica(budget_bytes_per_gpu=[8e9, 8e9], budget_source="explicit", gpu_vram_gb=10)
    del pool["metadata"]["weight_bytes"]
    pool["metadata"]["evidence"].update(weight_bytes_range=[5e9, 7e9], weight_basis="shard_files")
    # At least 5 GB of weights and two 8 GB caches would require 21 GB across 20 GB of VRAM.
    assert estimate(pool)["reason"] == "gpu_memory"
    pool.update(budget_bytes_per_gpu=[4e9, 4e9], flags={"gpu-memory-utilization": "0.5"})
    assert estimate(pool)["status"] == "estimated"


def test_fixed_gpu_cache_leaves_room_for_a_separate_external_drafter():
    pool = replica(budget_source="explicit", budget_bytes_per_gpu=[7e9, 7e9], gpu_vram_gb=10,
                   resident_weight_floor_bytes_per_gpu=3e9)
    model = pool["metadata"]["evidence"]
    forecast._check_weight_fit(model, pool, {})
    pool["draft_plan"] = {"included_in_target_index": False, "draft_bytes": 1_000_000_000}
    with pytest.raises(forecast.EstimateUnavailableError, match="gpu_memory"):
        forecast._check_weight_fit(model, pool, {})
    pool["draft_plan"]["included_in_target_index"] = True
    forecast._check_weight_fit(model, pool, {})


def test_private_gpu_profile_can_identify_the_placed_board_by_its_label():
    expected = estimate()
    by_label = estimate(replica(gpu_type="local-cluster-profile", gpu_label_value="Test GPU"))
    assert by_label["status"] == "estimated" and by_label["rpm"] == expected["rpm"]


def test_pd_profile_uses_matched_roles_and_prompt_handoff():
    agg = estimate()
    pools = [replica(role="decode"), replica(role="prefill")]
    pair = estimate(pools=pools)
    assert pair["status"] == "estimated" and pair["scope"] == "pd_pair"
    assert pair["rpm"] != agg["rpm"] and "pd_transfer" in pair["assumptions"]
    assert estimate(pools=[replica(role="decode")])["reason"] == "unsupported_topology"
    pools[1]["model_path"] = "other"
    assert estimate(pools=pools)["reason"] == "unsupported_topology"


def test_no_fixed_draft_speedup_and_offload_cost():
    base = estimate()
    draft = estimate(replica(integrated_speculation={"draft_tokens": 4}))
    assert "speculation" in draft["assumptions"]
    assert draft["rpm"][0] <= base["rpm"][0]
    offload = estimate(replica(flags={"cpu-offload-gb": "2"}))
    assert {"offload", "weight_offload_unmeasured"} <= set(offload["assumptions"])
    assert offload["rpm"][0] < base["rpm"][0] and offload["rpm"][1] == base["rpm"][1]
    known = estimate(replica(flags={"cpu-offload-gb": "2"},
                             weight_residency={"excluded_bytes": {"routed_experts": 3e9}}))
    assert "offload" in known["assumptions"] and "weight_offload_unmeasured" not in known["assumptions"]
    assert known["rpm"][1] < base["rpm"][1]
    bounded = estimate(replica(flags={"cpu-offload-gb": "2"},
                               weight_offload={"min_bytes_per_gpu": 1e9, "max_bytes_per_gpu": 2e9}))
    assert bounded["rpm"][1] < base["rpm"][1] and bounded["rpm"][0] > offload["rpm"][0]
    empty = estimate(replica(flags={"cpu-offload-gb": "2"},
                             weight_offload={"min_bytes_per_gpu": 0, "max_bytes_per_gpu": 0}))
    assert empty["rpm"] == base["rpm"] and "offload" not in empty["assumptions"]


@pytest.mark.parametrize("configured_bytes", [None, 2**30, 0])
def test_host_cache_has_no_gpu_slots_or_unmeasured_transfer_speed(configured_bytes):
    base = estimate(shared_prefix_percent=90)
    result = estimate(replica(host_kv_offload={"configured_bytes": configured_bytes}), shared_prefix_percent=90)
    assert result["cache_scenarios"] == base["cache_scenarios"]
    assert ("host_kv_transfer_unmeasured" in result["assumptions"]) == (configured_bytes != 0)


def test_moe_expert_routing_and_sparse_shorter_attention():
    pool = replica()
    model = deepcopy(pool["metadata"]["evidence"])
    model.update(total_params=100e9, kind="moe")
    pool["metadata"]["evidence"] = model
    assert "moe_routing" in estimate(pool)["assumptions"]
    cfg = {"num_hidden_layers": 4, "num_attention_heads": 4, "hidden_size": 256}
    full = forecast._attention_layers(cfg, 4, 4)
    window = forecast._attention_layers({**cfg, "sliding_window": 128}, 4, 4)
    assert forecast._attention_flops(full, 0, 2048) > forecast._attention_flops(window, 0, 2048)
    assert forecast._attention_flops(full, 1024, 2048) < forecast._attention_flops(full, 0, 2048)


def test_known_block_map_and_uncertain_checkpoint_payload():
    pool = replica()
    cfg = pool["metadata"]["config"]
    cfg.pop("num_hidden_layers")
    cfg["layers_block_type"] = ["attention", "mamba", "moe"] * 10
    del pool["metadata"]["weight_bytes"]
    model = pool["metadata"]["evidence"]
    model.update(weight_bytes_range=[4e9, 7e9], weight_basis="shard_files",
                 weight_source_url="https://publisher.example/model/shards")
    result = estimate(pool)
    assert result["status"] == "estimated" and "weight_approx" in result["assumptions"]
    assert any(s["url"] == model["weight_source_url"] for s in result["sources"])
    pool.update(budget_bytes_per_gpu=None, gpu_vram_gb=1)
    assert estimate(pool)["reason"] == "weight_budget"
    pool["flags"]["cpu-offload-gb"] = 3
    assert estimate(pool)["status"] == "estimated"
