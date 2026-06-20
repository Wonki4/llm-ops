"""Unit tests for the K8s clusters settings feature.

Covers the pure logic that carries the risk: secret encryption round-trip and
kubeconfig parsing/validation. Full CRUD is exercised against the running app.
"""

import types
import uuid

import pytest
from fastapi import HTTPException

from app.api.k8s_clusters import _parse_kubeconfig
from app.services import crypto
from app.services.benchmark_manifests import (
    build_vllm_bench_job,
    pvc_pair_incomplete,
    resolve_bench_pvc,
)

SAMPLE_KUBECONFIG = """
apiVersion: v1
kind: Config
clusters:
- name: prod-cluster
  cluster:
    server: https://10.0.0.1:6443
    insecure-skip-tls-verify: true
- name: dev-cluster
  cluster:
    server: https://10.0.0.2:6443
contexts:
- name: prod
  context:
    cluster: prod-cluster
    user: admin
- name: dev
  context:
    cluster: dev-cluster
    user: admin
users:
- name: admin
  user:
    token: secret-token
"""


def test_crypto_round_trip():
    plaintext = "kubeconfig: contents\nwith: secrets"
    token = crypto.encrypt(plaintext)
    assert token != plaintext
    assert crypto.decrypt(token) == plaintext


def test_crypto_tokens_are_not_plaintext():
    token = crypto.encrypt("super-secret-token")
    assert "super-secret-token" not in token


def test_parse_kubeconfig_resolves_api_server():
    parsed, api_server = _parse_kubeconfig(SAMPLE_KUBECONFIG, "prod")
    assert api_server == "https://10.0.0.1:6443"
    assert parsed["kind"] == "Config"


def test_parse_kubeconfig_resolves_second_context():
    _parsed, api_server = _parse_kubeconfig(SAMPLE_KUBECONFIG, "dev")
    assert api_server == "https://10.0.0.2:6443"


def test_parse_kubeconfig_missing_context_raises_400():
    with pytest.raises(HTTPException) as exc:
        _parse_kubeconfig(SAMPLE_KUBECONFIG, "staging")
    assert exc.value.status_code == 400
    assert "staging" in exc.value.detail


def test_parse_kubeconfig_bad_yaml_raises_400():
    with pytest.raises(HTTPException) as exc:
        _parse_kubeconfig("clusters: [unclosed", "prod")
    assert exc.value.status_code == 400


def test_parse_kubeconfig_not_a_kubeconfig_raises_400():
    with pytest.raises(HTTPException) as exc:
        _parse_kubeconfig("just: a yaml\nwithout: contexts", "prod")
    assert exc.value.status_code == 400


# ── PVC resolution / validation ──────────────────────────────────────────────

def _dep(name, mount):
    return types.SimpleNamespace(pvc_name=name, pvc_mount_path=mount)


def test_pvc_pair_incomplete():
    assert pvc_pair_incomplete(None, None) is False
    assert pvc_pair_incomplete("weights", "/models") is False
    assert pvc_pair_incomplete("", "") is False
    assert pvc_pair_incomplete("weights", None) is True
    assert pvc_pair_incomplete(None, "/models") is True


def test_resolve_pvc_deployment_wins():
    # A targeted deployment's PVC beats both the run override and cluster default.
    name, mount = resolve_bench_pvc(
        _dep("dep-pvc", "/dep"),
        {"pvc_name": "run-pvc", "pvc_mount_path": "/run"},
        default_name="cl-pvc",
        default_mount_path="/cl",
    )
    assert (name, mount) == ("dep-pvc", "/dep")


def test_resolve_pvc_run_override_beats_cluster_default():
    name, mount = resolve_bench_pvc(
        None,
        {"pvc_name": "run-pvc", "pvc_mount_path": "/run"},
        default_name="cl-pvc",
        default_mount_path="/cl",
    )
    assert (name, mount) == ("run-pvc", "/run")


def test_resolve_pvc_falls_back_to_cluster_default():
    name, mount = resolve_bench_pvc(
        None, {}, default_name="cl-pvc", default_mount_path="/cl"
    )
    assert (name, mount) == ("cl-pvc", "/cl")


def test_resolve_pvc_none_when_nothing_set():
    assert resolve_bench_pvc(None, {}) == (None, None)
    assert resolve_bench_pvc(None, None) == (None, None)


def _perf_run():
    return types.SimpleNamespace(
        id=uuid.uuid4(), params={}, k8s_namespace="bench",
        tool="vllm_serving", kind="performance",
    )


def test_vllm_bench_job_mounts_pvc_when_set():
    job = build_vllm_bench_job(
        _perf_run(), image="vllm:latest", target_base_url="http://t", api_key="k",
        served_model="m", tokenizer="/models/m",
        pvc_name="weights", pvc_mount_path="/models",
    )
    spec = job["spec"]["template"]["spec"]
    assert any(v["name"] == "model-weights" for v in spec["volumes"])
    mount = spec["containers"][0]["volumeMounts"][0]
    assert mount["mountPath"] == "/models" and mount["readOnly"] is True


def test_vllm_bench_job_no_pvc_when_unset():
    job = build_vllm_bench_job(
        _perf_run(), image="vllm:latest", target_base_url="http://t", api_key="k",
        served_model="m",
    )
    spec = job["spec"]["template"]["spec"]
    assert spec["volumes"] == []
    assert spec["containers"][0]["volumeMounts"] == []
