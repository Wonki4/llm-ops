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
    nfs_fields_incomplete,
    resolve_bench_nfs,
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


# ── NFS resolution / validation ──────────────────────────────────────────────

def test_nfs_fields_incomplete():
    assert nfs_fields_incomplete(None, None, None) is False
    assert nfs_fields_incomplete("nfs.local", "/export", "/models") is False
    assert nfs_fields_incomplete("", "", "") is False
    assert nfs_fields_incomplete("nfs.local", None, None) is True
    assert nfs_fields_incomplete("nfs.local", "/export", None) is True
    assert nfs_fields_incomplete(None, "/export", "/models") is True


def test_resolve_nfs_run_override_beats_cluster_default():
    out = resolve_bench_nfs(
        {"nfs_server": "run.nfs", "nfs_path": "/run", "nfs_mount_path": "/m"},
        default_server="cl.nfs", default_path="/cl", default_mount_path="/cm",
    )
    assert out == ("run.nfs", "/run", "/m")


def test_resolve_nfs_falls_back_to_cluster_default():
    out = resolve_bench_nfs(
        {}, default_server="cl.nfs", default_path="/cl", default_mount_path="/cm"
    )
    assert out == ("cl.nfs", "/cl", "/cm")


def test_resolve_nfs_none_when_nothing_set():
    assert resolve_bench_nfs({}) == (None, None, None)
    assert resolve_bench_nfs(None) == (None, None, None)


def _perf_run():
    return types.SimpleNamespace(
        id=uuid.uuid4(), params={}, k8s_namespace="bench",
        tool="vllm_serving", kind="performance",
    )


def test_vllm_bench_job_mounts_nfs_when_set():
    job = build_vllm_bench_job(
        _perf_run(), image="vllm:latest", target_base_url="http://t", api_key="k",
        served_model="m", tokenizer="/models/m",
        nfs_server="nfs.local", nfs_path="/export/models", nfs_mount_path="/models",
    )
    spec = job["spec"]["template"]["spec"]
    vol = next(v for v in spec["volumes"] if v["name"] == "model-weights")
    assert vol["nfs"] == {"server": "nfs.local", "path": "/export/models", "readOnly": True}
    mount = spec["containers"][0]["volumeMounts"][0]
    assert mount["mountPath"] == "/models" and mount["readOnly"] is True


def test_vllm_bench_job_mounts_pvc_for_deployment_target():
    # Deployment targets still mount their own PVC (unchanged by the NFS switch).
    job = build_vllm_bench_job(
        _perf_run(), image="vllm:latest", target_base_url="http://t", api_key="k",
        served_model="m", pvc_name="weights", pvc_mount_path="/models",
    )
    vol = job["spec"]["template"]["spec"]["volumes"][0]
    assert vol["persistentVolumeClaim"]["claimName"] == "weights"


def test_vllm_bench_job_no_volume_when_unset():
    job = build_vllm_bench_job(
        _perf_run(), image="vllm:latest", target_base_url="http://t", api_key="k",
        served_model="m",
    )
    spec = job["spec"]["template"]["spec"]
    assert spec["volumes"] == []
    assert spec["containers"][0]["volumeMounts"] == []


def _bench_script(params):
    run = types.SimpleNamespace(
        id=uuid.uuid4(), params=params, k8s_namespace="bench",
        tool="vllm_serving", kind="performance",
    )
    job = build_vllm_bench_job(
        run, image="vllm:latest", target_base_url="http://t", api_key="k",
        served_model="m", tokenizer="/models/m",
    )
    return job["spec"]["template"]["spec"]["containers"][0]["command"][2]


def test_extra_params_pass_through_as_flags():
    script = _bench_script({"trust_remote_code": True, "random_range_ratio": 0.5})
    assert "--trust-remote-code" in script  # bool True → bare flag
    assert "--random-range-ratio 0.5" in script  # underscores → dashes


def test_extra_params_skip_reserved_infra_and_collisions():
    script = _bench_script(
        {"num_prompts": 50, "seed": 42, "nfs_server": "nfs.local", "nfs_path": "/m",
         "nfs_mount_path": "/models"}
    )
    assert script.count("--num-prompts") == 1 and "--num-prompts 50" in script
    assert script.count("--seed") == 1 and "--seed 42" in script  # user seed wins, emitted once
    assert "--nfs-server" not in script  # infra-only, never a CLI flag


def test_extra_params_false_bool_omitted():
    assert "--disable-tqdm" not in _bench_script({"disable_tqdm": False})
