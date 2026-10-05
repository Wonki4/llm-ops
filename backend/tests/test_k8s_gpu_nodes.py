"""K8sClient.list_gpu_nodes: label grouping, allocatable, requested sums."""

from unittest.mock import AsyncMock, MagicMock, patch

from app.clients.k8s import K8sClient


def _node(name, labels=None, allocatable=None, unschedulable=False):
    n = MagicMock()
    n.metadata.name = name
    n.metadata.labels = labels or {}
    n.status.allocatable = allocatable or {}
    n.spec.unschedulable = unschedulable
    return n


def _pod(node, phase, requests):
    p = MagicMock()
    p.spec.node_name = node
    p.status.phase = phase
    c = MagicMock()
    c.resources.requests = requests
    p.spec.containers = [c]
    return p


def _k8s_with(nodes, pods):
    fake_api = MagicMock()
    fake_api.close = AsyncMock()
    core = MagicMock()
    core.list_node = AsyncMock(return_value=MagicMock(items=nodes))
    core.list_pod_for_all_namespaces = AsyncMock(return_value=MagicMock(items=pods))
    return (
        patch.object(K8sClient, "_api_client", AsyncMock(return_value=fake_api)),
        patch("app.clients.k8s.client.CoreV1Api", return_value=core),
    )


async def test_list_gpu_nodes_groups_and_sums():
    nodes = [
        _node("gpu-a", {"gpu-type": "a100-80g"}, {"nvidia.com/gpu": "8", "cpu": "64"}),
        _node("gpu-b", {}, {"nvidia.com/gpu": "4"}),  # unlabelled
        _node("gpu-c", {"gpu-type": "a100-80g"}, {"nvidia.com/gpu": "8"}, unschedulable=True),
        _node("cpu-1", {"gpu-type": "none"}, {"cpu": "16"}),  # no GPU resource → filtered out
    ]
    pods = [
        _pod("gpu-a", "Running", {"nvidia.com/gpu": "2"}),
        _pod("gpu-a", "Pending", {"nvidia.com/gpu": "1"}),
        _pod("gpu-a", "Succeeded", {"nvidia.com/gpu": "8"}),  # finished: not counted
        _pod("gpu-b", "Running", {"cpu": "1"}),  # no GPU request
        _pod(None, "Running", {"nvidia.com/gpu": "1"}),  # unscheduled: ignored
    ]
    p1, p2 = _k8s_with(nodes, pods)
    with p1, p2:
        out = await K8sClient().list_gpu_nodes("gpu-type", ["nvidia.com/gpu"])
    by_name = {n["name"]: n for n in out}
    assert set(by_name) == {"gpu-a", "gpu-b", "gpu-c"}
    assert by_name["gpu-a"] == {
        "name": "gpu-a", "label_value": "a100-80g", "schedulable": True,
        "allocatable": {"nvidia.com/gpu": 8}, "requested": {"nvidia.com/gpu": 3},
    }
    assert by_name["gpu-b"]["label_value"] is None and by_name["gpu-b"]["requested"] == {"nvidia.com/gpu": 0}
    assert by_name["gpu-c"]["schedulable"] is False


async def test_list_gpu_nodes_multiple_resource_keys_and_quantities():
    nodes = [_node("mig", {"gpu-type": "a100-mig"}, {"nvidia.com/mig-3g.40gb": "2", "nvidia.com/gpu": "0"})]
    pods = [_pod("mig", "Running", {"nvidia.com/mig-3g.40gb": "1"})]
    p1, p2 = _k8s_with(nodes, pods)
    with p1, p2:
        out = await K8sClient().list_gpu_nodes("gpu-type", ["nvidia.com/gpu", "nvidia.com/mig-3g.40gb"])
    assert out[0]["allocatable"] == {"nvidia.com/gpu": 0, "nvidia.com/mig-3g.40gb": 2}
    assert out[0]["requested"] == {"nvidia.com/gpu": 0, "nvidia.com/mig-3g.40gb": 1}
