"""Readiness / liveness probe settings: validation, defaults, manifest rendering, import."""

import types

import pytest

from app.services.model_deployment_manifests import build_deployment
from app.services.recipe_import import build_recipe_draft
from app.services.serving_probes import (
    LIVENESS_DEFAULTS,
    READINESS_DEFAULTS,
    effective_probes,
    probe_from_k8s,
    render_probes,
    validate_probes,
)


def _dep(**kw):
    base = dict(
        id="00000000-0000-0000-0000-000000000001", model_name="m", namespace="ns", image="img", replicas=1,
        gpu_count=0, gpu_resource_key="nvidia.com/gpu", cpu_request=None, cpu_limit=None, memory_request=None,
        memory_limit=None, node_selector=None, tolerations=None, pvc_name=None, pvc_mount_path=None,
        model_path="/w/m", vllm_extra_args=None, env=None, engine="vllm", engine_args=None, probes=None,
        ingress_host="x", ingress_path="/", ingress_class="nginx",
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


# ─── validation / normalisation ───────────────────────────────────────────────


def test_validate_none_and_empty():
    assert validate_probes(None) is None
    assert validate_probes({}) is None
    assert validate_probes({"readiness": None, "liveness": None}) is None


def test_validate_drops_unset_fields_and_keeps_liveness_opt_in():
    out = validate_probes({"readiness": {"initial_delay_seconds": 5, "path": None}, "liveness": {}})
    assert out == {"readiness": {"initial_delay_seconds": 5}, "liveness": {}}


@pytest.mark.parametrize(
    "bad",
    [
        {"readiness": {"path": "health"}},
        {"readiness": {"path": "/he alth"}},
        {"readiness": {"period_seconds": 0}},
        {"liveness": {"timeout_seconds": 601}},
        {"readiness": {"failure_threshold": -1}},
        {"readiness": {"initial_delay_seconds": "soon"}},
    ],
)
def test_validate_rejects_bad_values(bad):
    with pytest.raises(ValueError):
        validate_probes(bad)


# ─── defaults ─────────────────────────────────────────────────────────────────


def test_effective_probes_defaults_and_merge():
    eff = effective_probes(None)
    assert eff == {"readiness": READINESS_DEFAULTS, "liveness": None}
    eff = effective_probes({"readiness": {"initial_delay_seconds": 5}, "liveness": {"path": "/live"}})
    assert eff["readiness"] == {**READINESS_DEFAULTS, "initial_delay_seconds": 5}
    assert eff["liveness"] == {**LIVENESS_DEFAULTS, "path": "/live"}


# ─── rendering ────────────────────────────────────────────────────────────────


def test_render_default_matches_the_historical_hardcoded_probe():
    out = render_probes(_dep())
    assert out == {
        "readinessProbe": {
            "httpGet": {"path": "/health", "port": 8000},
            "initialDelaySeconds": 60,
            "periodSeconds": 10,
            "timeoutSeconds": 5,
            "failureThreshold": 30,
        }
    }
    assert "livenessProbe" not in out


def test_render_custom_readiness_and_liveness():
    dep = _dep(
        probes={"readiness": {"initial_delay_seconds": 5, "period_seconds": 5}, "liveness": {"failure_threshold": 6}}
    )
    out = render_probes(dep)
    assert out["readinessProbe"]["initialDelaySeconds"] == 5
    assert out["readinessProbe"]["periodSeconds"] == 5
    assert out["readinessProbe"]["failureThreshold"] == 30  # untouched default
    assert out["livenessProbe"] == {
        "httpGet": {"path": "/health", "port": 8000},
        "initialDelaySeconds": 120,
        "periodSeconds": 30,
        "timeoutSeconds": 5,
        "failureThreshold": 6,
    }


def test_rows_without_the_column_render_defaults():
    dep = _dep()
    del dep.probes
    assert render_probes(dep)["readinessProbe"]["initialDelaySeconds"] == 60


def test_build_deployment_uses_row_probes_for_both_engines():
    for engine in ("vllm", "sglang"):
        dep = _dep(engine=engine, probes={"liveness": {}})
        c = build_deployment(dep)["spec"]["template"]["spec"]["containers"][0]
        assert c["readinessProbe"]["httpGet"] == {"path": "/health", "port": 8000}
        assert c["livenessProbe"]["initialDelaySeconds"] == 120


# ─── import (reverse) ─────────────────────────────────────────────────────────


def test_probe_from_k8s_http_and_unsupported():
    spec, warn = probe_from_k8s(
        {
            "httpGet": {"path": "/ready", "port": 8000},
            "initialDelaySeconds": 7,
            "periodSeconds": 3,
            "failureThreshold": 2,
        }
    )
    assert spec == {"path": "/ready", "initial_delay_seconds": 7, "period_seconds": 3, "failure_threshold": 2}
    assert warn is None
    assert probe_from_k8s({"exec": {"command": ["true"]}}) == (None, "probe_unsupported")
    assert probe_from_k8s(None) == (None, None)
    spec, warn = probe_from_k8s({"httpGet": {"path": "/health", "port": 9000}})
    assert spec["path"] == "/health" and warn == "probe_port_changed"


def test_import_round_trips_probes_through_build_deployment():
    dep = _dep(
        image="vllm/vllm-openai:latest",
        probes={"readiness": {"initial_delay_seconds": 5}, "liveness": {"path": "/live"}},
    )
    manifest = build_deployment(dep)
    c = manifest["spec"]["template"]["spec"]["containers"][0]
    spec = {
        "name": "m-deployment", "namespace": "ns", "labels": {}, "replicas": 1,
        "container": {
            "name": c["name"], "image": c["image"], "command": c.get("command") or [], "args": c["args"],
            "env": [], "env_raw": [], "resources": {}, "ports": c["ports"], "volume_mounts": [],
            "readiness_probe": c["readinessProbe"], "liveness_probe": c["livenessProbe"],
        },
        "volumes": [], "node_selector": None, "tolerations": None,
    }
    out = build_recipe_draft(spec)
    assert out["warnings"] == []
    assert out["draft"]["probes"] == {
        "readiness": {"path": "/health", "initial_delay_seconds": 5, "period_seconds": 10, "timeout_seconds": 5,
                      "failure_threshold": 30},
        "liveness": {"path": "/live", "initial_delay_seconds": 120, "period_seconds": 30, "timeout_seconds": 5,
                     "failure_threshold": 3},
    }


def test_import_without_probes_leaves_field_empty():
    spec = {"name": "x", "namespace": "ns", "labels": {}, "replicas": 1, "container": {"image": "img", "command": [],
            "args": ["--model", "/w/m"], "env": [], "resources": {}, "ports": []}, "volumes": []}
    assert build_recipe_draft(spec)["draft"]["probes"] is None
