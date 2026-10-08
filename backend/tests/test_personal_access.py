"""Per-user personal-key grant helpers (pure)."""

import pytest

from app.services import personal_access as pa


@pytest.mark.parametrize(
    "models, state",
    [
        (None, "none"),
        ([], "none"),
        (["no-default-models"], "none"),
        (["all-proxy-models"], "all"),
        (["gpt-4o"], "custom"),
        (["beta-models", "gpt-4o"], "custom"),
    ],
)
def test_grant_state(models, state):
    assert pa.grant_state(models) == state
    assert pa.has_grant(models) is (state != "none")


def test_models_for_each_state():
    assert pa.models_for("none") == ["no-default-models"]
    assert pa.models_for("all") == ["all-proxy-models"]
    assert pa.models_for("custom", [" gpt-4o ", "beta-models", "gpt-4o"]) == ["gpt-4o", "beta-models"]
    with pytest.raises(ValueError):
        pa.models_for("custom", [])


def test_validate_selection_names_models_or_groups():
    pa.validate_selection(["gpt-4o", "beta-models"], model_names={"gpt-4o"}, access_groups={"beta-models"})
    with pytest.raises(ValueError, match="nope"):
        pa.validate_selection(["gpt-4o", "nope"], model_names={"gpt-4o"}, access_groups=set())


def test_key_models_must_fall_inside_the_grant():
    groups = {"beta-models": ["glm", "qwen"]}
    assert pa.key_models_allowed(["glm", "beta-models"], ["beta-models"], access_groups=groups) == []
    assert pa.key_models_allowed(["gpt-4o"], ["beta-models"], access_groups=groups) == ["gpt-4o"]
    assert pa.key_models_allowed(["anything"], ["all-proxy-models"], access_groups=groups) == []
    assert pa.key_models_allowed(["glm"], [], access_groups=groups) == ["glm"]  # no grant → nothing allowed
    assert pa.key_models_allowed(None, ["no-default-models"], access_groups=groups) == []


def test_access_groups_from_model_info():
    rows = [
        {"model_name": "glm", "model_info": {"access_groups": ["beta-models"]}},
        {"model_name": "qwen", "model_info": {"access_groups": ["beta-models", "oss"]}},
        {"model_name": "gpt-4o", "model_info": {}},
    ]
    assert pa.access_groups_from_model_info(rows) == {"beta-models": ["glm", "qwen"], "oss": ["qwen"]}
