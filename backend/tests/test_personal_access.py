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


@pytest.mark.parametrize(
    "models, expected",
    [
        (None, ["reasoning"]),
        ([], ["reasoning"]),  # LiteLLM's "everything" is "never granted" to the portal
        (["no-default-models"], ["reasoning"]),
        (["gpt-4o"], ["gpt-4o", "reasoning"]),
        (["all-proxy-models"], ["all-proxy-models", "reasoning"]),  # kept so the group survives a later revoke
        (["gpt-4o", "reasoning"], None),  # already there: nothing to write
    ],
)
def test_add_grant_entry(models, expected):
    assert pa.add_grant_entry(models, "reasoning") == expected


@pytest.mark.parametrize(
    "models, expected",
    [
        (["gpt-4o", "reasoning"], ["gpt-4o"]),
        (["reasoning"], ["no-default-models"]),  # nothing left: an explicit none, never []
        (["all-proxy-models", "reasoning"], ["all-proxy-models"]),
        (["gpt-4o"], None),  # not there: nothing to write
        (None, None),
    ],
)
def test_remove_grant_entry(models, expected):
    assert pa.remove_grant_entry(models, "reasoning") == expected


@pytest.mark.parametrize("raw, expected", [("user001", "USER001"), ("  a.b-c_d@corp ", "A.B-C_D@CORP")])
def test_normalize_user_id(raw, expected):
    assert pa.normalize_user_id(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "홍길동", "user 001", "-user", "x" * 129])
def test_normalize_user_id_rejects(raw):
    assert pa.normalize_user_id(raw) is None


@pytest.mark.parametrize("raw", ["reasoning", "beta-models", "oss.v2_test"])
def test_validate_group_name_accepts(raw):
    assert pa.validate_group_name(raw, model_names={"glm"}) == raw


@pytest.mark.parametrize(
    "raw, match",
    [
        ("", "group name"),
        ("has space", "group name"),
        ("그룹", "group name"),
        ("glm", "model"),  # a model of that name: the grant would be ambiguous
        ("all-proxy-models", "reserved"),
        ("no-default-models", "reserved"),
    ],
)
def test_validate_group_name_rejects(raw, match):
    with pytest.raises(ValueError, match=match):
        pa.validate_group_name(raw, model_names={"glm"})
