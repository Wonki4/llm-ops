"""Benchmark-by-cloning-an-external-serving: live-spec reader, clone builder, facts."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from kubernetes_asyncio.client.exceptions import ApiException

from app.clients.k8s import K8sClient
from app.services.benchmark_serving import build_external_clone, external_bench_facts


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


def _spec(args=None, ports=None, volumes=None, mounts=None, env_raw=None,
          node_selector=None, tolerations=None, image="vllm/vllm-openai:v0.6.0"):
    return {
        "name": "ext-vllm", "namespace": "team-a", "labels": {"app": "ext-vllm"},
        "replicas": 2,
        "container": {
            "name": "server", "image": image, "command": [],
            "args": args if args is not None else ["--model", "/models/llama-3-8b", "--port", "8000"],
            "env": [], "env_raw": env_raw or [],
            "resources": {"limits": {"nvidia.com/gpu": "1"}},
            "ports": ports or [],
            "volume_mounts": mounts or [],
        },
        "volumes": volumes or [],
        "node_selector": node_selector,
        "tolerations": tolerations,
    }


# ─── external_bench_facts ────────────────────────────────────


def test_facts_served_model_name_wins():
    facts = external_bench_facts(_spec(args=["--model", "/models/llama", "--served-model-name", "llama-3"]))
    assert facts["served_model"] == "llama-3"
    assert facts["tokenizer"] == "/models/llama"
    assert facts["model_arg"] == "/models/llama"


def test_facts_model_fallback_and_equals_form():
    facts = external_bench_facts(_spec(args=["--model=/models/qwen"]))
    assert facts["served_model"] == "/models/qwen"
    assert facts["tokenizer"] == "/models/qwen"


def test_facts_missing_model_raises():
    with pytest.raises(ValueError):
        external_bench_facts(_spec(args=["--port", "8000"]))


def test_facts_maps_pvc_backing_the_model_path():
    spec = _spec(
        args=["--model", "/models/llama-3-8b"],
        mounts=[{"name": "weights", "mountPath": "/models"}],
        volumes=[{"name": "weights", "persistentVolumeClaim": {"claimName": "model-weights"}}],
    )
    facts = external_bench_facts(spec)
    assert facts["pvc_name"] == "model-weights"
    assert facts["pvc_mount_path"] == "/models"


def test_facts_no_pvc_when_volume_not_pvc():
    spec = _spec(
        mounts=[{"name": "weights", "mountPath": "/models"}],
        volumes=[{"name": "weights", "nfs": {"server": "n", "path": "/x"}}],
    )
    facts = external_bench_facts(spec)
    assert facts["pvc_name"] is None


# ─── build_external_clone ────────────────────────────────────


def test_clone_names_labels_replicas_and_service_port():
    manifests = build_external_clone(_spec(), name="bench-abc123")
    dep, svc = manifests
    assert dep["metadata"]["name"] == "bench-abc123-deployment"
    assert svc["metadata"]["name"] == "bench-abc123-service"
    assert dep["spec"]["replicas"] == 1
    sel = svc["spec"]["selector"]
    assert sel == dep["spec"]["template"]["metadata"]["labels"]
    port = svc["spec"]["ports"][0]
    assert port["port"] == 80 and port["targetPort"] == 8000  # from --port arg


def test_clone_service_port_falls_back_to_container_port_then_8000():
    m = build_external_clone(_spec(args=["--model", "/m"], ports=[{"containerPort": 9000}]), name="b1")
    assert m[1]["spec"]["ports"][0]["targetPort"] == 9000
    m = build_external_clone(_spec(args=["--model", "/m"]), name="b2")
    assert m[1]["spec"]["ports"][0]["targetPort"] == 8000


def test_clone_preserves_volumes_selector_tolerations_and_container():
    spec = _spec(
        mounts=[{"name": "weights", "mountPath": "/models"}],
        volumes=[{"name": "weights", "persistentVolumeClaim": {"claimName": "w"}}],
        node_selector={"gpu": "a100"},
        tolerations=[{"key": "gpu", "operator": "Exists"}],
        env_raw=[{"name": "VLLM_API_KEY", "value": "sk-x"}],
    )
    dep = build_external_clone(spec, name="b3")[0]
    pod = dep["spec"]["template"]["spec"]
    assert pod["volumes"] == spec["volumes"]
    assert pod["nodeSelector"] == {"gpu": "a100"}
    assert pod["tolerations"] == spec["tolerations"]
    c = pod["containers"][0]
    assert c["image"] == "vllm/vllm-openai:v0.6.0"
    assert c["volumeMounts"] == spec["container"]["volume_mounts"]
    assert c["env"] == spec["container"]["env_raw"]


def test_clone_overrides_resources_and_image():
    dep = build_external_clone(
        _spec(), name="b4",
        overrides={"resources": {"limits": {"nvidia.com/gpu": "2"}}, "image": "vllm/vllm-openai:v0.7.0"},
    )[0]
    c = dep["spec"]["template"]["spec"]["containers"][0]
    assert c["resources"] == {"limits": {"nvidia.com/gpu": "2"}}
    assert c["image"] == "vllm/vllm-openai:v0.7.0"
