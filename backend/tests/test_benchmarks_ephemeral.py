"""Ephemeral (portal-deployment clone) perf bench now runs as one self-serving Job."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.benchmark_manifests import job_name_for


async def test_ephemeral_perf_creates_single_self_serving_job(client_for_user, super_user, mock_db):
    base = MagicMock()
    base.id = uuid.uuid4()
    base.model_name = "m"
    base.image = "vllm/vllm-openai:v0.6.0"
    base.model_path = "/models/m"
    base.replicas = 1
    base.gpu_count = 1
    base.gpu_resource_key = "nvidia.com/gpu"
    base.cpu_request = None
    base.cpu_limit = None
    base.memory_request = None
    base.memory_limit = None
    base.node_selector = {}
    base.tolerations = None
    base.pvc_name = "w"
    base.pvc_mount_path = "/models"
    base.vllm_extra_args = []
    base.env = {}
    result = MagicMock()
    result.scalar_one_or_none.return_value = base
    mock_db.execute = AsyncMock(return_value=result)
    fake_k8s = MagicMock()
    fake_k8s.create_job = AsyncMock()
    body = {"tool": "vllm_serving", "params": {"num_prompts": 10}, "ephemeral": True, "deployment_id": str(base.id)}
    with patch("app.api.benchmarks.k8s_for_cluster", AsyncMock(return_value=fake_k8s)):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/benchmarks", json=body)
    assert resp.status_code == 201, resp.text
    run = mock_db.add.call_args.args[0]
    assert run.status == "pending" and run.serving_k8s_name is None and run.serving_torn_down is True
    assert run.k8s_job_name == job_name_for(run.id)
    ns, manifest = fake_k8s.create_job.await_args.args
    assert manifest["kind"] == "Job"
    script = manifest["spec"]["template"]["spec"]["containers"][0]["command"][2]
    assert "vllm serve" in script and "vllm bench serve" in script
