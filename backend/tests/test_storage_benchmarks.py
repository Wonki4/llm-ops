"""Benchmark jobs: deployment storages read-only, bench default storage, legacy snapshots."""

import types
import uuid
from unittest.mock import AsyncMock, patch

from app.db.models.custom_model_deployment import CustomModelDeployment
from app.jobs import reconcile_benchmarks
from app.services.benchmark_manifests import build_vllm_bench_job
from app.services.benchmark_serving import build_ephemeral_deployment
from tests._storage import CLUSTER, legacy_entry


def _run(**kw):
    base = dict(id=uuid.uuid4(), tool="vllm_serving", kind="performance", params={}, k8s_namespace="bench")
    base.update(kw)
    return types.SimpleNamespace(**base)


def _job_volumes(manifest):
    pod = manifest["spec"]["template"]["spec"]
    return pod["volumes"], pod["containers"][0]["volumeMounts"]


def test_bench_job_takes_prebuilt_volumes():
    vols = [{"name": "model-weights", "nfs": {"server": "s", "path": "/p", "readOnly": True}}]
    mounts = [{"name": "model-weights", "mountPath": "/models", "readOnly": True}]
    m = build_vllm_bench_job(_run(), image="i", target_base_url="http://t", api_key="k", served_model="m",
                             volumes=vols, volume_mounts=mounts)
    assert _job_volumes(m) == (vols, mounts)
    m = build_vllm_bench_job(_run(), image="i", target_base_url="http://t", api_key="k", served_model="m")
    pod = m["spec"]["template"]["spec"]
    assert pod.get("volumes", []) == [] and pod["containers"][0].get("volumeMounts", []) == []


def test_deployment_target_mounts_its_storages_read_only():
    from app.api.benchmarks import _build_bench_job

    storages = [legacy_entry("models-pvc", "/models")]
    dep = types.SimpleNamespace(model_path="/models/llama", image="vllm:1", storages=storages)
    m = _build_bench_job(_run(), deployment=dep, target_base="http://t", api_key="k", image_override=None)
    vols, mounts = _job_volumes(m)
    assert vols == [{"name": "model-weights", "persistentVolumeClaim": {"claimName": "models-pvc", "readOnly": True}}]
    assert mounts == [{"name": "model-weights", "mountPath": "/models", "readOnly": True}]


def test_raw_target_uses_bench_vols():
    from app.api.benchmarks import _build_bench_job

    bench = ([{"name": "model-weights", "nfs": {"server": "s", "path": "/p", "readOnly": True}}],
             [{"name": "model-weights", "mountPath": "/m", "readOnly": True}])
    m = _build_bench_job(_run(model_name="llama"), deployment=None, target_base="http://t", api_key="k",
                         image_override=None, bench_vols=bench)
    assert _job_volumes(m) == bench


def test_ephemeral_copies_storages():
    base = CustomModelDeployment(
        model_name="llama", namespace="prod", image="i", replicas=1, gpu_count=1, gpu_resource_key="nvidia.com/gpu",
        model_path="/models/llama", ingress_host="h", ingress_path="/", ingress_class="nginx", engine="vllm",
        storages=[legacy_entry("models-pvc", "/models")],
    )
    eph = build_ephemeral_deployment(base, name="bench-x", namespace="bench", overrides=None)
    assert eph.storages == base.storages and eph.storages is not base.storages


async def test_ephemeral_storages_keep_snapshot_on_the_template_target():
    from app.api.benchmarks import _ephemeral_storages

    base = types.SimpleNamespace(
        cluster_id=CLUSTER, namespace="prod", storages=[legacy_entry("models-pvc", "/models", namespace="prod")]
    )
    with patch("app.api.benchmarks.resolve_entries", AsyncMock()) as res:
        out = await _ephemeral_storages(None, base, CLUSTER, "prod")
    assert out == base.storages and out[0] is not base.storages[0]
    res.assert_not_awaited()


async def test_ephemeral_storages_re_resolve_for_another_namespace():
    from app.api.benchmarks import _ephemeral_storages

    base = types.SimpleNamespace(cluster_id=CLUSTER, namespace="prod", storages=[legacy_entry("models-pvc", "/models")])
    with patch("app.api.benchmarks.resolve_entries", AsyncMock(return_value=["resolved"])) as res:
        assert await _ephemeral_storages(None, base, CLUSTER, "bench") == ["resolved"]
    assert res.await_args.args[1:] == (base.storages, CLUSTER, "bench")
    assert res.await_args.kwargs["previous"] is base.storages


async def test_reconcile_legacy_snapshot_still_mounts_pvc():
    snap = {
        "model_path": "/models/llama", "pvc_name": "models-pvc", "pvc_mount_path": "/models",
        "vllm_extra_args": [], "env": {},
    }
    vols, mounts = reconcile_benchmarks._snapshot_volumes(snap)
    assert vols[0]["persistentVolumeClaim"] == {"claimName": "models-pvc", "readOnly": True}
    snap2 = {"storages": [legacy_entry("new-pvc", "/models")]}
    assert reconcile_benchmarks._snapshot_volumes(snap2)[0][0]["persistentVolumeClaim"]["claimName"] == "new-pvc"
    assert reconcile_benchmarks._snapshot_volumes({}) == ([], [])


async def test_endpoint_benchmark_uses_bench_volumes(client_for_user, super_user, mock_db):
    bench = ([{"name": "model-weights", "nfs": {"server": "s", "path": "/p", "readOnly": True}}],
             [{"name": "model-weights", "mountPath": "/m", "readOnly": True}])
    k8s = types.SimpleNamespace(create_job=AsyncMock())
    with (
        patch("app.api.benchmarks.k8s_for_cluster", AsyncMock(return_value=k8s)),
        patch("app.api.benchmarks.bench_volumes", AsyncMock(return_value=bench)) as bv,
    ):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/benchmarks", json={
                "tool": "vllm_serving", "base_url": "http://x:8000", "model_name": "llama", "namespace": "bench",
            })
    assert resp.status_code in (200, 201), resp.text
    assert bv.await_args.args[1:] == (None, "bench", {})
    manifest = k8s.create_job.await_args.args[1]
    assert _job_volumes(manifest) == bench
