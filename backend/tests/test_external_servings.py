"""Tests for external vLLM/SGLang serving discovery."""

from app.services.deployment_status import classify


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

import types
from unittest.mock import AsyncMock, MagicMock, patch

from app.clients.k8s import K8sClient


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
