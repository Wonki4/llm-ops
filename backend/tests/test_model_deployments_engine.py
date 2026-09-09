"""Engine fields on the deployment API request models and serializer."""

import types
import uuid

import pytest
from pydantic import ValidationError

from app.api.model_deployments import CreateDeploymentRequest, UpdateDeploymentRequest, _serialize


def _row(**kw):
    base = dict(
        id=uuid.uuid4(), model_name="m", cluster_id=None, namespace="ns", image="img", replicas=1,
        gpu_count=1, gpu_resource_key="nvidia.com/gpu", cpu_request=None, cpu_limit=None,
        memory_request=None, memory_limit=None, node_selector=None, tolerations=None,
        pvc_name=None, pvc_mount_path=None, model_path="/m", vllm_extra_args=None, env=None,
        ingress_host="h", ingress_path="/", ingress_class="nginx", status="Pending",
        status_message=None, ready_replicas=0, service_cluster_ip=None, litellm_model_id=None,
        last_synced_at=None, created_by=None, updated_by=None, created_at=None, updated_at=None,
        engine="vllm", engine_args=None,
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_create_request_defaults():
    req = CreateDeploymentRequest(model_name="m", model_path="/m", ingress_host="h")
    assert req.engine == "vllm"
    assert req.engine_args is None
    assert req.image is None


def test_create_request_accepts_sglang_args():
    req = CreateDeploymentRequest(model_name="m", model_path="/m", ingress_host="h", engine="sglang", engine_args={"tp-size": 2})
    assert req.engine == "sglang"
    assert req.engine_args == {"tp-size": 2}


def test_create_request_rejects_bad_engine_and_keys():
    with pytest.raises(ValidationError):
        CreateDeploymentRequest(model_name="m", model_path="/m", ingress_host="h", engine="tgi")
    with pytest.raises(ValidationError):
        CreateDeploymentRequest(model_name="m", model_path="/m", ingress_host="h", engine_args={"--tp-size": 2})


def test_update_request_engine_fields_optional():
    assert UpdateDeploymentRequest().model_dump(exclude_unset=True) == {}
    with pytest.raises(ValidationError):
        UpdateDeploymentRequest(engine_args={"Bad Key": 1})


def test_serialize_includes_engine_fields():
    out = _serialize(_row(engine="sglang", engine_args={"tp-size": 2}))
    assert out["engine"] == "sglang"
    assert out["engine_args"] == {"tp-size": 2}
