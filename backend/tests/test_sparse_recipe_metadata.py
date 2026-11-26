"""Preserve the sparse indexer's actual layer schedule in a recipe metadata snapshot."""

import pytest

from app.services.kv_metadata import pinned_reference
from app.services.kv_metadata_reader import structural


def test_retain_explicit_sparse_index_schedule_in_nested_text_config():
    text = {
        "model_type": "glm_moe_dsa",
        "index_topk_freq": 4,
        "index_skip_topk_offset": 3,
        "index_topk_pattern": [1, 0, 0, 0],
        "indexer_types": ["full", "shared", "shared", "shared"],
    }
    result = structural({"model_type": "wrapper", "text_config": {**text, "private_field": "ignored"}})
    assert result["text_config"] == text
    assert "index_topk_freq" not in result


@pytest.mark.parametrize("model_type", ["glm_moe_dsa", "deepseek_v32"])
def test_collecting_a_complete_sparse_config_records_the_every_layer_default(model_type):
    assert structural({"model_type": model_type})["index_topk_freq"] == 1
    assert "index_topk_freq" not in structural({"model_type": "glm4v_text"})


def test_bundled_glyph_uses_the_pinned_decoder_dimensions():
    data = pinned_reference({}, {}, "zai-org/Glyph", "074a0727f3c5fe18c02d303ffd8515ae65da12a7")
    assert data["config_digest"] == "a4d14da68c5485a469d199a6bb65a5a85bfe23f6dccc8e0c4ab553529a63c426"
    text = data["config"]["text_config"]
    assert (text["model_type"], text["num_hidden_layers"], text["num_key_value_heads"]) == ("glm4v_text", 40, 2)
    assert data["weight_bytes"] == 20_585_554_944


@pytest.mark.parametrize(
    ("model", "revision"),
    [
        ("zai-org/GLM-4.5V-FP8", "3ca028eac7af91c53109dcfa865e5c8da7b1faf3"),
        ("zai-org/GLM-4.6V-FP8", "33172e26eb88482cf3d0a36fced01d05454734ec"),
    ],
)
def test_fp8_weight_budget_uses_the_packed_shards_despite_an_incorrect_index(model, revision):
    data = pinned_reference({}, {}, model, revision)
    assert data["weight_index_declared_bytes"] == 83_858_880
    assert data["weight_bytes"] == data["indexed_shard_file_bytes"] - data["indexed_shard_header_bytes"]
    assert data["weight_bytes"] == 109_999_771_648
