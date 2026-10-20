"""Deployment manifests mount the resolved storages; migrated rows render unchanged."""

import pytest

from app.db.models.custom_model_deployment import CustomModelDeployment
from app.services import pd_serving
from app.services.model_deployment_manifests import build_all
from tests._storage import SID, SID2, legacy_entry


def _dep(**kw) -> CustomModelDeployment:
    base = dict(
        model_name="llama", namespace="prod", image="vllm/vllm-openai:v0.9", replicas=1, gpu_count=1,
        gpu_resource_key="nvidia.com/gpu", model_path="/models/llama", ingress_host="llama.local",
        ingress_path="/", ingress_class="nginx", engine="vllm", serving_mode="aggregated", storages=[],
    )
    base.update(kw)
    return CustomModelDeployment(**base)


def _pod(manifests, kind="Deployment"):
    return next(m for m in manifests if m["kind"] == kind)["spec"]["template"]["spec"]


def test_migrated_deployment_renders_exactly_the_old_pvc_volume():
    pod = _pod(build_all(_dep(storages=[legacy_entry("models-pvc", "/models")])))
    assert pod["volumes"] == [{"name": "model-weights", "persistentVolumeClaim": {"claimName": "models-pvc"}}]
    assert pod["containers"][0]["volumeMounts"] == [{"name": "model-weights", "mountPath": "/models"}]


def test_multiple_storages_and_shm():
    nfs = {
        "storage_id": SID2, "mount_path": "/root/.cache/huggingface", "read_only": False, "volume_name": "st-hf-cache",
        "resolved": {"binding_id": "b", "type": "nfs", "claim_name": None, "namespace": None,
                     "nfs_server": "10.0.0.5", "nfs_path": "/cache", "sub_path": None},
    }
    dep = _dep(storages=[legacy_entry("models-pvc", "/models"), nfs], runtime={"shm_size_gi": 16})
    pod = _pod(build_all(dep))
    assert [v["name"] for v in pod["volumes"]] == ["model-weights", "st-hf-cache", "shm"]
    assert [m["mountPath"] for m in pod["containers"][0]["volumeMounts"]] == [
        "/models", "/root/.cache/huggingface", "/dev/shm",
    ]


def test_no_storages_no_volumes():
    pod = _pod(build_all(_dep()))
    assert pod["volumes"] == [] and pod["containers"][0]["volumeMounts"] == []


def test_pool_override_replaces_base_list():
    base = legacy_entry("models-pvc", "/models")
    prefill_only = {**legacy_entry("prefill-pvc", "/models", storage_id=SID2), "volume_name": "st-prefill"}
    dep = _dep(
        serving_mode="pd", storages=[base],
        pd_config={"prefill": {"replicas": 1, "storages": [prefill_only]}, "decode": {"replicas": 1}},
    )
    pools = {d["metadata"]["labels"][pd_serving.LABEL_PD_ROLE]: d for d in build_all(dep) if d["kind"] == "Deployment"}
    pre = pools["prefill"]["spec"]["template"]["spec"]
    dec = pools["decode"]["spec"]["template"]["spec"]
    assert pre["volumes"][0]["persistentVolumeClaim"]["claimName"] == "prefill-pvc"
    assert dec["volumes"][0]["persistentVolumeClaim"]["claimName"] == "models-pvc"


def test_role_override_validates_storages():
    assert pd_serving.RoleOverride(storages=None).storages is None
    assert pd_serving.RoleOverride(storages=[{"storage_id": SID, "mount_path": "/m/"}]).storages == [
        {"storage_id": SID, "mount_path": "/m", "read_only": True}
    ]
    with pytest.raises(ValueError):
        pd_serving.RoleOverride(storages=[{"storage_id": SID, "mount_path": "rel"}])
    assert "storages" in pd_serving.POOL_FIELDS
    assert "pvc_name" not in pd_serving.POOL_FIELDS
    assert "pvc_name" not in pd_serving.RoleOverride.model_fields
