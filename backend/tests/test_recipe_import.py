"""Reverse parser: live Deployment spec → recipe draft + warnings."""

import types

from app.services.model_deployment_manifests import build_deployment
from app.services.recipe_import import build_recipe_draft, parse_flags


def _codes(result):
    return [w["code"] for w in result["warnings"]]


def _spec(command=None, args=None, image="vllm/vllm-openai:v0.9.0", **extra):
    container = {
        "name": "server", "image": image, "command": list(command or []), "args": list(args or []),
        "env": [], "env_raw": [], "resources": {}, "ports": [{"containerPort": 8000}], "volume_mounts": [],
    }
    container.update(extra.pop("container", {}))
    spec = {"name": "my-llm", "namespace": "ml", "labels": {}, "replicas": 2, "container": container,
            "volumes": [], "node_selector": None, "tolerations": None}
    spec.update(extra)
    return spec


def _manifest_to_spec(manifest: dict) -> dict:
    """Shape a built Deployment manifest like K8sClient.read_deployment output."""
    pod = manifest["spec"]["template"]["spec"]
    c = pod["containers"][0]
    return {
        "name": manifest["metadata"]["name"], "namespace": manifest["metadata"]["namespace"],
        "labels": manifest["metadata"]["labels"], "replicas": manifest["spec"]["replicas"],
        "container": {
            "name": c["name"], "image": c["image"], "command": c.get("command") or [], "args": c["args"],
            "env": c["env"], "env_raw": c["env"], "resources": c["resources"], "ports": c["ports"],
            "volume_mounts": c["volumeMounts"],
        },
        "volumes": pod["volumes"], "node_selector": pod.get("nodeSelector"), "tolerations": pod.get("tolerations"),
    }


def _dep(**kw):
    base = dict(
        id="00000000-0000-0000-0000-000000000001", model_name="qwen", namespace="ml", image="vllm/vllm-openai:v0.9.0",
        replicas=1, gpu_count=2, gpu_resource_key="nvidia.com/gpu", cpu_request="4", cpu_limit="8",
        memory_request="32Gi", memory_limit="64Gi", node_selector={"gpu": "a100"},
        tolerations=[{"key": "gpu", "operator": "Exists", "effect": "NoSchedule"}],
        pvc_name="weights", pvc_mount_path="/models", model_path="/models/Qwen3-32B",
        vllm_extra_args=None, env={"HF_HOME": "/models/.cache"}, engine="vllm",
        engine_args={"tensor-parallel-size": 2, "max-model-len": 8192, "dtype": "bfloat16", "trust-remote-code": True},
        ingress_host="x", ingress_path="/", ingress_class="nginx",
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


# ─── Round trip with the forward builder ─────────────────────────────────────


def test_portal_vllm_manifest_round_trips_without_warnings():
    dep = _dep()
    out = build_recipe_draft(_manifest_to_spec(build_deployment(dep)))
    d = out["draft"]
    assert out["warnings"] == []
    assert d["engine"] == "vllm" and d["image"] == dep.image and d["model_path"] == dep.model_path
    assert d["engine_args"] == dep.engine_args
    assert d["vllm_extra_args"] is None
    assert (d["gpu_count"], d["gpu_resource_key"]) == (2, "nvidia.com/gpu")
    assert (d["cpu_request"], d["cpu_limit"], d["memory_request"], d["memory_limit"]) == ("4", "8", "32Gi", "64Gi")
    assert d["env"] == dep.env and d["node_selector"] == dep.node_selector and d["tolerations"] == dep.tolerations
    assert (d["pvc_name"], d["pvc_mount_path"]) == ("weights", "/models")
    assert d["name"] == "qwen-deployment"  # the K8s object name; the UI picks a friendlier default


def test_portal_sglang_manifest_round_trips():
    dep = _dep(engine="sglang", image="lmsysorg/sglang:v0.4", engine_args={"tp-size": 2, "context-length": 4096},
               gpu_count=0, cpu_request=None, cpu_limit=None, memory_request=None, memory_limit=None,
               pvc_name=None, pvc_mount_path=None, env=None, node_selector=None, tolerations=None)
    out = build_recipe_draft(_manifest_to_spec(build_deployment(dep)))
    d = out["draft"]
    assert out["warnings"] == []
    assert d["engine"] == "sglang" and d["model_path"] == dep.model_path
    assert d["engine_args"] == {"tp-size": 2, "context-length": 4096}
    assert d["gpu_count"] == 0 and d["pvc_name"] is None and d["env"] is None


def test_extra_args_from_portal_manifest_land_in_engine_args():
    # The forward builder appends free-text args after structured ones; on the
    # way back they are indistinguishable, so they all become engine_args (the
    # UI then moves unknown keys to the free-text box).
    dep = _dep(engine_args=None, vllm_extra_args=["--max-num-seqs=64", "--enable-prefix-caching"])
    d = build_recipe_draft(_manifest_to_spec(build_deployment(dep)))["draft"]
    assert d["engine_args"] == {"max-num-seqs": 64, "enable-prefix-caching": True}


# ─── Launch forms ────────────────────────────────────────────────────────────


def test_vllm_serve_positional_model():
    out = build_recipe_draft(
        _spec(command=["vllm", "serve", "meta-llama/Llama-3-70B"], args=["--tensor-parallel-size", "4"])
    )
    assert out["draft"]["engine"] == "vllm"
    assert out["draft"]["model_path"] == "meta-llama/Llama-3-70B"
    assert out["draft"]["engine_args"] == {"tensor-parallel-size": 4}
    assert out["warnings"] == []


def test_python_module_launch_with_underscored_and_inline_flags():
    out = build_recipe_draft(_spec(
        command=["python3", "-m", "vllm.entrypoints.openai.api_server"],
        args=["--model=/w/llama", "--tensor_parallel_size", "2", "--gpu-memory-utilization=0.95"],
    ))
    d = out["draft"]
    assert d["model_path"] == "/w/llama"
    assert d["engine_args"] == {"tensor-parallel-size": 2, "gpu-memory-utilization": 0.95}
    assert _codes(out) == ["flag_normalized"]
    assert out["warnings"][0]["detail"] == "--tensor_parallel_size → --tensor-parallel-size"


def test_sglang_launch_detected_from_command_not_image():
    out = build_recipe_draft(_spec(
        command=["python", "-m", "sglang.launch_server"],
        args=["--model-path", "Qwen/Qwen3-32B", "--tp", "4", "--host", "0.0.0.0", "--port", "30000"],
        image="my-registry/custom-runtime:1",
    ))
    d = out["draft"]
    assert d["engine"] == "sglang" and d["model_path"] == "Qwen/Qwen3-32B"
    assert d["engine_args"] == {"tp": 4}
    assert "host" not in (d["engine_args"] or {})
    assert {"code": "port_changed", "detail": "30000 → 8000"} in out["warnings"]


def test_entrypoint_image_without_command_uses_image_name_for_engine():
    out = build_recipe_draft(_spec(args=["--model", "/w/m"], image="ghcr.io/org/sglang-fork:latest"))
    assert out["draft"]["engine"] == "sglang"
    # sglang wants --model-path; the vLLM-style --model is renamed and flagged.
    assert out["draft"]["model_path"] == "/w/m"
    assert _codes(out) == ["flag_normalized"]


def test_unknown_image_and_no_command_falls_back_to_vllm_with_warning():
    out = build_recipe_draft(_spec(args=["--model", "/w/m"], image="internal/serve:1"))
    assert out["draft"]["engine"] == "vllm"
    assert _codes(out) == ["engine_from_default"]


def test_shell_wrapper_takes_last_segment_and_drops_inline_env():
    out = build_recipe_draft(_spec(
        command=["/bin/sh", "-c"],
        args=["pip install -q flashinfer && exec HF_TOKEN=abc python -m sglang.launch_server --model-path /w/m --tp 2"],
        image="lmsysorg/sglang:latest",
    ))
    d = out["draft"]
    assert d["engine"] == "sglang" and d["model_path"] == "/w/m" and d["engine_args"] == {"tp": 2}
    codes = _codes(out)
    assert "shell_wrapper" in codes and "shell_script_truncated" in codes and "inline_env_dropped" in codes
    assert next(w for w in out["warnings"] if w["code"] == "inline_env_dropped")["detail"] == "HF_TOKEN=abc"


def test_custom_wrapper_program_keeps_flags_and_warns():
    out = build_recipe_draft(_spec(command=["/app/start.sh"], args=["--model", "/w/m", "--dtype", "half"]))
    assert out["draft"]["model_path"] == "/w/m" and out["draft"]["engine_args"] == {"dtype": "half"}
    assert _codes(out) == ["command_unrecognized"]


def test_missing_model_path_is_reported_not_guessed():
    out = build_recipe_draft(_spec(args=["--dtype", "auto"]))
    assert out["draft"]["model_path"] == ""
    assert "model_path_missing" in _codes(out)


def test_no_launch_information_at_all():
    out = build_recipe_draft(_spec(image=""))
    assert out["draft"]["engine"] == "vllm" and out["draft"]["model_path"] == ""
    assert {"engine_from_default", "model_path_missing", "image_missing"} <= set(_codes(out))


# ─── parse_flags edge cases ──────────────────────────────────────────────────


def test_parse_flags_bool_negative_duplicate_short_and_terminator():
    warnings: list[dict] = []
    args, extra, pos = parse_flags(
        ["--trust-remote-code", "--max-model-len", "-1", "--seed", "1", "--seed", "2", "-tp", "4", "--enforce-eager",
         "--", "--not-a-flag"],
        warnings,
    )
    assert args == {"trust-remote-code": True, "max-model-len": -1, "seed": 2, "enforce-eager": True}
    assert extra == ["-tp 4"]
    assert pos == ["--not-a-flag"]
    assert [w["code"] for w in warnings] == ["duplicate_flag", "short_flag"]


def test_parse_flags_keeps_non_numeric_strings_and_scientific_floats():
    args, _, _ = parse_flags(["--dtype", "bfloat16", "--lr", "1e-4", "--name", "007x", "--zero", "0"], [])
    assert args == {"dtype": "bfloat16", "lr": 1e-4, "name": "007x", "zero": 0}


def test_positional_leftovers_are_warned_and_dropped():
    out = build_recipe_draft(_spec(command=["vllm", "serve", "/w/m"], args=["stray", "--dtype", "auto"]))
    assert out["draft"]["model_path"] == "/w/m"
    assert {"code": "positional_dropped", "detail": "stray"} in out["warnings"]


# ─── Pod spec pieces ─────────────────────────────────────────────────────────


def test_resources_extended_key_env_value_from_and_volumes():
    out = build_recipe_draft(_spec(
        args=["--model", "/w/m"],
        container={
            "resources": {
                "limits": {"amd.com/gpu": "4", "memory": "128Gi", "cpu": "16", "hugepages-2Mi": "1Gi"},
                "requests": {"memory": "96Gi", "cpu": "8", "amd.com/gpu": "4"},
            },
            "env_raw": [
                {"name": "HF_HOME", "value": "/w/.cache"},
                {"name": "HF_TOKEN", "valueFrom": {"secretKeyRef": {"name": "hf", "key": "token"}}},
                {"name": "EMPTY", "value": None},
            ],
            "volume_mounts": [
                {"name": "weights", "mountPath": "/w", "subPath": "llama"},
                {"name": "shm", "mountPath": "/dev/shm"},
                {"name": "cfg", "mountPath": "/etc/cfg"},
            ],
        },
        volumes=[
            {"name": "weights", "persistentVolumeClaim": {"claimName": "model-pvc"}},
            {"name": "shm", "emptyDir": {"medium": "Memory"}},
            {"name": "cfg", "configMap": {"name": "serve-cfg"}},
            {"name": "scratch", "hostPath": {"path": "/mnt/x"}},
        ],
        node_selector={"kubernetes.io/arch": "amd64"},
        tolerations=[{"key": "gpu", "operator": "Exists"}],
    ))
    d = out["draft"]
    assert (d["gpu_resource_key"], d["gpu_count"]) == ("amd.com/gpu", 4)
    assert (d["cpu_request"], d["cpu_limit"], d["memory_request"], d["memory_limit"]) == ("8", "16", "96Gi", "128Gi")
    assert d["env"] == {"HF_HOME": "/w/.cache", "EMPTY": ""}
    assert (d["pvc_name"], d["pvc_mount_path"]) == ("model-pvc", "/w")
    assert d["node_selector"] == {"kubernetes.io/arch": "amd64"}
    assert d["tolerations"] == [{"key": "gpu", "operator": "Exists"}]
    by_code = {w["code"]: w.get("detail") for w in out["warnings"]}
    assert by_code["env_value_from"] == "HF_TOKEN"
    assert by_code["subpath_dropped"] == "weights: llama"
    assert by_code["shm_volume_dropped"] == "shm"
    assert by_code["volume_unsupported"] == "cfg (configMap), scratch (hostPath)"


def test_multiple_gpu_resources_and_pvcs_keep_first_and_warn():
    out = build_recipe_draft(_spec(
        args=["--model", "/w/m"],
        container={"resources": {"limits": {"nvidia.com/gpu": "1", "nvidia.com/mig-1g.5gb": "2"}},
                   "volume_mounts": [{"name": "a", "mountPath": "/a"}, {"name": "b", "mountPath": "/b"}]},
        volumes=[{"name": "a", "persistentVolumeClaim": {"claimName": "pvc-a"}},
                 {"name": "b", "persistentVolumeClaim": {"claimName": "pvc-b"}}],
    ))
    d = out["draft"]
    assert (d["gpu_resource_key"], d["gpu_count"], d["pvc_name"]) == ("nvidia.com/gpu", 1, "pvc-a")
    by_code = {w["code"]: w.get("detail") for w in out["warnings"]}
    assert by_code["multi_gpu_resource"] == "nvidia.com/mig-1g.5gb" and by_code["multi_pvc"] == "pvc-b"


def test_no_gpu_limit_means_cpu_only_recipe():
    spec = _spec(args=["--model", "/w/m"], container={"resources": {"requests": {"cpu": "2"}}})
    d = build_recipe_draft(spec)["draft"]
    assert d["gpu_count"] == 0 and d["cpu_request"] == "2" and d["gpu_resource_key"] == "nvidia.com/gpu"


def test_pod_level_things_the_recipe_cannot_hold_are_warned():
    out = build_recipe_draft(_spec(
        args=["--model", "/w/m"], containers_count=2, init_containers=["download-weights"],
        affinity={"nodeAffinity": {}}, container={"ports": [{"containerPort": 9000}]},
    ))
    by_code = {w["code"]: w.get("detail") for w in out["warnings"]}
    assert by_code["multi_container"] == "2"
    assert by_code["init_containers"] == "download-weights"
    assert "affinity_dropped" in by_code
    assert by_code["port_changed"] == "9000 → 8000"


def test_source_echoes_the_original_launch():
    out = build_recipe_draft(_spec(command=["vllm", "serve", "/w/m"], args=["--dtype", "auto"]))
    assert out["source"] == {"namespace": "ml", "deployment_name": "my-llm", "replicas": 2,
                             "command": ["vllm", "serve", "/w/m"], "args": ["--dtype", "auto"]}
