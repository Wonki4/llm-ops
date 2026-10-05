"""GPU type profiles: schema, resolver, benchmark override path."""

import types

import pytest

from app.db.models.custom_gpu_profile import CustomGpuProfile
from app.db.models.custom_k8s_cluster import CustomK8sCluster
from app.db.models.custom_model_deployment import CustomModelDeployment
from app.db.models.custom_serving_recipe import CustomServingRecipe
from app.services.benchmark_serving import build_ephemeral_deployment
from app.services.gpu_profiles import (
    DEFAULT_GPU_RESOURCE_KEY,
    apply_profile,
    match_profile,
    merge_tolerations,
    resolve_placement,
    validate_profile_name,
)


def _profile(**kw):
    base = dict(
        name="a100-80g", label_key=None, label_value="a100-80g", gpu_resource_key="nvidia.com/gpu",
        tolerations=[{"key": "gpu", "operator": "Equal", "value": "a100", "effect": "NoSchedule"}], enabled=True,
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


# ─── schema ───────────────────────────────────────────────────────────────────


def test_schema_columns():
    assert CustomK8sCluster.__table__.columns["gpu_label_key"].server_default.arg == "gpu-type"
    for model in (CustomServingRecipe, CustomModelDeployment):
        assert model.__table__.columns["gpu_type"].nullable is True
    cols = CustomGpuProfile.__table__.columns
    assert cols["gpu_resource_key"].server_default.arg == "nvidia.com/gpu"
    uq = next(c for c in CustomGpuProfile.__table__.constraints if c.name == "uq_gpu_profile_cluster_name")
    assert [c.name for c in uq.columns] == ["cluster_id", "name"]
    assert uq.dialect_options["postgresql"]["nulls_not_distinct"] is True


# ─── names ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("ok", ["a100-80g", "h100", "l40s.mig-3g", "0abc"])
def test_valid_names(ok):
    assert validate_profile_name(f" {ok} ") == ok


@pytest.mark.parametrize("bad", ["", "A100", "-a100", "a 100", "a100_80g", "x" * 65])
def test_invalid_names(bad):
    with pytest.raises(ValueError):
        validate_profile_name(bad)


# ─── resolver ─────────────────────────────────────────────────────────────────


def test_no_profile_passes_explicit_values_through():
    p = resolve_placement(None, "gpu-type", node_selector={"zone": "a"}, tolerations=None, gpu_resource_key=None)
    assert p.node_selector == {"zone": "a"} and p.tolerations is None
    assert p.gpu_resource_key == DEFAULT_GPU_RESOURCE_KEY
    p = resolve_placement(None, "gpu-type", node_selector=None, tolerations=None, gpu_resource_key="amd.com/gpu")
    assert p.node_selector is None and p.gpu_resource_key == "amd.com/gpu"


def test_profile_wins_on_its_key_and_keeps_other_keys():
    p = resolve_placement(
        _profile(), "gpu-type",
        node_selector={"gpu-type": "old", "zone": "a"}, tolerations=None, gpu_resource_key="amd.com/gpu",
    )
    assert p.node_selector == {"gpu-type": "a100-80g", "zone": "a"}
    assert p.gpu_resource_key == "nvidia.com/gpu"
    assert p.tolerations == [{"key": "gpu", "operator": "Equal", "value": "a100", "effect": "NoSchedule"}]


def test_profile_label_key_override_and_cluster_default():
    p = resolve_placement(_profile(label_key="accelerator"), "gpu-type", node_selector=None, tolerations=None,
                          gpu_resource_key=None)
    assert p.node_selector == {"accelerator": "a100-80g"}
    p = resolve_placement(_profile(), None, node_selector=None, tolerations=None, gpu_resource_key=None)
    assert p.node_selector == {"gpu-type": "a100-80g"}  # library default when the cluster has none


def test_tolerations_union_dedup():
    a = {"key": "gpu", "operator": "Equal", "value": "a100", "effect": "NoSchedule"}
    b = {"key": "spot", "operator": "Exists"}
    assert merge_tolerations([a, b], [dict(a), {"key": "dedicated", "operator": "Exists", "effect": "NoExecute"}]) == [
        a, b, {"key": "dedicated", "operator": "Exists", "effect": "NoExecute"},
    ]
    assert merge_tolerations(None, None) is None


def test_apply_profile_mutates_row():
    dep = types.SimpleNamespace(node_selector=None, tolerations=[{"key": "spot", "operator": "Exists"}],
                                gpu_resource_key="nvidia.com/gpu")
    apply_profile(dep, _profile(gpu_resource_key="nvidia.com/mig-3g.40gb"), "gpu-type")
    assert dep.node_selector == {"gpu-type": "a100-80g"}
    assert dep.gpu_resource_key == "nvidia.com/mig-3g.40gb"
    assert [t["key"] for t in dep.tolerations] == ["spot", "gpu"]


def test_match_profile_from_node_selector():
    profiles = [
        _profile(name="a100-80g"),
        _profile(name="h100", label_value="h100"),
        _profile(name="off", label_value="x", enabled=False),
    ]
    assert match_profile(profiles, "gpu-type", {"gpu-type": "h100", "zone": "a"}).name == "h100"
    assert match_profile(profiles, "gpu-type", {"gpu-type": "x"}) is None  # disabled
    assert match_profile(profiles, "gpu-type", None) is None
    assert match_profile([_profile(label_key="acc", label_value="v")], "gpu-type", {"acc": "v"}).name == "a100-80g"


# ─── benchmark ephemeral serving ──────────────────────────────────────────────


def _base():
    return CustomModelDeployment(
        model_name="base", namespace="ns", image="img", replicas=1, gpu_count=1, gpu_resource_key="nvidia.com/gpu",
        model_path="/w/m", ingress_host="x", ingress_path="/", ingress_class="nginx", engine="vllm",
    )


def test_ephemeral_with_profile_resolves_placement():
    dep = build_ephemeral_deployment(
        _base(), name="bench-1", namespace="ns",
        overrides={"gpu_type": "a100-80g", "gpu_profile": _profile(), "gpu_label_key": "accelerator"},
    )
    assert dep.gpu_type == "a100-80g"
    assert dep.node_selector == {"accelerator": "a100-80g"}
    assert dep.tolerations[0]["value"] == "a100"


def test_ephemeral_legacy_string_keeps_label_fold():
    dep = build_ephemeral_deployment(_base(), name="bench-1", namespace="ns", overrides={"gpu_type": "h100"})
    assert dep.node_selector == {"gpu-type": "h100"} and dep.gpu_type == "h100"
    dep = build_ephemeral_deployment(
        _base(), name="bench-1", namespace="ns", overrides={"gpu_type": "h100", "gpu_label_key": "acc"}
    )
    assert dep.node_selector == {"acc": "h100"}
