"""Serving engine catalogue, engine_args rendering, and launch helpers."""

import types

import pytest

from app.db.models.custom_model_deployment import CustomModelDeployment
from app.db.models.custom_serving_recipe import CustomServingRecipe


def test_engine_columns_exist_on_both_tables():
    for model in (CustomServingRecipe, CustomModelDeployment):
        cols = model.__table__.columns
        assert cols["engine"].nullable is False
        assert cols["engine"].server_default.arg == "vllm"
        assert cols["engine_args"].nullable is True


from app.services.model_deployment_manifests import build_deployment
from app.services.serving_engines import (
    container_launch,
    default_image,
    engine_of,
    render_engine_args,
    serve_argv,
    validate_engine_args,
)


def _dep(**kw):
    base = dict(
        model_name="m", namespace="ns", image="img", replicas=1, gpu_count=1,
        gpu_resource_key="nvidia.com/gpu", cpu_request=None, cpu_limit=None,
        memory_request=None, memory_limit=None, node_selector=None, tolerations=None,
        pvc_name=None, pvc_mount_path=None, model_path="/models/m", vllm_extra_args=None,
        env=None, ingress_host="h", ingress_path="/", ingress_class="nginx",
        engine="vllm", engine_args=None,
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def _container(manifest: dict) -> dict:
    return manifest["spec"]["template"]["spec"]["containers"][0]


def test_render_engine_args_sorted_and_typed():
    args = {
        "tp-size": 4, "dtype": "bfloat16", "trust-remote-code": True,
        "disable-radix-cache": False, "served-model-name": "", "mem-fraction-static": 0.88,
    }
    assert render_engine_args(args) == [
        "--dtype", "bfloat16", "--mem-fraction-static", "0.88", "--tp-size", "4", "--trust-remote-code",
    ]


def test_render_engine_args_empty():
    assert render_engine_args(None) == []
    assert render_engine_args({}) == []
    assert render_engine_args({"max-num-seqs": 0}) == ["--max-num-seqs", "0"]


def test_engine_of_defaults_to_vllm():
    assert engine_of(types.SimpleNamespace()) == "vllm"
    assert engine_of(types.SimpleNamespace(engine=None)) == "vllm"
    assert engine_of(types.SimpleNamespace(engine="sglang")) == "sglang"


def test_default_image_per_engine():
    assert default_image("vllm") == "vllm/vllm-openai:latest"
    assert default_image("sglang") == "lmsysorg/sglang:latest"


def test_validate_engine_args():
    assert validate_engine_args(None) is None
    assert validate_engine_args({"tp-size": 4, "dtype": "auto", "x": True, "f": 0.5}) == {
        "tp-size": 4, "dtype": "auto", "x": True, "f": 0.5,
    }
    for bad in ({"--tp-size": 4}, {"TP": 4}, {"a b": 1}, {"-x": 1}):
        with pytest.raises(ValueError):
            validate_engine_args(bad)
    with pytest.raises(ValueError):
        validate_engine_args({"tp-size": [4]})


def test_vllm_container_matches_legacy_args():
    c = _container(build_deployment(_dep(vllm_extra_args=["--max-model-len", "8192"])))
    assert c["name"] == "vllm"
    assert "command" not in c
    assert c["args"] == ["--model", "/models/m", "--port", "8000", "--max-model-len", "8192"]


def test_vllm_container_engine_args_before_extra_args():
    c = _container(build_deployment(_dep(engine_args={"tensor-parallel-size": 2}, vllm_extra_args=["--x"])))
    assert c["args"] == ["--model", "/models/m", "--port", "8000", "--tensor-parallel-size", "2", "--x"]


def test_sglang_container_sets_command_and_model_path():
    c = _container(build_deployment(_dep(engine="sglang", engine_args={"tp-size": 2}, vllm_extra_args=["--log-level", "info"])))
    assert c["name"] == "sglang"
    assert c["command"] == ["python3", "-m", "sglang.launch_server"]
    assert c["args"] == [
        "--model-path", "/models/m", "--host", "0.0.0.0", "--port", "8000",
        "--tp-size", "2", "--log-level", "info",
    ]
    assert c["readinessProbe"]["httpGet"] == {"path": "/health", "port": 8000}


def test_container_launch_in_memory_row_without_engine_is_vllm():
    dep = _dep()
    del dep.engine
    del dep.engine_args
    command, args = container_launch(dep)
    assert command is None
    assert args == ["--model", "/models/m", "--port", "8000"]


def test_serve_argv_vllm_matches_legacy():
    assert serve_argv(_dep(vllm_extra_args=["--a"]), 8000) == ["vllm", "serve", "/models/m", "--port", "8000", "--a"]


def test_serve_argv_sglang():
    assert serve_argv(_dep(engine="sglang", engine_args={"tp-size": 2}), 8000) == [
        "python3", "-m", "sglang.launch_server", "--model-path", "/models/m",
        "--host", "0.0.0.0", "--port", "8000", "--tp-size", "2",
    ]
