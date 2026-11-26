"""Check the saved Kimi B300 launch and air-gapped draft resolution through recipe sizing."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.kv_capacity import Scenario, estimate
from app.services.kv_metadata import pinned_reference
from app.services.kv_metadata_reader import inspect
from app.services.recipe_capacity import _with_draft_metadata

KIMI = "moonshotai/Kimi-K3.1-Pro"
TARGET_REVISION = "d9e99b0792a5e93b78ea9bea62a68167e36ddd9e"
DRAFT = "Inferact/Kimi-K3.1-Pro-DSpark"


def kimi_flags(k):
    spec = {"model": DRAFT, "method": "dspark", "num_speculative_tokens": k,
            "draft_sample_method": "probabilistic", "rejection_sample_method": "block"}
    return [
        "--tensor-parallel-size=8", "--decode-context-parallel-size=8", "--enable-expert-parallel",
        "--max-num-seqs=64", "--max-num-batched-tokens=16384", "--gpu-memory-utilization=0.97",
        "--load-format=fastsafetensors", "--kv-cache-dtype=fp8", '--attention-config={"indexer_kv_dtype":"mxfp4"}',
        "--use-replayssm", "--mamba-ssm-cache-dtype=float32", "--speculative-config", json.dumps(spec),
    ]


def size(k, requests=64, context=32768, extra=(), observed=None):
    target = pinned_reference({}, {}, KIMI, TARGET_REVISION)
    return estimate(target, "vllm", [*kimi_flags(k), *extra], vram_gb=288, observed_budget=observed,
                    scenario=Scenario(context, requests))


def test_kimi_four_and_seven_use_same_weights_and_different_candidate_counts():
    four, seven = size(4), size(7)
    for k, out in [(4, four), (7, seven)]:
        draft = out["speculation"]
        assert out["missing"] == []
        assert draft["draft_tokens"] == k
        assert draft["active_requests"] == 64
        assert draft["candidate_tokens"] == 64 * k
        assert draft["lookahead_tokens_per_request"] == k
        assert draft["draft_window_tokens"] == 2048
        assert draft["draft_weight_bytes_per_gpu"] > 0 and draft["draft_kv_bytes_per_gpu"] > 0
        assert out["capacity_tokens"] is None and out["capacity_range"][1] > 0
        assert out["complete_requests_range"][1] <= 64
        # Stronger than checking the presence of a field: charge weights against
        # the verified target-only memory ceiling and the draft KV against the pool.
        # 69 KDA layers, 64 configured slots, 12 heads/rank, 128 values per head.
        # Each verify position holds FP32 correction and two BF16 key/gate vectors.
        workspace = 69 * 64 * (k + 1) * 12 * 128 * (4 + 2 * 2)
        assert draft["verification_workspace_bytes_per_gpu"] == workspace
        assert out["budget_bytes_per_gpu"][1] == 84_207_827_840 - draft["draft_weight_bytes_per_gpu"] - workspace
        assert out["required_bytes_per_gpu"] > draft["draft_kv_bytes_per_gpu"]
    assert four["speculation"]["draft_weight_bytes_per_gpu"] == seven["speculation"]["draft_weight_bytes_per_gpu"]
    assert four["budget_bytes_per_gpu"][1] > seven["budget_bytes_per_gpu"][1]
    assert seven["capacity_range"][1] <= four["capacity_range"][1]
    assert seven["required_bytes_per_gpu"] >= four["required_bytes_per_gpu"]


def test_kimi_active_decode_count_clamps_to_the_recipe_limit():
    single, full, queued = size(4, 1), size(4, 64), size(4, 96)
    assert single["speculation"]["candidate_tokens"] == 4
    assert full["speculation"]["candidate_tokens"] == queued["speculation"]["candidate_tokens"] == 256
    assert full["speculation"]["draft_kv_bytes_per_gpu"] == single["speculation"]["draft_kv_bytes_per_gpu"] * 64
    assert full["capacity_range"][1] <= single["capacity_range"][1]


@pytest.mark.parametrize("extra,observed,expected", [
    (["--kv-cache-memory-bytes=20G"], None, 20 << 30),
    ([], 19 << 30, 19 << 30),
    ([], 0, 0),
])
def test_measured_or_configured_kv_pool_does_not_charge_loaded_draft_weights_again(extra, observed, expected):
    result = size(4, extra=extra, observed=observed)
    assert result["budget_bytes_per_gpu"] == [expected, expected]
    assert result["capacity_range"][0] == 0
    if not expected:
        assert result["capacity_range"] == [0, 0]


async def test_reference_reuse_respects_the_recipe_storage_and_hub():
    target = {"config": {}, "revision": TARGET_REVISION}
    draft = {"method": "dspark", "model": DRAFT, "num_speculative_tokens": 4}
    with patch("app.services.recipe_capacity.metadata_for", AsyncMock(return_value={"error": "offline"})) as read:
        public = await _with_draft_metadata(None, SimpleNamespace(), {}, {}, target, draft, False)
        assert public["resolved_draft"]["assumed_revision"] and read.await_count == 0
        for pod, container in [
            ({}, {"env": [{"name": "HF_ENDPOINT", "value": "https://mirror.invalid"}]}),
            ({"volumes": [{"name": "weights", "persistentVolumeClaim": {"claimName": "models"}}]},
             {"volumeMounts": [{"name": "weights", "mountPath": "/weights"}]}),
        ]:
            result = await _with_draft_metadata(None, SimpleNamespace(), pod, container, target, draft, False)
            assert result["resolved_draft"] is None and result["draft_error"] == "offline"
            read.assert_awaited_with(None, SimpleNamespace(), pod, container, DRAFT, None, start=False)


def test_local_draft_metadata_reads_tensor_directory_and_excludes_shared_weights(tmp_path):
    cfg = {"speculators_model_type": "dspark", "sample_from_anchor": True, "block_size": 7,
           "transformer_layer_config": {"model_type": "qwen3", "sliding_window": 2048}}
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    header = json.dumps({
        "embed_tokens.weight": {"dtype": "BF16", "shape": [8], "data_offsets": [0, 16]},
        "layers.0.self_attn.k_proj.weight": {"dtype": "BF16", "shape": [16], "data_offsets": [16, 48]},
    }).encode()
    (tmp_path / "model.safetensors").write_bytes(len(header).to_bytes(8, "little") + header + bytes(48))
    local = inspect(str(tmp_path), None)
    assert local["draft_weight_bytes"] == 32 and local["config"]["sample_from_anchor"] is True
    assert local["config"]["transformer_layer_config"]["sliding_window"] == 2048


def test_changing_draft_width_can_cross_a_physical_target_block():
    # A 32,763-token committed context plus 4 or 7 positions lands on
    # opposite sides of the 32,768-token boundary in this TP8/DCP8 recipe.
    four, seven = size(4, context=32763), size(7, context=32763)
    assert four["block_tokens"] == seven["block_tokens"] == 16384
    assert four["required_bytes_per_gpu"] < seven["required_bytes_per_gpu"]


@pytest.mark.parametrize("k", [0, 8, True])
def test_bad_draft_width_never_returns_capacity(k):
    result = size(k)
    assert result["missing"] and "capacity_range" not in result


def test_local_draft_metadata_uses_index_tensor_sizes_when_available(tmp_path):
    cfg = {"speculators_model_type": "dspark", "dtype": "bfloat16"}
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    index = {"metadata": {"total_size": 4096}, "weight_map": {"layers.0.self_attn.k_proj.weight": "weights"}}
    (tmp_path / "model.safetensors.index.json").write_text(json.dumps(index))
    assert inspect(str(tmp_path), None)["draft_weight_bytes"] == 4096
    index["weight_map"]["lm_head.weight"] = "weights"
    (tmp_path / "model.safetensors.index.json").write_text(json.dumps(index))
    assert inspect(str(tmp_path), None)["draft_weight_bytes"] is None
