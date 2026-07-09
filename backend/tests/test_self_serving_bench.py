"""Self-serving benchmark Job builder (serve + bench in one pod)."""

import types
import uuid

from app.services.benchmark_manifests import (
    build_self_serving_bench_job,
    job_name_for,
)


def _run(kind="performance", params=None):
    return types.SimpleNamespace(
        id=uuid.UUID(int=7),
        tool="vllm_serving",
        kind=kind,
        params=params or {"num_prompts": 50, "random_input_len": 128, "random_output_len": 32},
        k8s_namespace="bench",
    )


def _serving_deployment():
    # Shaped like build_deployment / build_external_clone[0] output.
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "x-deployment"},
        "spec": {
            "replicas": 1,
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "vllm",
                            "image": "vllm/vllm-openai:v0.6.0",
                            "args": ["--model", "/models/m", "--port", "8000"],
                            "ports": [{"containerPort": 8000}],
                            "resources": {"limits": {"nvidia.com/gpu": "1"}},
                            "volumeMounts": [{"name": "model-weights", "mountPath": "/models"}],
                            "env": [{"name": "HF_HOME", "value": "/models/.hf"}],
                        }
                    ],
                    "volumes": [{"name": "model-weights", "persistentVolumeClaim": {"claimName": "w"}}],
                    "nodeSelector": {"gpu-type": "H100"},
                    "tolerations": [{"key": "nvidia.com/gpu", "operator": "Exists"}],
                }
            },
        },
    }


def test_job_is_single_batch_job_named_by_run():
    job = build_self_serving_bench_job(
        _run(), serving_deployment=_serving_deployment(),
        serve_argv=["vllm", "serve", "/models/m", "--port", "8000"],
        port=8000, api_key="EMPTY", served_model="/models/m",
    )
    assert job["kind"] == "Job"
    assert job["metadata"]["name"] == job_name_for(uuid.UUID(int=7))
    assert job["spec"]["backoffLimit"] == 0
    assert job["metadata"]["labels"]["app"] == "llmops-benchmark"


def test_reuses_serving_image_gpu_mount_and_scheduling():
    job = build_self_serving_bench_job(
        _run(), serving_deployment=_serving_deployment(),
        serve_argv=["vllm", "serve", "/models/m", "--port", "8000"],
        port=8000, api_key="EMPTY", served_model="/models/m",
    )
    pod = job["spec"]["template"]["spec"]
    c = pod["containers"][0]
    assert c["image"] == "vllm/vllm-openai:v0.6.0"
    assert c["resources"] == {"limits": {"nvidia.com/gpu": "1"}}
    assert {"name": "model-weights", "mountPath": "/models"} in c["volumeMounts"]
    assert pod["volumes"][0]["persistentVolumeClaim"]["claimName"] == "w"
    assert pod["nodeSelector"] == {"gpu-type": "H100"}
    assert pod["tolerations"][0]["key"] == "nvidia.com/gpu"
    assert pod["restartPolicy"] == "Never"


def test_script_backgrounds_serve_polls_health_and_benches_localhost():
    job = build_self_serving_bench_job(
        _run(), serving_deployment=_serving_deployment(),
        serve_argv=["vllm", "serve", "/models/m", "--port", "8000"],
        port=8000, api_key="EMPTY", served_model="/models/m", tokenizer="/models/m",
    )
    script = job["spec"]["template"]["spec"]["containers"][0]["command"][2]
    # serve is backgrounded
    assert "vllm serve /models/m --port 8000 &" in script
    # readiness poll targets localhost:8000/health
    assert "http://localhost:8000/health" in script
    # bench targets the same localhost base-url and the served model
    assert "vllm bench serve" in script
    assert "http://localhost:8000" in script
    assert "--model /models/m" in script
    # RESULT marker emitted, server killed
    assert "<<<RESULT>>>" in script
    assert "kill" in script


def test_env_carries_serving_env_plus_openai_key():
    job = build_self_serving_bench_job(
        _run(), serving_deployment=_serving_deployment(),
        serve_argv=["vllm", "serve", "/models/m", "--port", "8000"],
        port=8000, api_key="sk-secret", served_model="/models/m",
    )
    env = {e["name"]: e["value"] for e in job["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["OPENAI_API_KEY"] == "sk-secret"
    assert env["HF_HOME"] == "/models/.hf"           # serving env preserved
    assert env["BENCH_RUN_ID"] == str(uuid.UUID(int=7))


def test_bench_argv_respects_params():
    job = build_self_serving_bench_job(
        _run(params={"num_prompts": 300, "random_input_len": 512, "request_rate": 8, "ignore_eos": True}),
        serving_deployment=_serving_deployment(),
        serve_argv=["vllm", "serve", "/models/m"],
        port=8000, api_key="EMPTY", served_model="srv",
    )
    script = job["spec"]["template"]["spec"]["containers"][0]["command"][2]
    assert "--num-prompts 300" in script
    assert "--random-input-len 512" in script
    assert "--request-rate 8" in script
    assert "--ignore-eos" in script
