"""Fixed load presets are the sweep methodology: stable keys, full expansion."""

import pytest

from app.services.benchmark_presets import LOAD_PRESETS, preset_params


def test_three_presets_with_expected_load_shapes():
    assert set(LOAD_PRESETS) == {"chat", "long_input", "long_output", "agentic"}
    assert LOAD_PRESETS["chat"] == {
        "random_input_len": 512,
        "random_output_len": 256,
        "num_prompts": 300,
        "max_concurrency": 32,
        "cache_ratio": 90,
        "name": "짧은 대화",
    }
    assert LOAD_PRESETS["long_input"] == {
        "random_input_len": 100_000,
        "random_output_len": 1_000,
        "num_prompts": 120,
        "max_concurrency": 8,
        "cache_ratio": 90,
        "name": "긴 입력",
    }
    assert LOAD_PRESETS["long_output"] == {
        "random_input_len": 256,
        "random_output_len": 1024,
        "num_prompts": 200,
        "max_concurrency": 16,
        "cache_ratio": 90,
        "name": "긴 출력",
    }


def test_preset_params_expands_common_fields_and_tags_key():
    p = preset_params("chat")
    assert p["seed"] == 0 and p["ignore_eos"] is False and p["preset"] == "chat"
    assert p["num_prompts"] == 300
    # expansion is a copy — mutating it must not touch the constant
    p["num_prompts"] = 1
    assert LOAD_PRESETS["chat"]["num_prompts"] == 300


def test_unknown_preset_raises():
    with pytest.raises(KeyError):
        preset_params("nope")


async def test_preset_save_reload_default_and_authorization(client_for_user, super_user, regular_user, mock_db):
    import json
    from unittest.mock import MagicMock

    from app.services.benchmark_presets import PresetSettings

    stored = None

    async def execute(sql, params):
        nonlocal stored
        result = MagicMock()
        if str(sql).startswith("INSERT"):
            stored = params["value"]
        else:
            result.scalar_one_or_none.return_value = stored
        return result

    mock_db.execute.side_effect = execute
    async with client_for_user(super_user) as client:
        data = (await client.get("/api/benchmarks/presets")).json()
        assert data["default_preset"] == "long_input"
        assert data["presets"]["long_input"]["cache_ratio"] == 90
        frozen = json.loads(json.dumps(data))
        data["presets"]["long_input"]["cache_ratio"] = 60
        data["presets"]["custom"] = {**data["presets"]["chat"], "name": "Custom"}
        data["default_preset"] = "custom"
        assert (await client.put("/api/benchmarks/presets", json=data)).status_code == 200
        assert (await client.get("/api/benchmarks/presets")).json() == data
        assert frozen["presets"]["long_input"]["cache_ratio"] == 90
        del data["presets"]["custom"]
        assert (await client.put("/api/benchmarks/presets", json=data)).status_code == 422
        data["default_preset"] = "chat"
        assert (await client.put("/api/benchmarks/presets", json=data)).status_code == 200
    async with client_for_user(regular_user) as client:
        assert (await client.put("/api/benchmarks/presets", json=data)).status_code == 403
        assert (await client.get("/api/benchmarks/presets")).status_code == 403
    assert PresetSettings.model_validate(data).default_preset == "chat"
