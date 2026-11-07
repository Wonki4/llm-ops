"""P/D pools run vLLM as root with IPC_LOCK + SYS_RESOURCE (RDMA / NIXL), per pool changeable."""

import pytest

from app.services import pd_serving
from app.services.model_deployment_manifests import build_all, build_pd_deployments
from tests.test_deployment_reapply import _row
from tests.test_pd_serving import _dep, _extras, _with_extras

DEFAULT = {"runAsUser": 0, "capabilities": {"add": ["IPC_LOCK", "SYS_RESOURCE"]}}


def _vllm(deployment: dict) -> dict:
    return next(c for c in deployment["spec"]["template"]["spec"]["containers"] if c["name"] == "vllm")


def _pools(dep) -> dict:
    return {d["spec"]["selector"]["matchLabels"][pd_serving.LABEL_PD_ROLE]: d for d in build_pd_deployments(dep)}


def test_pd_pools_get_the_default_security_context():
    pools = _pools(_dep())  # no P/D settings at all (rows from before)
    assert _vllm(pools["prefill"])["securityContext"] == DEFAULT
    assert _vllm(pools["decode"])["securityContext"] == DEFAULT
    # the decode routing sidecar keeps its own, non-root context
    sidecar = pools["decode"]["spec"]["template"]["spec"]["initContainers"][0]
    assert sidecar["securityContext"] == {"allowPrivilegeEscalation": False, "runAsNonRoot": True}


def test_pool_settings_without_a_security_context_get_the_default():
    pools = _pools(_with_extras("prefill", _extras("prefill")))
    assert _vllm(pools["prefill"])["securityContext"] == DEFAULT


def test_pool_settings_change_or_clear_it():
    own = {"run_as_user": 1000, "capabilities": ["IPC_LOCK"]}
    custom = _pools(_with_extras("prefill", _extras("prefill", security_context=own)))
    assert _vllm(custom["prefill"])["securityContext"] == {"runAsUser": 1000, "capabilities": {"add": ["IPC_LOCK"]}}
    assert _vllm(custom["decode"])["securityContext"] == DEFAULT  # the other pool keeps the default
    cleared = _pools(_with_extras("decode", _extras("decode", security_context={"capabilities": []})))
    assert "securityContext" not in _vllm(cleared["decode"])


def test_a_privileged_pool_keeps_privileged():
    cfg = _dep().pd_config
    dep = _dep(pd_config={**cfg, "prefill": {**cfg["prefill"], "runtime": {"privileged": True}}})
    assert _vllm(_pools(dep)["prefill"])["securityContext"] == {"privileged": True, **DEFAULT}


def test_aggregated_serving_has_none():
    container = build_all(_row())[0]["spec"]["template"]["spec"]["containers"][0]
    assert "securityContext" not in container


def _validate(role: str, sc: dict | None) -> dict:
    extras = _extras(role) if sc is None else _extras(role, security_context=sc)
    return pd_serving.validate_pd_config({role: {"pd_extras": extras}}, engine="vllm", base_extra_args=None)


def test_validation_keeps_an_explicit_empty_context_and_drops_an_absent_one():
    cleared = _validate("prefill", {"run_as_user": None, "capabilities": []})
    assert cleared["prefill"]["pd_extras"]["security_context"] == {"capabilities": []}  # cleared stays cleared
    assert "security_context" not in _validate("decode", None)["decode"]["pd_extras"]  # absent = the default


@pytest.mark.parametrize(
    "sc", [{"run_as_user": -1}, {"capabilities": ["ipc_lock"]}, {"capabilities": ["IPC LOCK"]}, {"capabilities": [""]}]
)
def test_validation_rejects_bad_values(sc):
    with pytest.raises(ValueError, match="run_as_user|capability"):
        _validate("prefill", sc)


def test_capabilities_are_deduplicated():
    out = _validate("prefill", {"capabilities": ["IPC_LOCK", "IPC_LOCK", "SYS_RESOURCE"]})
    assert out["prefill"]["pd_extras"]["security_context"]["capabilities"] == ["IPC_LOCK", "SYS_RESOURCE"]
