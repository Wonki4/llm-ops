"""Fixed load presets are the sweep methodology: stable keys, full expansion."""

import pytest

from app.services.benchmark_presets import LOAD_PRESETS, preset_params


def test_three_presets_with_expected_load_shapes():
    assert set(LOAD_PRESETS) == {"chat", "long_input", "long_output"}
    assert LOAD_PRESETS["chat"] == {
        "random_input_len": 512, "random_output_len": 256,
        "num_prompts": 300, "max_concurrency": 32,
    }
    assert LOAD_PRESETS["long_input"] == {
        "random_input_len": 4096, "random_output_len": 512,
        "num_prompts": 120, "max_concurrency": 8,
    }
    assert LOAD_PRESETS["long_output"] == {
        "random_input_len": 256, "random_output_len": 1024,
        "num_prompts": 200, "max_concurrency": 16,
    }


def test_preset_params_expands_common_fields_and_tags_key():
    p = preset_params("chat")
    assert p["seed"] == 0 and p["ignore_eos"] is True and p["preset"] == "chat"
    assert p["num_prompts"] == 300
    # expansion is a copy — mutating it must not touch the constant
    p["num_prompts"] = 1
    assert LOAD_PRESETS["chat"]["num_prompts"] == 300


def test_unknown_preset_raises():
    with pytest.raises(KeyError):
        preset_params("nope")
