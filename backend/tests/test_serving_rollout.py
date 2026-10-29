"""Rollout / graceful-termination settings: schema, defaults, manifest rendering, guard."""

import types

import pytest

from app.services import serving_rollout as ro
from app.services.pd_serving import validate_runtime
from tests.test_pd_serving import _dep as _pd_dep


def _dep(**kw):
    base = dict(
        id="00000000-0000-0000-0000-000000000001", model_name="m", namespace="ns", image="img", replicas=1,
        gpu_count=4, gpu_resource_key="nvidia.com/gpu", cpu_request=None, cpu_limit=None, memory_request=None,
        memory_limit=None, node_selector=None, tolerations=None, storages=[], model_path="/w/m",
        vllm_extra_args=None, env=None, engine="vllm", engine_args=None, probes=None, runtime=None,
        serving_mode="aggregated", pd_config=None, ingress_host="x", ingress_path="/", ingress_class="nginx",
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


# ─── schema / helpers ─────────────────────────────────────────────────────────


def test_defaults_when_runtime_is_empty():
    assert ro.mode(None) == "surge"
    assert ro.strategy(None) == {"type": "RollingUpdate", "rollingUpdate": {"maxSurge": 1, "maxUnavailable": 0}}
    assert ro.termination_grace_seconds(None) == 120
    assert ro.lifecycle(None) == {"preStop": {"exec": {"command": ["sh", "-c", "sleep 10"]}}}
    assert ro.lifecycle({}) == ro.lifecycle(None)


def test_replace_strategy_and_overrides():
    assert ro.strategy({"rollout": "replace"}) == {
        "type": "RollingUpdate",
        "rollingUpdate": {"maxSurge": 0, "maxUnavailable": 1},
    }
    assert ro.termination_grace_seconds({"termination_grace_seconds": 300}) == 300
    assert ro.lifecycle({"prestop_sleep_seconds": 25})["preStop"]["exec"]["command"] == ["sh", "-c", "sleep 25"]
    assert ro.lifecycle({"prestop_sleep_seconds": 0}) is None


def test_validate_runtime_accepts_rollout_fields():
    value = {"rollout": "replace", "termination_grace_seconds": 60, "prestop_sleep_seconds": 0}
    assert validate_runtime(value) == value
    assert validate_runtime({"rollout": "surge"}) == {"rollout": "surge"}


@pytest.mark.parametrize(
    "bad",
    [
        {"rollout": "recreate"},
        {"termination_grace_seconds": 0},
        {"termination_grace_seconds": 3601},
        {"prestop_sleep_seconds": -1},
        {"prestop_sleep_seconds": 601},
    ],
)
def test_validate_runtime_rejects_bad_rollout_fields(bad):
    with pytest.raises(ValueError):
        validate_runtime(bad)


def test_check_rollout_blocks_replace_with_one_replica():
    with pytest.raises(ValueError, match="replicas: rollout 'replace' needs at least 2 replicas"):
        ro.check_rollout(_dep(replicas=1, runtime={"rollout": "replace"}))
    ro.check_rollout(_dep(replicas=2, runtime={"rollout": "replace"}))
    ro.check_rollout(_dep(replicas=0, runtime={"rollout": "replace"}))
    ro.check_rollout(_dep(replicas=1))
    ro.check_rollout(_dep(replicas=1, runtime={"rollout": "surge"}))


def test_check_rollout_pd_checks_each_pool():
    dep = _pd_dep(runtime={"rollout": "replace"})  # prefill replicas 2, decode replicas 1
    with pytest.raises(ValueError, match="decode: rollout 'replace'"):
        ro.check_rollout(dep)
    dep = _pd_dep(runtime={"rollout": "replace"})
    dep.pd_config["decode"]["replicas"] = 2
    ro.check_rollout(dep)


def test_surge_gpus():
    assert ro.surge_gpus(_dep(gpu_count=4)) == 4
    assert ro.surge_gpus(_dep(gpu_count=4, runtime={"rollout": "replace"})) == 0
    assert ro.surge_gpus(_pd_dep()) == 16  # prefill 8 + decode 8
    dep = _pd_dep()
    dep.pd_config["decode"]["runtime"] = {"rollout": "replace"}
    assert ro.surge_gpus(dep) == 8


# ─── manifest rendering ───────────────────────────────────────────────────────


def test_build_deployment_renders_rollout_defaults():
    from app.services.model_deployment_manifests import build_deployment

    m = build_deployment(_dep())
    assert m["spec"]["strategy"] == {"type": "RollingUpdate", "rollingUpdate": {"maxSurge": 1, "maxUnavailable": 0}}
    pod = m["spec"]["template"]["spec"]
    assert pod["terminationGracePeriodSeconds"] == 120
    assert pod["containers"][0]["lifecycle"] == {"preStop": {"exec": {"command": ["sh", "-c", "sleep 10"]}}}


def test_build_deployment_renders_runtime_rollout_overrides():
    from app.services.model_deployment_manifests import build_deployment

    runtime = {"rollout": "replace", "termination_grace_seconds": 45, "prestop_sleep_seconds": 0}
    m = build_deployment(_dep(replicas=2, runtime=runtime))
    assert m["spec"]["strategy"]["rollingUpdate"] == {"maxSurge": 0, "maxUnavailable": 1}
    pod = m["spec"]["template"]["spec"]
    assert pod["terminationGracePeriodSeconds"] == 45
    assert "lifecycle" not in pod["containers"][0]


def test_pd_pool_runtime_overrides_rollout():
    from app.services.model_deployment_manifests import build_pd_deployments

    dep = _pd_dep(runtime={"termination_grace_seconds": 200})
    dep.pd_config["decode"]["replicas"] = 2
    dep.pd_config["decode"]["runtime"] = {"rollout": "replace"}
    prefill, decode = build_pd_deployments(dep)
    assert prefill["spec"]["strategy"]["rollingUpdate"] == {"maxSurge": 1, "maxUnavailable": 0}
    assert decode["spec"]["strategy"]["rollingUpdate"] == {"maxSurge": 0, "maxUnavailable": 1}
    # A pool's runtime replaces the base's (pool_row semantics): prefill inherits the
    # 200 s grace, decode set its own runtime and so gets the default grace.
    assert prefill["spec"]["template"]["spec"]["terminationGracePeriodSeconds"] == 200
    assert decode["spec"]["template"]["spec"]["terminationGracePeriodSeconds"] == 120
    for m in (prefill, decode):
        pod = m["spec"]["template"]["spec"]
        assert pod["containers"][0]["lifecycle"]["preStop"]["exec"]["command"] == ["sh", "-c", "sleep 10"]
    # the decode init sidecar gets no preStop hook
    assert "lifecycle" not in decode["spec"]["template"]["spec"]["initContainers"][0]
