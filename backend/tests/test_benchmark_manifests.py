import types
import uuid

from app.services.benchmark_manifests import build_job_manifest, build_vllm_bench_job


def _run(**kw):
    base = dict(
        id=uuid.uuid4(), params={}, k8s_namespace="bench",
        tool="vllm_serving", kind="performance", model_name="m",
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def _env(manifest: dict) -> dict:
    container = manifest["spec"]["template"]["spec"]["containers"][0]
    return {e["name"]: e["value"] for e in container.get("env", [])}


def test_vllm_bench_job_passes_api_key_as_openai_key():
    # An explicit key (e.g. the user's benchmark api_key override) must reach the
    # runner as OPENAI_API_KEY so auth-gated targets accept the request.
    m = build_vllm_bench_job(
        _run(), image="img", target_base_url="http://t", api_key="sk-override", served_model="m",
    )
    assert _env(m)["OPENAI_API_KEY"] == "sk-override"


def test_accuracy_job_passes_api_key_as_bench_key():
    m = build_job_manifest(
        _run(tool="lm_eval", kind="accuracy"),
        image="img", target_base_url="http://t", api_key="sk-acc", bench_model="m",
    )
    assert _env(m)["BENCH_API_KEY"] == "sk-acc"
