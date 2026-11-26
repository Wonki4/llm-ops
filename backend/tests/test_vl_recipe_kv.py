"""Saved GLM and Qwen vision-language recipes use the language decoder KV layout."""

import pytest

from app.services.kv_capacity import GIB, estimate


def test_glyph_b200_uses_the_text_tower_and_saved_budget():
    # zai-org/Glyph@074a0727f3c5fe18c02d303ffd8515ae65da12a7
    metadata = {
        "config": {
            "model_type": "glm4v",
            "vision_config": {"num_hidden_layers": 24, "num_attention_heads": 12},
            "text_config": {
                "model_type": "glm4v_text",
                "num_hidden_layers": 40,
                "num_attention_heads": 32,
                "num_key_value_heads": 2,
                "hidden_size": 4096,
                "dtype": "bfloat16",
                "max_position_embeddings": 131072,
            },
        },
        "weight_bytes": 20_585_554_944,
    }
    pool = estimate(metadata, "vllm", ["--tensor-parallel-size", "1"], vram_gb=180)
    assert not pool["missing"]
    assert pool["attention_bytes_per_token_per_gpu"] == 40 * 2 * 2 * 128 * 2 == 40_960
    assert pool["required_bytes_per_gpu"] == 1_342_177_280
    assert pool["budget_source"] == "profile_upper_bound"
    assert pool["capacity_tokens"] is None
    assert pool["capacity_range"] == [0, 3_891_952]
    assert pool["complete_requests_range"] == [0, 118]


@pytest.mark.parametrize("model_type", ["glm4_moe", "glm4v_moe_text"])
def test_glm4_moe_uses_declared_head_dim_and_replicated_kv_heads(model_type):
    # 4096 / 96 is not the head size. The selected decoder's explicit value is 128.
    cfg = {
        "model_type": model_type,
        "num_hidden_layers": 46,
        "num_attention_heads": 96,
        "num_key_value_heads": 8,
        "head_dim": 128,
        "hidden_size": 4096,
        "dtype": "bfloat16",
    }
    meta = {"config": {"text_config": cfg} if model_type == "glm4v_moe_text" else cfg}
    pool = estimate(meta, "vllm", ["--tensor-parallel-size=16", "--kv-cache-memory-bytes=8G"])
    width = 46 * 2 * 128 * 2
    assert pool["attention_bytes_per_token_per_gpu"] == width
    assert pool["capacity_tokens"] == (8 * GIB // (width * 16)) * 16


@pytest.mark.parametrize(
    ("model_type", "layers", "hidden", "heads", "kv_heads", "head_dim", "tp", "expected"),
    [
        ("qwen3_vl_text", 36, 2560, 32, 8, 128, 2, 73_728),  # Qwen3-VL-4B-Instruct
        ("qwen3_vl_moe_text", 94, 4096, 64, 4, 128, 8, 48_128),  # Qwen3-VL-235B-A22B-Instruct
        ("qwen2_5_vl", 80, 8192, 64, 8, None, 8, 40_960),  # Qwen2.5-VL-72B-Instruct
    ],
)
def test_qwen_vl_text_attention(model_type, layers, hidden, heads, kv_heads, head_dim, tp, expected):
    cfg = {
        "model_type": model_type,
        "dtype": "bfloat16",
        "num_hidden_layers": layers,
        "hidden_size": hidden,
        "num_attention_heads": heads,
        "num_key_value_heads": kv_heads,
        "head_dim": head_dim,
    }
    if model_type == "qwen2_5_vl":
        cfg.update(sliding_window=32768, use_sliding_window=False)
        wrapper = cfg  # This checkpoint stores decoder dimensions in the outer config.
    else:
        wrapper = {"text_config": cfg}
    result = estimate({"config": wrapper}, "vllm", [f"--tensor-parallel-size={tp}", "--kv-cache-memory-bytes=8G"])
    assert not result["missing"]
    assert result["attention_bytes_per_token_per_gpu"] == expected
    assert result["capacity_tokens"] == (8 * GIB // (expected * 16)) * 16
    assert result["components"][0]["window"] is None


def test_qwen2_5_vl_window_starts_after_full_layers():
    from app.services.kv_layout import layer_kinds

    cfg = {"model_type": "qwen2_5_vl_text", "num_hidden_layers": 3, "sliding_window": 128, "max_window_layers": 2}
    assert layer_kinds(cfg, False) == ["full_attention"] * 3
    assert layer_kinds({**cfg, "use_sliding_window": True}, False) == ["full_attention"] * 2 + ["sliding_attention"]


def test_new_decoder_rejects_tp_that_cannot_partition_query_heads():
    cfg = {
        "model_type": "glm4v_text",
        "num_hidden_layers": 40,
        "num_attention_heads": 32,
        "num_key_value_heads": 1,
        "head_dim": 128,
        "dtype": "bfloat16",
    }
    result = estimate({"config": cfg}, "vllm", ["--tensor-parallel-size=3", "--kv-cache-memory-bytes=8G"])
    assert result["capacity_tokens"] is None and result["missing"] == ["uneven sharding"]
