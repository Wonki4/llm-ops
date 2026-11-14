import json
import pytest
import types
import uuid

from app.services.benchmark_manifests import build_job_manifest, build_performance_job


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
    m = build_performance_job(
        _run(), image="img", target_base_url="http://t", api_key="sk-override", served_model="m",
    )
    assert _env(m)["OPENAI_API_KEY"] == "sk-override"


def test_accuracy_job_passes_api_key_as_bench_key():
    m = build_job_manifest(
        _run(tool="lm_eval", kind="accuracy"),
        image="img", target_base_url="http://t", api_key="sk-acc", bench_model="m",
    )
    assert _env(m)["BENCH_API_KEY"] == "sk-acc"


def test_job_uses_structured_workload_and_separate_model_alias():
    m = build_performance_job(_run(params={"random_input_len": 256, "seed": 42, "preset": "chat"}),
                              image="engine", target_base_url="http://proxy/prefix/v1",
                              api_key="secret", served_model="my alias", tokenizer="/models/tokenizer")
    env = _env(m)
    workload = json.loads(env["BENCH_WORKLOAD"])
    assert (env["BENCH_URL"], env["BENCH_MODEL"], env["BENCH_TOKENIZER"]) == (
        "http://proxy/prefix/v1", "my alias", "/models/tokenizer")
    assert workload["random_input_len"] == 256 and workload["seed"] == 42 and "preset" not in workload
    command = m["spec"]["template"]["spec"]["containers"][0]["command"]
    assert command[:2] == ["python", "-c"] and "vllm bench serve" not in command[-1]
    assert "secret" not in command[-1]


@pytest.mark.parametrize("params", [{"extra_args": "--backend openai"}, {"burstiness": .5},
                                    {"goodput": "slo:5"}, {"max_concurrency": 0}, {"request_rate": -1}])
def test_runner_rejects_unsupported_or_invalid_options(params):
    with pytest.raises(ValueError):
        build_performance_job(_run(params=params), image="i", target_base_url="http://t", api_key="", served_model="m")
