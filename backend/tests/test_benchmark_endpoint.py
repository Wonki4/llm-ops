"""Endpoint-URL mode: benchmark an already-running endpoint by base URL alone.

No serving is provisioned — a single `vllm bench serve` Job hits the URL.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.benchmark_manifests import job_name_for


def _endpoint_body(**kw):
    body = {
        "tool": "vllm_serving",
        "base_url": "http://my-vllm:8000",
        "model_name": "meta-llama/Llama-3-8B",
        "params": {"random_input_len": 2048, "num_prompts": 25},
    }
    body.update(kw)
    return body


async def test_endpoint_url_creates_single_job_no_serving(client_for_user, super_user, mock_db):
    fake_k8s = MagicMock()
    fake_k8s.create_job = AsyncMock()
    with patch("app.api.benchmarks.k8s_for_cluster", AsyncMock(return_value=fake_k8s)):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/benchmarks", json=_endpoint_body())
    assert resp.status_code == 201, resp.text

    run = mock_db.add.call_args.args[0]
    assert run.status == "pending"
    assert run.deployment_id is None
    # (ephemeral defaults to False via the column server_default — not asserted
    # here because the mocked session doesn't apply ORM defaults pre-flush.)
    assert run.serving_snapshot["source"] == "endpoint"
    assert run.serving_snapshot["base_url"] == "http://my-vllm:8000"
    # Shape-complete so the run detail / compare views (which read these without
    # guards) never crash on an endpoint snapshot.
    assert run.serving_snapshot["vllm_extra_args"] == []
    assert run.serving_snapshot["env"] == {}
    assert run.k8s_job_name == job_name_for(run.id)

    ns, manifest = fake_k8s.create_job.await_args.args
    assert manifest["kind"] == "Job"
    pod = manifest["spec"]["template"]["spec"]
    # No serving is provisioned → no model-weights volume mounted.
    assert pod.get("volumes", []) == []
    script = pod["containers"][0]["command"][2]
    assert "vllm bench serve" in script
    assert "--base-url http://my-vllm:8000" in script
    assert "--model meta-llama/Llama-3-8B" in script
    # The user-supplied workload value flows straight through to the flag.
    assert "--random-input-len 2048" in script


async def test_endpoint_url_requires_model_name(client_for_user, super_user, mock_db):
    fake_k8s = MagicMock()
    fake_k8s.create_job = AsyncMock()
    body = _endpoint_body()
    del body["model_name"]
    with patch("app.api.benchmarks.k8s_for_cluster", AsyncMock(return_value=fake_k8s)):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/benchmarks", json=body)
    assert resp.status_code == 400
    assert "model_name" in resp.text
    fake_k8s.create_job.assert_not_awaited()


async def test_endpoint_url_rejects_deployment_id(client_for_user, super_user, mock_db):
    fake_k8s = MagicMock()
    fake_k8s.create_job = AsyncMock()
    body = _endpoint_body(deployment_id=str(uuid.uuid4()))
    with patch("app.api.benchmarks.k8s_for_cluster", AsyncMock(return_value=fake_k8s)):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/benchmarks", json=body)
    assert resp.status_code == 400
    assert "mutually exclusive" in resp.text
    fake_k8s.create_job.assert_not_awaited()


async def test_endpoint_url_rejects_non_http_url(client_for_user, super_user, mock_db):
    fake_k8s = MagicMock()
    fake_k8s.create_job = AsyncMock()
    body = _endpoint_body(base_url="ftp://nope")
    with patch("app.api.benchmarks.k8s_for_cluster", AsyncMock(return_value=fake_k8s)):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/benchmarks", json=body)
    assert resp.status_code == 400
    fake_k8s.create_job.assert_not_awaited()
