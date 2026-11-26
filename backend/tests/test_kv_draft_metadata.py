from copy import deepcopy

import pytest

from app.services.kv_draft_metadata import draft_from_metadata, resolve_draft

KIMI_DRAFT = "Inferact/Kimi-K3.1-Pro-DSpark"
KIMI_REVISION = "50f5a10c9385fb9a225c534dda8f7a1f5ae87110"
OFFICIAL_RECIPE_REVISION = "e4e6e6261e9f8b6d5f249f9627eb6b109d762d5f"


@pytest.mark.parametrize("revision", [None, "main"])
def test_floating_public_recipe_discloses_reference_commit(revision):
    draft = resolve_draft(KIMI_DRAFT, revision)
    assert draft["assumed_revision"] and draft["revision"] == KIMI_REVISION
    assert draft["max_draft_tokens"] == 7 and draft["sample_from_anchor"] is True
    assert draft["weight_bytes"] == 2_479_976_577 * 2
    assert draft["sliding_window"] == 2_048
    assert draft["num_hidden_layers"] * draft["num_key_value_heads"] * draft["head_dim"] == 5 * 16 * 64


@pytest.mark.parametrize("sha", [KIMI_REVISION, OFFICIAL_RECIPE_REVISION])
def test_known_recipe_commit_is_immutable(sha):
    for revision in (sha, sha[:8]):
        draft = resolve_draft(KIMI_DRAFT, revision)
        assert draft["assumed_revision"] is False
        assert draft["revision"] == sha and sha in draft["source"]


def test_unknown_checkpoint_or_revision_has_no_public_fallback():
    for model, revision in (
        (KIMI_DRAFT, "f" * 40),
        (KIMI_DRAFT, "experimental"),
        (KIMI_DRAFT, ""),
        (KIMI_DRAFT + "-preview", KIMI_REVISION),
        ("/mnt/models/" + KIMI_DRAFT, None),
    ):
        assert resolve_draft(model, revision) is None


@pytest.fixture
def inspected():
    return {
        "source": "mount",
        "digest": "inspected-checkpoint",
        "weight_bytes": 9000,
        "draft_weight_bytes": 5000,
        "config": {
            "speculators_model_type": "dspark",
            "dtype": "float16",
            "block_size": 4,
            "sample_from_anchor": False,
            "transformer_layer_config": {
                "model_type": "qwen3",
                "num_hidden_layers": 2,
                "num_attention_heads": 8,
                "num_key_value_heads": 2,
                "head_dim": 64,
                "layer_types": ["sliding_attention", "sliding_attention"],
                "sliding_window": 1024,
            },
        },
    }


def test_inspected_draft_uses_real_dimensions_and_tensor_payload(inspected):
    draft = draft_from_metadata(inspected)
    assert draft["revision"] == "inspected-checkpoint" and not draft["assumed_revision"]
    assert draft["weight_bytes"] == 5000 and draft["sliding_window"] == 1024
    assert draft["max_draft_tokens"] == 3 and not draft["sample_from_anchor"]
    assert draft["dtype"] == "float16"


def test_unsupported_or_incomplete_draft_is_not_substituted(inspected):
    for key, value in (
        ("layer_types", ["sliding_attention", "full_attention"]),
        ("layer_types", ["sliding_attention"]),
        ("model_type", "mystery"),
        ("head_dim", True),
        ("num_key_value_heads", 3),
        ("use_sliding_window", False),
    ):
        other = deepcopy(inspected)
        other["config"]["transformer_layer_config"][key] = value
        assert draft_from_metadata(other) is None
    assert draft_from_metadata({**inspected, "weight_format": "awq"}) is None


def test_inspected_layout_survives_unknown_weight_payload(inspected):
    inspected.pop("draft_weight_bytes")
    draft = draft_from_metadata(inspected)
    assert draft["weight_bytes"] is None
    assert draft["num_hidden_layers"] == 2
    assert draft["num_key_value_heads"] == 2


@pytest.mark.parametrize("invalid", [True, "5000", 0, -5])
def test_malformed_draft_payload_is_rejected(inspected, invalid):
    assert draft_from_metadata({**inspected, "draft_weight_bytes": invalid}) is None
