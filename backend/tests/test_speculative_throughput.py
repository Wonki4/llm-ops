import json

import pytest

from app.services.kv_metadata import pinned_reference
from app.services.recipe_throughput import estimate_throughput
from app.services.speculative_throughput import (
    DraftEvidenceError,
    _profiles,
    draft_adjusted_pool,
    draft_plan,
    draft_timing,
    public_draft_known,
    speculative_options,
)

EAGLE = "lightseekorg/kimi-k2.6-eagle3-mla"
HY4 = "tencent/Hy4"
MTP = "nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B-NVFP4"
DSPARK = "thinkingmachines/Inkling-2-Small"
TARGET = "moonshotai/Kimi-K2.6"


def replica(name, width=3):
    row = _profiles()[name]
    integrated = row["integrated"]
    model = name if integrated else TARGET
    revision = row["revision"] if integrated else row["target_revisions"][model]
    args = {"method": row["method"], "num_speculative_tokens": width}
    if not integrated:
        args["model"] = name
    return {
        "model_path": model,
        "engine": "vllm",
        "tp": 8,
        "pp": 1,
        "public_hub": True,
        "gpu_type": "GB300",
        "gpu_count": 8,
        "role": "aggregated",
        "gpu_vram_gb": 288,
        "metadata": pinned_reference({}, {}, model, revision),
        "flags": {"speculative-config": json.dumps(args)},
    }


@pytest.mark.parametrize("name", [HY4, MTP, EAGLE, DSPARK, "amd/MiniMax-M3.1-Flash-Quark-MXFP4"])
def test_engine_and_revision_backed_results(name):
    scenario = {"input_tokens": 1024, "output_tokens": 128, "concurrency": 4, "shared_prefix_percent": 90}
    first = estimate_throughput([replica(name, 2)], scenario)
    wider = estimate_throughput([replica(name, 3)], scenario)
    assert first["status"] == wider["status"] == "estimated", wider
    assert first["rpm"] != wider["rpm"] and "speculation" in wider["assumptions"]
    assert any(_profiles()[name]["revision"] in item["url"] for item in wider["sources"])
    assert ("draft_revision_assumed" in wider["assumptions"]) == (name == EAGLE)


def test_launch_precedence_and_invalid_values_are_redacted():
    assert speculative_options({"speculative-config": '{"model":"sensitive"}', "speculative-config.method": "mtp"}) == {
        "method": "mtp"
    }
    pool = replica(HY4)
    pool["flags"] = {"speculative-config": '{"method":"secret","model":"private"}'}
    with pytest.raises(DraftEvidenceError) as err:
        draft_plan(pool)
    assert err.value.method == "other" and err.value.missing == "draft_options"
    assert not public_draft_known(EAGLE, "unrecognized")


def test_external_source_and_both_private_proofs_are_required():
    p = replica(EAGLE)
    ref = _profiles()[EAGLE]
    p.update(public_hub=False, model_path="/models/target")
    p["metadata"] = {"source": "mount", **ref["target_proofs"][TARGET]}
    p["flags"]["speculative-config"] = json.dumps(
        {"method": "eagle3", "model": "/models/draft", "num_speculative_tokens": 3}
    )
    p["draft_metadata"] = {"source": "mount", **ref["geometry_proof"]}
    assert not draft_plan(p)["assumed_revision"]
    p["metadata"]["weight_index_digest"] = "stale"
    with pytest.raises(DraftEvidenceError) as err:
        draft_plan(p)
    assert err.value.missing == "draft_geometry"
    p["draft_metadata"]["speculative_tensor_digest"] = "stale"
    with pytest.raises(DraftEvidenceError) as err:
        draft_plan(p)
    assert err.value.missing == "draft_checkpoint"


def test_integrated_private_geometry_same_size_different_index():
    p = replica(HY4)
    p.update(
        public_hub=False, model_path="/models/hy", metadata={"source": "mount", **_profiles()[HY4]["geometry_proof"]}
    )
    assert draft_plan(p)["method"] == "dfly"
    p["metadata"]["weight_index_digest"] = "different"
    with pytest.raises(DraftEvidenceError) as err:
        draft_plan(p)
    assert err.value.missing == "draft_checkpoint"


def test_budget_and_scheduler_apply_once():
    for name in (EAGLE, MTP, DSPARK):
        p = replica(name)
        p.update(budget_bytes_per_gpu=[0, 20_000_000_000], budget_source="profile_upper_bound")
        p["flags"]["max-num-batched-tokens"] = "8"
        plan = draft_plan(p)
        actual = draft_adjusted_pool(p, plan, 1024, 8)
        assert actual["max_num_seqs"] == 2
        hi = actual["budget_bytes_per_gpu"][1]
        assert (hi == p["budget_bytes_per_gpu"][1]) == plan["included_in_target_index"]
        p["budget_source"] = "explicit"
        assert draft_adjusted_pool(p, plan, 1024, 8)["budget_bytes_per_gpu"] == p["budget_bytes_per_gpu"]


def test_historical_reads_and_draft_compute():
    for name in (EAGLE, MTP, HY4, DSPARK):
        detail = draft_plan(replica(name))
        small = draft_timing(detail, 128, 1, 2, 128, 2e13, 1e12, 8, 5e-6)
        bigger = draft_timing(detail, 1024, 8, 3, 512, 2e13, 1e12, 8, 5e-6)
        assert bigger[1] > small[1] > 0
        assert (bigger[0] == 0) == (name == HY4)


def test_unsupported_variants_and_batch_are_explicit():
    p = replica(DSPARK, 8)
    with pytest.raises(DraftEvidenceError) as err:
        draft_plan(p)
    assert err.value.missing == "draft_options"
    p = replica(HY4)
    p["role"] = "decode"
    with pytest.raises(DraftEvidenceError) as err:
        draft_plan(p)
    assert err.value.missing == "draft_placement"


@pytest.mark.parametrize(
    ("name", "target", "backend"),
    [
        ("Inferact/Kimi-K3-DSpark", "moonshotai/Kimi-K3", "ROCM_AITER_MLA"),
        ("Inferact/MiniMax-M3-EAGLE3-GQA", "amd/MiniMax-M3-MXFP4", "ROCM_AITER_UNIFIED_ATTN"),
        ("Inferact/MiniMax-M3-EAGLE3-GQA", "nvidia/MiniMax-M3-NVFP4", "FLASHINFER"),
    ],
)
def test_additional_public_external_backends(name, target, backend):
    row = _profiles()[name]
    p = replica(EAGLE)
    p["model_path"] = target
    p["metadata"] = pinned_reference({}, {}, target, row["target_revisions"][target])
    opts = {"method": row["method"], "model": name, "attention_backend": backend, "num_speculative_tokens": 3}
    if row["method"] == "dspark":
        opts["kv_cache_dtype"] = "fp8"
    if backend == "FLASHINFER":
        opts["use_local_argmax_reduction"] = True
    p["flags"] = {"speculative-config": json.dumps(opts)}
    plan = draft_plan(p)
    assert plan["revision"] == row["revision"] and plan["draft_bytes"] > 1e9


def test_kimi31_specialized_kv_is_charged_once_and_4_vs_7_uses_draft_compute():
    name = "Inferact/Kimi-K3.1-Pro-DSpark"
    row = _profiles()[name]
    p = replica(EAGLE)
    target = "moonshotai/Kimi-K3.1-Pro"
    p.update(model_path=target, metadata=pinned_reference({}, {}, target, row["target_revisions"][target]))
    p.update(gpu_type="GB300", budget_bytes_per_gpu=[0, 6_000_000_000], complete_requests=3)
    rates = []
    for width in [4, 7]:
        p["speculation"] = {
            "draft_model": name,
            "revision": row["revision"],
            "draft_tokens": width,
            "active_requests": 2,
            "draft_bytes_per_token_per_gpu": 2560,
            "draft_cache_dtype": "bfloat16",
            "draft_weight_bytes_per_gpu": 620_000_000,
        }
        p["flags"] = {
            "speculative-config": json.dumps({"model": name, "num_speculative_tokens": width, "method": "dspark"})
        }
        r = estimate_throughput(
            [p], {"input_tokens": 1024, "output_tokens": 128, "concurrency": 4, "shared_prefix_percent": 0}
        )
        assert r["status"] == "estimated", r
        assert r["cache_scenarios"]["cold"]["effective_concurrency"] == 2
        assert "speculation" in r["assumptions"] and "draft_traffic_only" not in r["assumptions"]
        rates.append(r["rpm"])
    assert rates[0] != rates[1]


def test_verified_pvc_target_reaches_analytical_result():
    pool = replica(HY4)
    meta = pool["metadata"]
    pool.update(
        public_hub=False,
        model_path="/pvc/serving/model",
        metadata={**meta, **_profiles()[HY4]["geometry_proof"], "revision": None, "source": "mount"},
    )
    scenario = {"input_tokens": 1024, "output_tokens": 128, "concurrency": 4, "shared_prefix_percent": 0}
    assert estimate_throughput([pool], scenario)["status"] == "estimated"
    pool["metadata"]["weight_index_digest"] = "different"
    assert estimate_throughput([pool], scenario)["rpm"] is None


@pytest.mark.parametrize(
    "name",
    [
        "Qwen/Qwen3.8-Flash-Next",
        "Qwen/Qwen3.8-Flash-Next-FP8",
        "Inferact/Qwen3.8-Flash-Next-NVFP4",
        "nvidia/Qwen3.5-397B-A17B-NVFP4",
    ],
)
def test_qwen_integrated_mtp_uses_routed_head_and_per_rank_kv(name):
    pool = replica(name)
    plan = draft_plan(pool)
    assert plan["routed_bytes"] > 0 and plan["topk"] == 10 and plan["experts"] == 512
    assert plan["kv_heads"] == 2 and plan["row_bytes_per_rank"] == 1024
    result = estimate_throughput(
        [pool], {"input_tokens": 1024, "output_tokens": 128, "concurrency": 4, "shared_prefix_percent": 0}
    )
    assert result["status"] == "estimated" and "speculation" in result["assumptions"]


def test_sparse_qwen_replicated_compressed_keys_and_mrope_ring():
    pool = replica("Qwen/Qwen3.8-Flash-Next")
    bf16 = draft_plan(pool)
    pool["flags"]["attention-config.indexer_kv_dtype"] = "fp8"
    fp8 = draft_plan(pool)
    assert fp8["index_row_bytes"] == 128 and bf16["index_row_bytes"] == 256
    context = 16384
    positions = context + fp8["draft_tokens"] + 1
    main = positions * fp8["row_bytes_per_rank"]
    count = positions // 4
    required = main + count * 128 + 8 * 280
    pool.update(requests=1, required_bytes_per_gpu=1024, budget_bytes_per_gpu=[required + 1024] * 2)
    assert draft_adjusted_pool(pool, fp8, context, 4)["complete_requests"] == 1
    assert draft_adjusted_pool(pool, bf16, context, 4)["complete_requests"] == 0
    pool["flags"]["attention-config.indexer_kv_dtype"] = "nvfp4"
    with pytest.raises(DraftEvidenceError):
        draft_plan(pool)


def kimi_dcp_replica():
    from app.services.kimi_dcp_throughput import IMAGE

    target = "moonshotai/Kimi-K3"
    draft = "Inferact/Kimi-K3-DSpark"
    rev = _profiles()[draft]["target_revisions"][target]
    return {
        "gpu_type": "MI355X",
        "gpu_count": 8,
        "tp": 8,
        "dcp": 8,
        "pp": 1,
        "engine": "vllm",
        "role": "aggregated",
        "image": IMAGE,
        "public_hub": True,
        "model_path": target,
        "metadata": pinned_reference({}, {}, target, rev),
        "flags": {
            "attention-backend": "ROCM_AITER_MLA",
            "kv-cache-dtype": "fp8",
            "dcp-comm-backend": "a2a",
            "speculative-config": json.dumps(
                {
                    "model": draft,
                    "method": "dspark",
                    "num_speculative_tokens": 7,
                    "kv_cache_dtype": "fp8",
                    "attention_backend": "ROCM_AITER_MLA",
                }
            ),
        },
        "budget_bytes_per_gpu": [0, 64_092_459_392],
        "budget_source": "profile_upper_bound",
    }


def test_rocm_dcp_kimi_target_draft_state_budget_and_collectives():
    from app.services import kimi_dcp_throughput as k3

    pool = kimi_dcp_replica()
    plan = draft_plan(pool)
    layout = plan["dcp_layout"]
    expected_state = 69 * (12 * 128 * 128 * 4 + 3 * 12 * 128 * 10 * 2)
    assert k3.kv_bytes(layout, 8001) == 24 * 1001 * 576 + expected_state
    # Target and draft MLA caches use local positions; the recurrent state is
    # independent of context length. Full-width lookahead also owns local slots.
    context = 7994
    target_bytes = k3.kv_bytes(layout, context + 7)
    draft_bytes = 5 * 1001 * 576
    pool.update(budget_source="explicit", budget_bytes_per_gpu=[0, target_bytes + draft_bytes])
    assert draft_adjusted_pool(pool, plan, context, 1)["complete_requests"] == 1
    pool["budget_bytes_per_gpu"][1] -= 1
    assert draft_adjusted_pool(pool, plan, context, 1)["complete_requests"] == 0
    assert k3.exchange_time(layout, plan, 1, 3, 8, 7, False) > k3.exchange_time(layout, plan, 1, 3, 8, 7, True) > 0
    assert k3.prefill_transfer(layout, 1, 4096, 0, 8192, True) == 0
    assert k3.prefill_transfer(layout, 1, 16384, 0, 8192, True) > 0
    pool = kimi_dcp_replica()
    adjusted = draft_adjusted_pool(pool, plan, context, 8)
    replicated = layout["replicated_draft_bytes"]
    assert replicated == 2 * 7168 * (5 * 7168 + 5 * 576 + 5 * (1536 + 576))
    assert pool["budget_bytes_per_gpu"][1] - adjusted["budget_bytes_per_gpu"][1] > plan["draft_bytes"] / 8
    result = estimate_throughput(
        [pool], {"input_tokens": 100000, "output_tokens": 1000, "concurrency": 8, "shared_prefix_percent": 90}
    )
    assert result["status"] == "estimated", result
    assert set(result["assumptions"]) >= {"kv_unverified", "kimi_mla_dcp", "speculation"}
    assert k3.SOURCE in {s["url"] for s in result["sources"]}


@pytest.mark.parametrize(
    "change",
    [
        {"image": "vllm/vllm-openai-rocm:latest"},
        {"dcp": 4},
        {"tp": 4},
        {"gpu_type": "MI350X"},
        {"pp": 2},
        {"v2_runner": "0"},
        {"role": "decode"},
        {"gpu_count": 16},
    ],
)
def test_dcp_geometry_requires_the_verified_runner_and_placement(change):
    pool = {**kimi_dcp_replica(), **change}
    with pytest.raises(DraftEvidenceError) as err:
        draft_plan(pool)
    assert err.value.missing == "draft_placement"


def test_dcp_rejects_explicit_draft_override_on_older_rocm_image():
    pool = kimi_dcp_replica()
    opts = json.loads(pool["flags"]["speculative-config"])
    opts["draft_decode_context_parallel_size"] = 1
    pool["flags"]["speculative-config"] = json.dumps(opts)
    with pytest.raises(DraftEvidenceError):
        draft_plan(pool)
