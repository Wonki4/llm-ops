"""Prefill/decode serving: schema, role merge, kv-transfer flags, manifests, runtime, status."""

import json
import types

import pytest

from app.db.models.custom_model_deployment import CustomModelDeployment
from app.db.models.custom_serving_recipe import CustomServingRecipe
from app.services import pd_serving
from app.services.benchmark_serving import build_ephemeral_deployment
from app.services.model_deployment_manifests import (
    build_all,
    build_deployment,
    build_pd_deployments,
    build_pd_services,
    k8s_resource_names,
)
from app.services.pd_serving import (
    derive_pd_status,
    kv_transfer_config,
    role_view,
    validate_pd_config,
    validate_runtime,
)
from app.services.serving_probes import render_probes, validate_probes


def _dep(**kw):
    base = dict(
        id="00000000-0000-0000-0000-000000000001",
        model_name="glm",
        namespace="ns",
        image="vllm/vllm-openai:nightly",
        replicas=1,
        gpu_count=8,
        gpu_resource_key="nvidia.com/gpu",
        cpu_request=None,
        cpu_limit=None,
        memory_request=None,
        memory_limit=None,
        node_selector={"gpu-type": "h200"},
        tolerations=None,
        pvc_name="weights",
        pvc_mount_path="/models",
        model_path="zai-org/GLM-5.3-Flash",
        vllm_extra_args=None,
        env={"VLLM_KV_CACHE_LAYOUT": "HND"},
        engine="vllm",
        engine_args={"tensor-parallel-size": 8, "kv-cache-dtype": "fp8", "reasoning-parser": "glm47"},
        probes=None,
        gpu_type="h200",
        serving_mode="pd",
        runtime=None,
        pd_config={
            "prefill": {
                "replicas": 2,
                "gpu_count": 8,
                "engine_args": {"compilation-config": '{"cudagraph_mm_encoder": true}'},
            },
            "decode": {
                "replicas": 1,
                "engine_args": {"compilation-config": '{"cudagraph_mode":"FULL_DECODE_ONLY"}', "max-num-seqs": 512},
                "vllm_extra_args": ["--enable-auto-tool-choice"],
                "env": {"DECODE_ONLY": "1"},
            },
            "nixl_port": 5600,
            "kv_transfer_extra": {"kv_load_failure_policy": "fail"},
        },
        ingress_host="x",
        ingress_path="/",
        ingress_class="nginx",
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


# ─── schema ───────────────────────────────────────────────────────────────────


def test_schema_columns():
    for model in (CustomServingRecipe, CustomModelDeployment):
        cols = model.__table__.columns
        assert cols["serving_mode"].server_default.arg == "aggregated"
        assert cols["pd_config"].nullable and cols["runtime"].nullable
    cols = CustomModelDeployment.__table__.columns
    assert cols["pd_status"].nullable
    assert cols["router_stack_created"].server_default.arg == "false"
    fk = next(iter(cols["router_stack_id"].foreign_keys))
    assert fk.ondelete == "SET NULL" and fk.column.table.name == "custom_llmd_stack"


def test_validate_pd_config_defaults_and_errors():
    out = validate_pd_config(None, engine="vllm", base_extra_args=None)
    assert out["prefill"]["replicas"] == 1 and out["decode"]["replicas"] == 1
    assert out["nixl_port"] == 5600 and out["router"]["peak_prefill_throughput"] == 33821
    with pytest.raises(ValueError, match="requires engine 'vllm'"):
        validate_pd_config({}, engine="sglang", base_extra_args=None)
    with pytest.raises(ValueError, match="--kv-transfer-config"):
        validate_pd_config({}, engine="vllm", base_extra_args=["--kv-transfer-config={}"])
    with pytest.raises(ValueError, match="--port"):
        validate_pd_config({"decode": {"vllm_extra_args": ["--port", "9000"]}}, engine="vllm", base_extra_args=None)
    with pytest.raises(ValueError):
        validate_pd_config({"nixl_port": 80}, engine="vllm", base_extra_args=None)


def test_validate_runtime():
    assert validate_runtime(None) is None
    assert validate_runtime({"host_ipc": False, "privileged": False}) is None
    assert validate_runtime({"shm_size_gi": 20, "privileged": True, "extra_resources": {"rdma/ib": "1"}}) == {
        "shm_size_gi": 20,
        "privileged": True,
        "extra_resources": {"rdma/ib": "1"},
    }
    with pytest.raises(ValueError):
        validate_runtime({"extra_resources": {"ib": "1"}})


def test_startup_probe_schema():
    assert validate_probes({"startup": {"period_seconds": 30}}) == {"startup": {"period_seconds": 30}}
    dep = _dep(serving_mode="aggregated", probes=None)
    assert "startupProbe" not in render_probes(dep)
    dep = _dep(serving_mode="aggregated", probes={"startup": {}})
    assert render_probes(dep)["startupProbe"]["failureThreshold"] == 120


# ─── merge + flags ────────────────────────────────────────────────────────────


def test_role_view_merges_base_and_role():
    d = role_view(_dep(), "decode")
    assert d.port == 8200 and d.replicas == 1 and d.gpu_count == 8 and d.gpu_type == "h200"
    assert d.engine_args == {
        "tensor-parallel-size": 8,
        "kv-cache-dtype": "fp8",
        "reasoning-parser": "glm47",
        "compilation-config": '{"cudagraph_mode":"FULL_DECODE_ONLY"}',
        "max-num-seqs": 512,
    }
    assert d.extra_args == ["--enable-auto-tool-choice"]
    assert d.env == {"VLLM_KV_CACHE_LAYOUT": "HND", "DECODE_ONLY": "1"}
    p = role_view(_dep(), "prefill")
    assert p.port == 8000 and p.replicas == 2
    assert p.engine_args["compilation-config"] == '{"cudagraph_mm_encoder": true}'
    assert "max-num-seqs" not in p.engine_args and p.extra_args == []


def test_kv_transfer_config_roles_and_extras():
    assert json.loads(kv_transfer_config("prefill", None)) == {
        "kv_connector": "NixlConnector",
        "kv_role": "kv_producer",
    }
    out = json.loads(
        kv_transfer_config("decode", {"kv_role": "kv_producer", "kv_connector": "X", "kv_load_failure_policy": "fail"})
    )
    assert out == {"kv_connector": "NixlConnector", "kv_role": "kv_consumer", "kv_load_failure_policy": "fail"}


# ─── manifests ────────────────────────────────────────────────────────────────


def _by_name(manifests):
    return {m["metadata"]["name"]: m for m in manifests}


def test_pd_deployments_render_roles():
    deps = build_pd_deployments(_dep(), sidecar_image="ghcr.io/llm-d/llm-d-router-disagg-sidecar:v0.9.0")
    m = _by_name(deps)
    assert set(m) == {"glm-prefill-deployment", "glm-decode-deployment"}

    pre = m["glm-prefill-deployment"]
    pod = pre["spec"]["template"]["spec"]
    c = pod["containers"][0]
    assert pre["spec"]["replicas"] == 2
    assert pre["spec"]["selector"]["matchLabels"] == {
        "llm-ops/managed-by": "litellm-portal",
        "llm-ops/model-name": "glm",
        "llm-ops/pd-role": "prefill",
    }
    assert pre["spec"]["template"]["metadata"]["labels"]["llm-d.ai/role"] == "prefill"
    assert pre["spec"]["template"]["metadata"]["labels"]["llm-d.ai/model"] == "glm"
    assert c["args"][:6] == [
        "--model",
        "zai-org/GLM-5.3-Flash",
        "--port",
        "8000",
        "--kv-transfer-config",
        '{"kv_connector":"NixlConnector","kv_role":"kv_producer","kv_load_failure_policy":"fail"}',
    ]
    assert "--compilation-config" in c["args"] and '{"cudagraph_mm_encoder": true}' in c["args"]
    assert c["ports"] == [
        {"containerPort": 8000, "name": "http"},
        {"containerPort": 5600, "name": "nixl", "protocol": "TCP"},
    ]
    env = {e["name"]: e for e in c["env"]}
    assert env["VLLM_NIXL_SIDE_CHANNEL_HOST"] == {
        "name": "VLLM_NIXL_SIDE_CHANNEL_HOST",
        "valueFrom": {"fieldRef": {"fieldPath": "status.podIP"}},
    }
    assert env["VLLM_NIXL_SIDE_CHANNEL_PORT"]["value"] == "5600" and env["VLLM_KV_CACHE_LAYOUT"]["value"] == "HND"
    assert c["resources"]["limits"]["nvidia.com/gpu"] == "8"
    assert c["readinessProbe"]["httpGet"]["port"] == 8000 and c["startupProbe"]["failureThreshold"] == 120
    assert "initContainers" not in pod
    assert pod["nodeSelector"] == {"gpu-type": "h200"}
    assert {v["name"] for v in pod["volumes"]} == {"model-weights"}

    dec = m["glm-decode-deployment"]
    pod = dec["spec"]["template"]["spec"]
    c = pod["containers"][0]
    assert dec["spec"]["replicas"] == 1
    assert c["args"][2:4] == ["--port", "8200"]
    assert '"kv_role":"kv_consumer"' in c["args"][5]
    assert "--max-num-seqs" in c["args"] and c["args"][-1] == "--enable-auto-tool-choice"
    assert c["ports"][0] == {"containerPort": 8200, "name": "http"}
    assert c["readinessProbe"]["httpGet"]["port"] == 8200
    assert {e["name"]: e.get("value") for e in c["env"]}["DECODE_ONLY"] == "1"
    sidecar = pod["initContainers"][0]
    assert sidecar["name"] == "routing-proxy" and sidecar["image"].endswith(":v0.9.0")
    assert sidecar["args"] == ["--port=8000", "--kv-connector=nixlv2", "--zap-log-level=1", "--secure-proxy=false"]
    assert sidecar["restartPolicy"] == "Always" and sidecar["ports"][0]["containerPort"] == 8000


def test_sidecar_image_precedence():
    dep = _dep()
    dep.pd_config = {**dep.pd_config, "sidecar_image": "internal/sidecar:1"}
    dec = _by_name(build_pd_deployments(dep, sidecar_image="ghcr.io/x:y"))["glm-decode-deployment"]
    assert dec["spec"]["template"]["spec"]["initContainers"][0]["image"] == "internal/sidecar:1"
    dep.pd_config = {k: v for k, v in dep.pd_config.items() if k != "sidecar_image"}
    dec = _by_name(build_pd_deployments(dep))["glm-decode-deployment"]
    assert dec["spec"]["template"]["spec"]["initContainers"][0]["image"] == pd_serving.DEFAULT_SIDECAR_IMAGE


def test_pd_services_and_build_all_have_no_ingress():
    svcs = _by_name(build_pd_services(_dep()))
    assert set(svcs) == {"glm-prefill-service", "glm-decode-service"}
    assert svcs["glm-decode-service"]["spec"]["selector"]["llm-ops/pd-role"] == "decode"
    assert svcs["glm-decode-service"]["spec"]["ports"][0]["targetPort"] == 8000
    kinds = [m["kind"] for m in build_all(_dep())]
    assert kinds == ["Deployment", "Deployment", "Service", "Service"]
    assert k8s_resource_names(_dep()) == {
        "prefill_deployment": "glm-prefill-deployment",
        "decode_deployment": "glm-decode-deployment",
        "prefill_service": "glm-prefill-service",
        "decode_service": "glm-decode-service",
    }


def test_pd_requires_vllm():
    with pytest.raises(ValueError):
        build_pd_deployments(_dep(engine="sglang"))


# ─── runtime (both modes) ─────────────────────────────────────────────────────


def test_runtime_renders_shm_hostipc_privileged_extra_resources():
    dep = _dep(
        serving_mode="aggregated",
        runtime={"shm_size_gi": 20, "host_ipc": True, "privileged": True, "extra_resources": {"rdma/ib": "1"}},
    )
    pod = build_deployment(dep)["spec"]["template"]["spec"]
    c = pod["containers"][0]
    assert pod["hostIPC"] is True
    assert c["securityContext"] == {"privileged": True}
    assert {"name": "shm", "emptyDir": {"medium": "Memory", "sizeLimit": "20Gi"}} in pod["volumes"]
    assert {"name": "shm", "mountPath": "/dev/shm"} in c["volumeMounts"]
    assert c["resources"]["limits"]["rdma/ib"] == "1" and c["resources"]["requests"]["rdma/ib"] == "1"


def test_aggregated_output_unchanged_without_runtime():
    dep = _dep(
        serving_mode="aggregated",
        runtime=None,
        pd_config=None,
        node_selector=None,
        pvc_name=None,
        pvc_mount_path=None,
        env=None,
        engine_args=None,
        gpu_count=0,
    )
    m = build_deployment(dep)
    pod = m["spec"]["template"]["spec"]
    assert list(pod.keys()) == ["containers", "volumes"]
    c = pod["containers"][0]
    assert list(c.keys()) == ["name", "image", "args", "ports", "resources", "env", "volumeMounts", "readinessProbe"]
    assert c["resources"] == {} and "securityContext" not in c and "hostIPC" not in pod
    assert m["spec"]["selector"]["matchLabels"] == {"llm-ops/managed-by": "litellm-portal", "llm-ops/model-name": "glm"}
    assert k8s_resource_names(dep) == {
        "deployment": "glm-deployment",
        "service": "glm-service",
        "ingress": "glm-ingress",
    }


def test_benchmark_clone_refuses_pd():
    base = CustomModelDeployment(
        model_name="b",
        namespace="ns",
        image="img",
        replicas=1,
        gpu_count=1,
        gpu_resource_key="nvidia.com/gpu",
        model_path="/w/m",
        ingress_host="x",
        ingress_path="/",
        ingress_class="nginx",
        engine="vllm",
        serving_mode="pd",
    )
    with pytest.raises(ValueError, match="P/D"):
        build_ephemeral_deployment(base, name="e", namespace="ns", overrides=None)


# ─── status derivation ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "prefill,decode,expected",
    [
        (("Ready", None), ("Ready", None), ("Ready", None)),
        (("Ready", None), ("Pending", "No ready pods yet"), ("Pending", "decode: No ready pods yet")),
        (("Updating", "1/2 pods ready"), ("Ready", None), ("Updating", "prefill: 1/2 pods ready")),
        (
            ("Failed", "Deployment progress deadline exceeded"),
            ("Ready", None),
            ("Failed", "prefill: Deployment progress deadline exceeded"),
        ),
        (
            ("Ready", None),
            ("Unhealthy", "ReplicaFailure condition true"),
            ("Unhealthy", "decode: ReplicaFailure condition true"),
        ),
        (("Ready", None), None, ("Unhealthy", "decode: K8s Deployment not found")),
        (("Stopped", "replicas set to 0"), ("Stopped", "replicas set to 0"), ("Stopped", "replicas set to 0")),
    ],
)
def test_derive_pd_status(prefill, decode, expected):
    assert derive_pd_status(prefill, decode) == expected


def test_pd_status_summary():
    status = {"prefill": {"ready": 7, "desired": 8}, "decode": {"ready": 2, "desired": 2}}
    assert pd_serving.pd_status_summary(status) == "D 2/2 · P 7/8"
    assert pd_serving.pd_status_summary(None) is None


def test_role_cpu_memory_override_the_base():
    dep = _dep(
        cpu_request="4",
        memory_limit="32Gi",
        pd_config={"prefill": {"replicas": 1, "cpu_request": "16", "memory_limit": "128Gi"}, "decode": {"replicas": 1}},
    )
    prefill, decode = build_pd_deployments(dep)
    p = prefill["spec"]["template"]["spec"]["containers"][0]["resources"]
    d = decode["spec"]["template"]["spec"]["containers"][0]["resources"]
    assert p["requests"]["cpu"] == "16" and p["limits"]["memory"] == "128Gi"
    assert d["requests"]["cpu"] == "4" and d["limits"]["memory"] == "32Gi"


def test_router_block_strips_blanks_and_validates_epp_replicas():
    out = validate_pd_config(
        {"router": {"epp_registry": " ", "epp_tag": "v0.9.1", "epp_replicas": 2, "ingress_class": ""}},
        engine="vllm",
        base_extra_args=None,
    )
    assert out["router"]["epp_tag"] == "v0.9.1" and out["router"]["epp_replicas"] == 2
    assert "epp_registry" not in out["router"] and "ingress_class" not in out["router"]
    with pytest.raises(ValueError):
        validate_pd_config({"router": {"epp_replicas": 0}}, engine="vllm", base_extra_args=None)


def test_each_pool_renders_its_own_image_model_storage_probes_runtime_placement():
    dep = _dep(
        image="base:img",
        model_path="/models/base",
        pvc_name="base-pvc",
        pvc_mount_path="/models",
        node_selector={"gpu-type": "h200"},
        tolerations=None,
        probes=None,
        runtime=None,
        pd_config={
            "prefill": {
                "replicas": 1,
                "image": "pre:img",
                "model_path": "/models/pre",
                "gpu_resource_key": "amd.com/gpu",
                "pvc_name": "pre-pvc",
                "pvc_mount_path": "/pre",
                "probes": {"readiness": {"path": "/ready"}},
                "runtime": {"shm_size_gi": 4, "host_ipc": True},
                "node_selector": {"gpu-type": "mi300"},
                "tolerations": [{"key": "amd", "operator": "Exists"}],
            },
            "decode": {"replicas": 1},
        },
    )
    prefill, decode = build_pd_deployments(dep)
    pre = prefill["spec"]["template"]["spec"]
    dec = decode["spec"]["template"]["spec"]
    pc, dc = pre["containers"][0], dec["containers"][0]
    assert pc["image"] == "pre:img" and dc["image"] == "base:img"
    assert pc["args"][1] == "/models/pre" and dc["args"][1] == "/models/base"
    assert "amd.com/gpu" in pc["resources"]["limits"] and "nvidia.com/gpu" in dc["resources"]["limits"]
    assert pre["volumes"][0]["persistentVolumeClaim"]["claimName"] == "pre-pvc"
    assert pre["volumes"][1]["emptyDir"]["sizeLimit"] == "4Gi" and pre["hostIPC"] is True
    assert dec["volumes"][0]["persistentVolumeClaim"]["claimName"] == "base-pvc" and "hostIPC" not in dec
    assert pc["readinessProbe"]["httpGet"]["path"] == "/ready" and dc["readinessProbe"]["httpGet"]["path"] == "/health"
    assert pre["nodeSelector"] == {"gpu-type": "mi300"} and pre["tolerations"] == [{"key": "amd", "operator": "Exists"}]
    assert dec["nodeSelector"] == {"gpu-type": "h200"} and "tolerations" not in dec


def test_pool_probes_and_runtime_are_validated():
    with pytest.raises(ValueError):
        validate_pd_config(
            {"prefill": {"probes": {"readiness": {"path": "no-slash"}}}}, engine="vllm", base_extra_args=None
        )
    with pytest.raises(ValueError):
        validate_pd_config({"decode": {"runtime": {"shm_size_gi": 0}}}, engine="vllm", base_extra_args=None)
    out = validate_pd_config({"prefill": {"image": " ", "model_path": "/m"}}, engine="vllm", base_extra_args=None)
    assert "image" not in out["prefill"] and out["prefill"]["model_path"] == "/m"
