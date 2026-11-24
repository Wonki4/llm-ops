import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from kubernetes_asyncio.client.exceptions import ApiException

from app.clients.k8s import K8sClient
from app.config import settings
from app.db.models.custom_benchmark_run import CustomBenchmarkRun
from app.jobs.reconcile_benchmarks import _drive_provisioning
from app.services.benchmark_credentials import BenchmarkCredentials, public_snapshot
from app.services.benchmark_serving import external_runner_key
from app.services.crypto import encrypt


def test_snapshot_removes_credentials_recursively_and_preserves_comparison_fields():
    source = {
        "image": "engine",
        "api_key_override": "canary-1",
        "resources": {"gpu_count": 2},
        "pd_config": {"prefill": {"env": {"CUSTOM": "canary-2"}, "engine_args": {"api-key": "canary-3"}}},
        "vllm_extra_args": ["--api-key", "canary-4", "canary-5", "--port", "8000", "--api-key=canary-6"],
        "runner_secret": True,
        "legacy_runner_key_encrypted": "canary-7",
    }
    safe = public_snapshot(source)
    assert safe["vllm_extra_args"] == ["--api-key", "***", "***", "--port", "8000", "***"]
    assert safe["resources"] == {"gpu_count": 2}
    assert "canary" not in json.dumps(safe) and "canary-4" in json.dumps(source)
    assert public_snapshot(safe) == safe


async def test_existing_history_and_detail_hide_legacy_credentials(client_for_user, super_user, mock_db):
    run = CustomBenchmarkRun(
        id=uuid4(),
        serving_snapshot={
            "api_key_override": "canary-1",
            "env": {"WHATEVER": "canary-2"},
            "vllm_extra_args": ["sh", "-c", "export", "CUSTOM=canary-3", "vllm", "serve"],
        },
    )
    rows = MagicMock()
    rows.scalars.return_value.all.return_value = [run]
    rows.scalar_one_or_none.return_value = run
    mock_db.execute.return_value = rows
    async with client_for_user(super_user) as client:
        for path in ("/api/benchmarks", f"/api/benchmarks/{run.id}"):
            resp = await client.get(path)
            assert resp.status_code == 200 and "canary-" not in resp.text
    assert "canary-1" in run.serving_snapshot.values()


async def test_litellm_admin_key_only_in_owned_secret(client_for_user, super_user, mock_db, monkeypatch):
    monkeypatch.setattr(settings, "litellm_admin_api_key", "master-canary")
    k8s = SimpleNamespace(create_job=AsyncMock())
    with (
        patch("app.api.benchmarks.k8s_for_cluster", AsyncMock(return_value=k8s)),
        patch("app.api.benchmarks.bench_volumes", AsyncMock(return_value=([], []))),
    ):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/benchmarks", json={"tool": "vllm_serving", "model_name": "alias"})
    assert resp.status_code == 201 and "master-canary" not in resp.text
    manifest = k8s.create_job.await_args.args[1]
    secret = k8s.create_job.await_args.kwargs["secret"]
    assert secret["stringData"] == {"runner-key": "master-canary"}
    assert "master-canary" not in json.dumps(manifest)


async def test_new_accuracy_is_rejected_without_leaking_credentials(client_for_user, super_user, mock_db):
    body = {"tool": "lm_eval", "ephemeral": True, "deployment_id": str(uuid4()), "api_key": "runner-canary"}
    async with client_for_user(super_user) as client:
        for path in ("/api/benchmarks/preview", "/api/benchmarks"):
            response = await client.post(path, json=body)
            assert response.status_code == 400
            assert "accuracy" in response.text and "canary" not in response.text
    mock_db.add.assert_not_called()


async def test_worker_removes_legacy_encrypted_key_after_handoff():
    run = CustomBenchmarkRun(
        id=uuid4(),
        serving_k8s_name="clone",
        k8s_namespace="bench",
        kind="accuracy",
        tool="lm_eval",
        params={},
        status="provisioning",
        serving_snapshot={"model_path": "m", "legacy_runner_key_encrypted": encrypt("canary")},
    )
    k8s = SimpleNamespace(create_job=AsyncMock(), read_deployment_status=AsyncMock(return_value={"ready": 1}))
    assert await _drive_provisioning(k8s, run) == 1
    assert run.serving_snapshot == {"model_path": "m"}
    assert k8s.create_job.await_args.kwargs["secret"]["stringData"]["runner-key"] == "canary"
    assert "canary" not in json.dumps(k8s.create_job.await_args.args[1])


def test_external_serving_existing_secret_ref_is_reused():
    ref = {"secretKeyRef": {"name": "model-auth", "key": "current"}}
    c = {
        "command": ["sh", "-c", 'vllm serve /m --api-key "$SERVICE_KEY"'],
        "env_raw": [{"name": "SERVICE_KEY", "valueFrom": ref}],
    }
    credentials = BenchmarkCredentials(uuid4())
    assert credentials.runner_env("BENCH_RUNNER_API_KEY", external_runner_key(c))["valueFrom"] == ref
    assert credentials.manifest() is None


async def test_secret_creation_failure_redacts_body_and_rolls_back_only_its_job():
    k8s = K8sClient()
    api = SimpleNamespace(close=AsyncMock())
    k8s._api_client = AsyncMock(return_value=api)
    owner = SimpleNamespace(metadata=SimpleNamespace(name="job", uid="owner-uid"))
    batch = SimpleNamespace(create_namespaced_job=AsyncMock(return_value=owner), delete_namespaced_job=AsyncMock())
    failure = ApiException(status=403, reason="denied canary")
    core = SimpleNamespace(create_namespaced_secret=AsyncMock(side_effect=failure))
    with (
        patch("app.clients.k8s.client.BatchV1Api", return_value=batch),
        patch("app.clients.k8s.client.CoreV1Api", return_value=core),
        pytest.raises(RuntimeError, match="403") as exc,
    ):
        await k8s.create_job("bench", {}, secret={"metadata": {"name": "secret"}, "stringData": {"k": "canary"}})
    assert "canary" not in str(exc.value)
    created = core.create_namespaced_secret.await_args.args[1]
    assert created["metadata"]["ownerReferences"] == [
        {"apiVersion": "batch/v1", "kind": "Job", "name": "job", "uid": "owner-uid"}
    ]
    assert batch.delete_namespaced_job.await_args.kwargs["body"]["preconditions"]["uid"] == "owner-uid"


@pytest.mark.parametrize("owned", [True, False])
async def test_worker_retry_verifies_existing_job_before_recovering_secret(owned):
    run = CustomBenchmarkRun(
        id=uuid4(),
        serving_k8s_name="clone",
        k8s_namespace="bench",
        kind="accuracy",
        tool="lm_eval",
        params={},
        status="provisioning",
        serving_snapshot={"model_path": "m", "api_key_override": "legacy-canary"},
    )
    k8s = SimpleNamespace(
        read_deployment_status=AsyncMock(return_value={"ready": 1}),
        create_job=AsyncMock(side_effect=ApiException(status=409)),
        read_job_status=AsyncMock(
            return_value={"run_id": str(run.id) if owned else "other", "succeeded": 0, "failed": 0}
        ),
        ensure_benchmark_job_secret=AsyncMock(),
    )
    if owned:
        await _drive_provisioning(k8s, run)
        assert run.status == "pending" and "legacy-canary" not in json.dumps(run.serving_snapshot)
        assert k8s.ensure_benchmark_job_secret.await_args.args[2]["stringData"]["runner-key"] == "legacy-canary"
    else:
        with pytest.raises(ApiException):
            await _drive_provisioning(k8s, run)
        assert run.status == "provisioning"
        k8s.ensure_benchmark_job_secret.assert_not_awaited()


def test_protecting_env_preserves_dependent_variables_and_existing_refs():
    ref = {"name": "ROOT", "valueFrom": {"secretKeyRef": {"name": "source", "key": "k"}}}
    credentials = BenchmarkCredentials(uuid4())
    items = [ref, {"name": "CUSTOM", "value": "canary-$(ROOT)-$$"}]
    env = credentials.protect_env(items)
    assert env[0] == ref and env[-1]["name"] == "CUSTOM"
    assert "canary" not in json.dumps(env) and "$(ROOT)" in env[-1]["value"] and env[-1]["value"].endswith("$$")
    assert "canary-" in credentials.manifest()["stringData"].values()
