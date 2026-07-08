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
          node_selector=None, tolerations=None, image="vllm/vllm-openai:v0.6.0", command=None):
    return {
        "name": "ext-vllm", "namespace": "team-a", "labels": {"app": "ext-vllm"},
        "replicas": 2,
        "container": {
            "name": "server", "image": image, "command": command or [],
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


def test_facts_positional_model_vllm_serve():
    facts = external_bench_facts(
        _spec(command=["vllm", "serve", "/models/qwen-7b"], args=["--port", "8000"])
    )
    assert facts["served_model"] == "/models/qwen-7b"
    assert facts["tokenizer"] == "/models/qwen-7b"
    assert facts["model_arg"] == "/models/qwen-7b"


def test_facts_sglang_model_path():
    facts = external_bench_facts(
        _spec(args=["--model-path", "/models/qwen", "--served-model-name", "qwen-2"])
    )
    assert facts["served_model"] == "qwen-2"
    assert facts["tokenizer"] == "/models/qwen"


def test_facts_flags_in_command_only():
    facts = external_bench_facts(
        _spec(command=["python", "-m", "vllm.entrypoints.openai.api_server", "--model", "/m/x"], args=[])
    )
    assert facts["served_model"] == "/m/x"


def test_facts_sh_c_shell_string():
    spec = _spec(
        command=["sh", "-c", "vllm serve /models/y --served-model-name y-8b --port 9000"],
        args=[],
    )
    facts = external_bench_facts(spec)
    assert facts["served_model"] == "y-8b"
    assert facts["tokenizer"] == "/models/y"


def test_facts_positional_model_pvc_detection():
    spec = _spec(
        command=["vllm", "serve", "/models/llama-3-8b"],
        args=[],
        mounts=[{"name": "weights", "mountPath": "/models"}],
        volumes=[{"name": "weights", "persistentVolumeClaim": {"claimName": "model-weights"}}],
    )
    facts = external_bench_facts(spec)
    assert facts["pvc_name"] == "model-weights"
    assert facts["pvc_mount_path"] == "/models"


def test_facts_missing_command_and_args_raises_cleanly():
    spec = _spec(args=[])
    spec["container"].pop("args")
    spec["container"].pop("command")
    with pytest.raises(ValueError):
        external_bench_facts(spec)


def test_clone_target_port_from_sh_c_command():
    spec = _spec(command=["sh", "-c", "vllm serve /m --port 9000"], args=[])
    manifests = build_external_clone(spec, name="bench-x")
    svc = next(m for m in manifests if m["kind"] == "Service")
    assert svc["spec"]["ports"][0]["targetPort"] == 9000


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


# ─── API: create/preview external_target ────────────────────────────────


def _exec_result(rows):
    r = MagicMock()
    r.scalars.return_value.all.return_value = rows
    r.scalar_one_or_none.return_value = rows[0] if rows else None
    return r


EXTERNAL_BODY = {
    "tool": "vllm_serving",
    "params": {"num_prompts": 10},
    "external_target": {"cluster_id": None, "namespace": "team-a", "deployment_name": "ext-vllm"},
}


def _spec_for_api():
    return _spec(args=["--model", "/models/llama-3-8b", "--served-model-name", "llama-3", "--port", "8000"])


async def test_create_external_clone_run(client_for_user, super_user, mock_db):
    fake_k8s = MagicMock()
    fake_k8s.read_deployment = AsyncMock(return_value=_spec_for_api())
    fake_k8s.create_or_patch = AsyncMock()
    with patch("app.api.benchmarks.k8s_for_cluster", AsyncMock(return_value=fake_k8s)):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/benchmarks", json=EXTERNAL_BODY)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["ephemeral"] is True
    assert body["model_name"] == "llama-3"
    # run row persisted with the snapshot contract the reconciler needs
    run = mock_db.add.call_args.args[0]
    assert run.deployment_id is None and run.ephemeral is True
    assert run.k8s_namespace == "team-a"
    snap = run.serving_snapshot
    assert snap["model_path"] == "llama-3"                # served name for the bench job
    assert snap["vllm_extra_args"] == _spec_for_api()["container"]["args"]
    assert run.params.get("tokenizer") == "/models/llama-3-8b"  # tokenizer preset from --model
    # clone applied into the serving's namespace
    ns, manifests = fake_k8s.create_or_patch.await_args.args
    assert ns == "team-a" and manifests[0]["kind"] == "Deployment"


async def test_create_external_missing_serving_404(client_for_user, super_user, mock_db):
    fake_k8s = MagicMock()
    fake_k8s.read_deployment = AsyncMock(return_value=None)
    with patch("app.api.benchmarks.k8s_for_cluster", AsyncMock(return_value=fake_k8s)):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/benchmarks", json=EXTERNAL_BODY)
    assert resp.status_code == 404


async def test_create_external_unparseable_args_400(client_for_user, super_user, mock_db):
    fake_k8s = MagicMock()
    fake_k8s.read_deployment = AsyncMock(return_value=_spec(args=["--port", "8000"]))
    with patch("app.api.benchmarks.k8s_for_cluster", AsyncMock(return_value=fake_k8s)):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/benchmarks", json=EXTERNAL_BODY)
    assert resp.status_code == 400


async def test_create_external_lm_eval_400(client_for_user, super_user, mock_db):
    async with client_for_user(super_user) as client:
        resp = await client.post("/api/benchmarks", json={**EXTERNAL_BODY, "tool": "lm_eval"})
    assert resp.status_code == 400


async def test_preview_external_returns_clone_manifests(client_for_user, super_user, mock_db):
    fake_k8s = MagicMock()
    fake_k8s.read_deployment = AsyncMock(return_value=_spec_for_api())
    with patch("app.api.benchmarks.k8s_for_cluster", AsyncMock(return_value=fake_k8s)):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/benchmarks/preview", json=EXTERNAL_BODY)
    assert resp.status_code == 200
    kinds = [m.get("kind") for m in resp.json().get("manifests", [])]
    assert "Deployment" in kinds and "Service" in kinds


async def test_create_external_api_key_override(client_for_user, super_user, mock_db):
    fake_k8s = MagicMock()
    fake_k8s.read_deployment = AsyncMock(return_value=_spec_for_api())
    fake_k8s.create_or_patch = AsyncMock()
    with patch("app.api.benchmarks.k8s_for_cluster", AsyncMock(return_value=fake_k8s)):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/benchmarks", json={**EXTERNAL_BODY, "api_key": "sk-gate"})
    assert resp.status_code == 201
    run = mock_db.add.call_args.args[0]
    assert run.serving_snapshot["api_key_override"] == "sk-gate"


async def test_create_external_snapshot_merges_command_cli(client_for_user, super_user, mock_db):
    spec = _spec(
        command=["sh", "-c", "vllm serve /models/y --served-model-name y-8b --api-key sk-live --port 9000"],
        args=[],
    )
    fake_k8s = MagicMock()
    fake_k8s.read_deployment = AsyncMock(return_value=spec)
    fake_k8s.create_or_patch = AsyncMock()
    with patch("app.api.benchmarks.k8s_for_cluster", AsyncMock(return_value=fake_k8s)):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/benchmarks", json=EXTERNAL_BODY)
    assert resp.status_code == 201, resp.text
    run = mock_db.add.call_args.args[0]
    assert run.model_name == "y-8b"
    snap = run.serving_snapshot
    # Merged+expanded CLI in the snapshot so serving_api_key can derive
    # --api-key from command-form launches too.
    assert "--api-key" in snap["vllm_extra_args"]
    assert "sk-live" in snap["vllm_extra_args"]
    assert run.params.get("tokenizer") == "/models/y"
