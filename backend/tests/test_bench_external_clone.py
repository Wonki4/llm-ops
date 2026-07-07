"""Benchmark-by-cloning-an-external-serving: live-spec reader, clone builder, facts."""

from unittest.mock import AsyncMock, MagicMock, patch

from kubernetes_asyncio.client.exceptions import ApiException

from app.clients.k8s import K8sClient


def _live_deployment(args=None, ports=None, volumes=None, mounts=None,
                     env=None, node_selector=None, tolerations=None, image="vllm/vllm-openai:v0.6.0"):
    container = MagicMock()
    container.name = "server"
    container.image = image
    container.command = None
    container.args = args if args is not None else ["--model", "/models/llama-3-8b", "--port", "8000"]
    container.env = env
    container.resources = {"limits": {"nvidia.com/gpu": "1"}}
    container.ports = ports
    container.volume_mounts = mounts
    pod_spec = MagicMock()
    pod_spec.containers = [container]
    pod_spec.volumes = volumes
    pod_spec.node_selector = node_selector
    pod_spec.tolerations = tolerations
    dep = MagicMock()
    dep.metadata.name = "ext-vllm"
    dep.metadata.namespace = "team-a"
    dep.metadata.labels = {"app": "ext-vllm"}
    dep.spec.replicas = 2
    dep.spec.template.spec = pod_spec
    return dep


def _k8s_with(apps):
    fake_api = MagicMock()
    fake_api.close = AsyncMock()
    fake_api.sanitize_for_serialization = lambda x: x
    return (
        patch.object(K8sClient, "_api_client", AsyncMock(return_value=fake_api)),
        patch("app.clients.k8s.client.AppsV1Api", return_value=apps),
    )


async def test_read_deployment_shapes_spec():
    apps = MagicMock()
    apps.read_namespaced_deployment = AsyncMock(return_value=_live_deployment())
    p1, p2 = _k8s_with(apps)
    with p1, p2:
        spec = await K8sClient().read_deployment("team-a", "ext-vllm")
    assert spec["name"] == "ext-vllm" and spec["namespace"] == "team-a"
    assert spec["container"]["image"] == "vllm/vllm-openai:v0.6.0"
    assert spec["container"]["args"][0] == "--model"
    assert spec["container"]["resources"] == {"limits": {"nvidia.com/gpu": "1"}}
    assert spec["replicas"] == 2


async def test_read_deployment_none_on_404():
    apps = MagicMock()
    apps.read_namespaced_deployment = AsyncMock(side_effect=ApiException(status=404))
    p1, p2 = _k8s_with(apps)
    with p1, p2:
        assert await K8sClient().read_deployment("team-a", "gone") is None
