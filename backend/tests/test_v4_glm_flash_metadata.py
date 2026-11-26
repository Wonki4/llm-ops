"""Checkpoint evidence for DeepSeek-V4/V4.1 and the GLM-5.3-Flash text backbone."""

import json
from collections import Counter

import pytest

from app.services.kv_metadata import pinned_reference
from app.services.kv_metadata_reader import inspect, optional_weight_bytes, structural


def ref(model, rev):
    return pinned_reference({}, {}, model, rev)


@pytest.mark.parametrize(("model", "revision"), [
    ("deepseek-ai/DeepSeek-V4.1-Flash", "314113f8a7dadb388215396ba5fbee2c610a04d6"),
    ("deepseek-ai/DeepSeek-V4.1-Flash-1017", "08c8657f02291e11b6f6b63e0e16014f8e748518"),
])
def test_v41_shared_sources_and_host_offloadable_table_bytes(model, revision):
    meta = ref(model, revision)
    cfg = meta["config"]["text_config"]
    assert cfg["kv_source_layer_ids"] == [2, 8, 14, 20]
    assert cfg["index_source_layer_ids"] == [2, 8, 14, 20, 24, 28, 32, 36]
    assert cfg["num_hidden_layers"] + cfg["num_nextn_predict_layers"] == len(cfg["compress_ratios"])
    assert cfg["dspark_block_size"] == 5
    assert cfg["candidate_block_size"] == 8
    assert cfg["candidate_source_layer_id"] == 20
    assert meta["config"]["quantization_config"] == {"expert_dtype": "fp4", "quant_method": "fp8"}
    assert (cfg["hidden_size"], cfg["moe_intermediate_size"], cfg["n_routed_experts"]) == (5120, 2304, 384)
    # Each stored FP8 embed row has 256 value and eight one-byte scale entries.
    assert meta["weight_components"]["engram_embeddings"] == sum(cfg["engram_num_embeddings"]) * (256 + 8)
    assert meta["weight_bytes"] - sum(meta["weight_components"].values()) == 298_624_609_728


def test_v41_expert_metadata_projection_selects_declared_dimensions_and_packing():
    raw = {"model_type": "deepseek_v41", "quantization_config": {
        "quant_method": "fp8", "expert_dtype": "fp4", "modules_to_not_convert": ["many entries"]},
        "text_config": {"model_type": "deepseek_v41_text", "n_routed_experts": 384, "moe_intermediate_size": 2304}}
    projected = structural(raw)
    assert projected["quantization_config"] == {"quant_method": "fp8", "expert_dtype": "fp4"}
    assert projected["text_config"] == raw["text_config"]
    assert structural({"model_type": "deepseek_v41", "quantization_config": {"expert_dtype": ["fp4"]}}) == {
        "model_type": "deepseek_v41",
    }


def test_glm_flash_has_a_compressed_index_and_zero_based_kda_layers():
    meta = ref("zai-org/GLM-5.3-Flash", "46a050bec13002ad29ee795c16e61cf4fa3c4a9c")
    cfg = meta["config"]["text_config"]
    assert Counter(cfg["layer_types"]) == {"linear_attention": 34, "deepseek_sparse_attention": 11}
    assert cfg["linear_attn_config"]["kda_layers"][0] == 0
    assert cfg["index_kpool"] == 4 and cfg["index_kpool_compress"] and cfg["mla_use_nope"]
    assert meta["weight_components"] == {"engram_embeddings": 0, "draft": 7_493_399_168, "vision": 1_127_254_016}


def test_v4_plain_and_repacked_checkpoints_keep_their_own_nextn_evidence():
    base = ref("deepseek-ai/DeepSeek-V4-Flash", "60d8d70770c6776ff598c94bb586a859a38244f1")
    repacked = ref("RedHatAI/DeepSeek-V4-Flash-NVFP4-FP8", "b2199fd8c47ef2e54be738c67c26949c9823aca1")
    cfg = base["config"]
    assert len(cfg["compress_ratios"]) == cfg["num_hidden_layers"] + 1
    assert cfg["compress_ratios"][:5] == [0, 0, 4, 128, 4]
    assert base["weight_components"]["draft"] == 3_593_787_756
    assert repacked["weight_components"]["draft"] == 0


def test_mounted_v41_metadata_reads_only_offsets_for_selectable_weights(tmp_path):
    cfg = {"model_type": "deepseek_v41", "text_config": {
        "model_type": "deepseek_v41_text", "num_hidden_layers": 40, "compress_ratios": [0],
        "engram_layer_ids": [1], "kv_source_layer_ids": [2],
    }}
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    grouped = [
        ("layers.1.engram.embed.weight", 16), ("layers.1.engram.embed.scale", 2),
        ("layers.1.engram.q_weight", 7), ("mtp.0.attn.weight", 10), ("vision.blocks.0.weight", 8),
        ("aligner.proj.weight", 4),
    ]
    directory = {}
    total = 0
    for name, size in grouped:
        directory[name] = {"data_offsets": [total, total + size]}
        total += size
    weight_map = {name: "group.safetensors" for name, _ in grouped}
    weight_map["layers.0.attn.weight"] = "backbone-shard-not-needed.safetensors"
    index = {"metadata": {"total_size": total + 100}, "weight_map": weight_map}
    (tmp_path / "model.safetensors.index.json").write_text(json.dumps(index))
    header = json.dumps(directory).encode()
    (tmp_path / "group.safetensors").write_bytes(len(header).to_bytes(8, "little") + header + bytes(total))
    result = inspect(str(tmp_path), None)
    assert result["config"] == structural(cfg)
    assert result["weight_components"] == {"engram_embeddings": 18, "draft": 10, "vision": 12}
    assert result["weight_bytes"] == total + 100
    assert optional_weight_bytes(tmp_path, cfg, {"weight_map": {"mtp.0.weight": "../escaped.safetensors"}}) is None
    assert optional_weight_bytes(tmp_path, cfg, {"weight_map": {"mtp.0.weight": "absent.safetensors"}}) is None
    assert optional_weight_bytes(tmp_path, cfg, {"weight_map": {"mtp.0.unknown": "group.safetensors"}}) is None
