"""Tests for external vLLM/SGLang serving discovery."""

import asyncio
import types
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.clients.k8s import K8sClient, K8sNotConfigured
from app.services.deployment_status import classify
from app.services.external_servings import scan_clusters, to_external_serving

# ─── classify ────────────────────────────────────────────────


def test_classify_ready():
    observed = {"ready": 2, "available": 2, "conditions": []}
    assert classify(observed, 2) == ("Ready", None)


def test_classify_pending_when_no_ready_pods():
    observed = {"ready": 0, "available": 0, "conditions": []}
    status, message = classify(observed, 1)
    assert status == "Pending"


def test_classify_stopped_when_zero_desired():
    observed = {"ready": 0, "available": 0, "conditions": []}
    status, _ = classify(observed, 0)
    assert status == "Stopped"


def test_classify_failed_on_progress_deadline():
    observed = {
        "ready": 0,
        "available": 0,
        "conditions": [
            {"type": "Progressing", "status": "False", "reason": "ProgressDeadlineExceeded", "message": "x"}
        ],
    }
    status, _ = classify(observed, 1)
    assert status == "Failed"


# ─── list_deployments_all ────────────────────────────────────


def _fake_k8s_deployment(name="ext-vllm", namespace="team-a", image="vllm/vllm-openai:v0.6.0",
                         args=None, labels=None, replicas=2, ready=2, available=2):
    container = types.SimpleNamespace(image=image, args=args or ["--model", "/models/llama"])
    dep = types.SimpleNamespace(
        metadata=types.SimpleNamespace(name=name, namespace=namespace, labels=labels or {},
                                       creation_timestamp=None),
        spec=types.SimpleNamespace(
            replicas=replicas,
            template=types.SimpleNamespace(spec=types.SimpleNamespace(containers=[container])),
        ),
        status=types.SimpleNamespace(ready_replicas=ready, available_replicas=available, conditions=None),
    )
    return dep


async def test_list_deployments_all_shapes_items():
    fake_apps = MagicMock()
    fake_apps.list_deployment_for_all_namespaces = AsyncMock(
        return_value=types.SimpleNamespace(items=[_fake_k8s_deployment()])
    )
    fake_api_client = MagicMock()
    fake_api_client.close = AsyncMock()

    k8s = K8sClient()
    with patch.object(K8sClient, "_api_client", AsyncMock(return_value=fake_api_client)), \
         patch("app.clients.k8s.client.AppsV1Api", return_value=fake_apps):
        items = await k8s.list_deployments_all()

    assert len(items) == 1
    item = items[0]
    assert item["name"] == "ext-vllm"
    assert item["namespace"] == "team-a"
    assert item["containers"] == [{"image": "vllm/vllm-openai:v0.6.0", "args": ["--model", "/models/llama"]}]
    assert item["replicas"] == 2 and item["ready"] == 2 and item["available"] == 2
    assert item["conditions"] == []
    fake_api_client.close.assert_awaited()


# ─── to_external_serving ─────────────────────────────────────


def _raw_dep(name="ext-vllm", namespace="team-a", image="vllm/vllm-openai:v0.6.0",
             args=None, labels=None, replicas=2, ready=2, available=2):
    return {
        "name": name, "namespace": namespace, "labels": labels or {},
        "created_at": "2026-07-01T00:00:00+00:00",
        "containers": [{"image": image, "args": args if args is not None else ["--model", "/models/llama-3-8b"]}],
        "replicas": replicas, "ready": ready, "available": available, "conditions": [],
    }


def test_vllm_image_detected():
    serving = to_external_serving(_raw_dep())
    assert serving["engine"] == "vllm"
    assert serving["deployment_name"] == "ext-vllm"
    assert serving["model_path"] == "/models/llama-3-8b"
    assert serving["status"] == "Ready"


def test_sglang_image_detected():
    serving = to_external_serving(_raw_dep(image="lmsysorg/sglang:latest"))
    assert serving["engine"] == "sglang"


def test_unrelated_image_ignored():
    assert to_external_serving(_raw_dep(image="nginx:1.27")) is None


def test_portal_managed_label_excluded():
    dep = _raw_dep(labels={"llm-ops/managed-by": "litellm-portal"})
    assert to_external_serving(dep) is None


def test_model_arg_equals_form():
    serving = to_external_serving(_raw_dep(args=["--model=/models/qwen", "--port", "8000"]))
    assert serving["model_path"] == "/models/qwen"


def test_missing_model_arg_gives_none_path():
    serving = to_external_serving(_raw_dep(args=["--port", "8000"]))
    assert serving["model_path"] is None


# ─── scan_clusters ───────────────────────────────────────────


def _fake_client(deployments=None, error=None):
    fake = MagicMock()
    if error is not None:
        fake.list_deployments_all = AsyncMock(side_effect=error)
    else:
        fake.list_deployments_all = AsyncMock(return_value=deployments or [])
    return fake


async def test_scan_clusters_merges_and_tags_cluster():
    targets = [
        (None, "default", _fake_client([_raw_dep()])),
        ("cid-1", "prod", _fake_client([_raw_dep(name="prod-vllm", namespace="ml")])),
    ]
    servings, errors = await scan_clusters(targets)
    assert errors == []
    names = {(s["cluster_name"], s["deployment_name"]) for s in servings}
    assert names == {("default", "ext-vllm"), ("prod", "prod-vllm")}
    assert [s for s in servings if s["cluster_name"] == "prod"][0]["cluster_id"] == "cid-1"


async def test_scan_clusters_partial_failure_reports_error():
    targets = [
        (None, "default", _fake_client([_raw_dep()])),
        ("cid-1", "prod", _fake_client(error=RuntimeError("connection refused"))),
    ]
    servings, errors = await scan_clusters(targets)
    assert len(servings) == 1
    assert errors == [{"cluster": "prod", "message": "connection refused"}]


async def test_scan_clusters_not_configured_is_silent():
    targets = [(None, "default", _fake_client(error=K8sNotConfigured("no kubeconfig")))]
    servings, errors = await scan_clusters(targets)
    assert servings == [] and errors == []


async def test_scan_clusters_timeout_reports_error():
    async def _hang():
        await asyncio.sleep(10)

    fake = MagicMock()
    fake.list_deployments_all = MagicMock(side_effect=lambda: _hang())
    servings, errors = await scan_clusters([("cid-1", "slow", fake)], timeout=0.05)
    assert servings == []
    assert errors[0]["cluster"] == "slow"
    assert "timed out" in errors[0]["message"]


# ─── GET /api/model-deployments/external ─────────────────────


def _exec_result(rows):
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    result.scalar_one_or_none.return_value = rows[0] if rows else None
    return result


def _serving(cluster_id=None, cluster_name="default", name="ext-vllm", namespace="team-a"):
    return {
        "cluster_id": cluster_id, "cluster_name": cluster_name,
        "namespace": namespace, "deployment_name": name,
        "engine": "vllm", "image": "vllm/vllm-openai:v0.6.0",
        "replicas": 2, "ready_replicas": 2,
        "status": "Ready", "status_message": None,
        "created_at": "2026-07-01T00:00:00+00:00", "model_path": "/models/llama-3-8b",
        "labels": {}, "args": ["--model", "/models/llama-3-8b"],
    }


async def test_get_external_servings(client_for_user, super_user, mock_db):
    # execute #1: registered clusters (none) / execute #2: registrations (none)
    mock_db.execute = AsyncMock(side_effect=[_exec_result([]), _exec_result([])])
    with patch("app.api.model_deployments.scan_clusters", AsyncMock(return_value=([_serving()], []))):
        async with client_for_user(super_user) as client:
            resp = await client.get("/api/model-deployments/external")
    assert resp.status_code == 200
    body = resp.json()
    assert body["errors"] == []
    assert len(body["servings"]) == 1
    assert body["servings"][0]["deployment_name"] == "ext-vllm"
    assert body["servings"][0]["registration"] is None


async def test_get_external_servings_joins_registration(client_for_user, super_user, mock_db):
    reg = MagicMock()
    reg.id = uuid.uuid4()
    reg.cluster_id = None
    reg.namespace = "team-a"
    reg.deployment_name = "ext-vllm"
    reg.model_name = "llama-3-8b"
    reg.api_base = "https://ext.example.com"
    reg.litellm_model_id = "litellm-abc"
    mock_db.execute = AsyncMock(side_effect=[_exec_result([]), _exec_result([reg])])
    with patch("app.api.model_deployments.scan_clusters", AsyncMock(return_value=([_serving()], []))):
        async with client_for_user(super_user) as client:
            resp = await client.get("/api/model-deployments/external")
    body = resp.json()
    assert body["servings"][0]["registration"]["model_name"] == "llama-3-8b"
    assert body["servings"][0]["registration"]["litellm_model_id"] == "litellm-abc"


async def test_get_external_servings_reports_cluster_errors(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(side_effect=[_exec_result([]), _exec_result([])])
    with patch(
        "app.api.model_deployments.scan_clusters",
        AsyncMock(return_value=([], [{"cluster": "prod", "message": "scan timed out after 5s"}])),
    ):
        async with client_for_user(super_user) as client:
            resp = await client.get("/api/model-deployments/external")
    assert resp.status_code == 200
    assert resp.json()["errors"][0]["cluster"] == "prod"


async def test_get_external_servings_requires_super_user(client_for_user, regular_user):
    async with client_for_user(regular_user) as client:
        resp = await client.get("/api/model-deployments/external")
    assert resp.status_code == 403


async def test_get_external_servings_scans_registered_clusters(client_for_user, super_user, mock_db):
    """Registered clusters become scan targets after the portal default, and a
    registration keyed by the cluster's UUID joins onto that cluster's serving."""
    cluster_id = uuid.uuid4()
    cluster_row = MagicMock()
    cluster_row.id = cluster_id
    cluster_row.name = "prod"

    reg = MagicMock()
    reg.id = uuid.uuid4()
    reg.cluster_id = cluster_id
    reg.namespace = "ml"
    reg.deployment_name = "prod-vllm"
    reg.model_name = "qwen-72b"
    reg.api_base = "https://prod.example.com"
    reg.litellm_model_id = "litellm-prod-1"

    mock_db.execute = AsyncMock(side_effect=[_exec_result([cluster_row]), _exec_result([reg])])

    captured = {}

    async def _fake_scan(targets, timeout=5.0):
        captured["targets"] = targets
        serving = _serving(cluster_id=str(cluster_id), cluster_name="prod", name="prod-vllm", namespace="ml")
        return [serving], []

    with (
        patch("app.api.model_deployments.k8s_for_cluster", AsyncMock(return_value=MagicMock())),
        patch("app.api.model_deployments.scan_clusters", AsyncMock(side_effect=_fake_scan)),
    ):
        async with client_for_user(super_user) as client:
            resp = await client.get("/api/model-deployments/external")

    assert resp.status_code == 200
    targets = captured["targets"]
    assert len(targets) == 2
    assert targets[0][0] is None and targets[0][1] == "default"
    assert targets[1][0] == str(cluster_id) and targets[1][1] == "prod"
    registration = resp.json()["servings"][0]["registration"]
    assert registration["model_name"] == "qwen-72b"
    assert registration["litellm_model_id"] == "litellm-prod-1"
