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


def _script(manifest: dict) -> str:
    return manifest["spec"]["template"]["spec"]["containers"][0]["command"][2]


def test_vllm_bench_job_appends_extra_args_verbatim():
    """`extra_args` is a raw CLI string (vllm bench serve flags) — bare flags
    have no value, so the JSON key/value passthrough can't express them."""
    m = build_vllm_bench_job(
        _run(params={"extra_args": "--disable-tqdm --burstiness 0.5"}),
        image="img", target_base_url="http://t", api_key="k", served_model="m",
    )
    script = _script(m)
    assert "--disable-tqdm" in script
    assert "--burstiness 0.5" in script
    assert "--extra-args" not in script  # must not fall through the key/value path


def test_vllm_bench_job_extra_args_tokens_are_quoted():
    m = build_vllm_bench_job(
        _run(params={"extra_args": '--served-model-name "my model"'}),
        image="img", target_base_url="http://t", api_key="k", served_model="m",
    )
    assert "'my model'" in _script(m)  # quoted as ONE argv token, not shell-split


def test_seed_param_overrides_default():
    m = build_vllm_bench_job(
        _run(params={"seed": 42}),
        image="img", target_base_url="http://t", api_key="", served_model="m",
    )
    script = _script(m)
    assert "--seed 42" in script
    assert "--seed 0" not in script


def test_seed_defaults_to_zero_when_absent():
    m = build_vllm_bench_job(
        _run(), image="img", target_base_url="http://t", api_key="", served_model="m",
    )
    assert "--seed 0" in _script(m)


def test_goodput_param_emits_space_separated_pairs():
    m = build_vllm_bench_job(
        _run(params={"goodput": "ttft:200 tpot:50"}),
        image="img", target_base_url="http://t", api_key="", served_model="m",
    )
    assert "--goodput ttft:200 tpot:50" in _script(m)
