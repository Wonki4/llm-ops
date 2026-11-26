import hashlib
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.kv_capacity import GIB, Scenario, estimate
from app.services.kv_metadata import metadata_job
from app.services.kv_metadata_reader import inspect
from app.services.recipe_capacity import deployment_estimate

QWEN = {
    "model_type": "qwen3",
    "num_hidden_layers": 64,
    "num_attention_heads": 64,
    "num_key_value_heads": 8,
    "head_dim": 128,
    "torch_dtype": "bfloat16",
    "max_position_embeddings": 40960,
}
DSA = [*range(4, 93, 4), 93]
KIMI = {
    "model_type": "kimi_k31",
    "text_config": {
        "num_hidden_layers": 93,
        "model_type": "kimi_linear",
        "dtype": "bfloat16",
        "kv_lora_rank": 512,
        "dsa_config": {"dsa_layers": DSA, "index_head_dim": 128},
        "linear_attn_config": {
            "kda_layers": [i for i in range(1, 94) if i not in DSA],
            "num_heads": 96,
            "head_dim": 128,
            "short_conv_kernel_size": 4,
        },
    },
}


def test_qwen_target_and_rank_sharding():
    actual = estimate({"config": QWEN}, "vllm", ["--kv-cache-memory-bytes=8G"])
    assert actual["required_bytes_per_replica"] == 8 * GIB
    assert actual["capacity_tokens"] == 32768
    assert actual["complete_requests"] == 1
    split = estimate({"config": QWEN}, "vllm", ["-tp", "8", "-pp", "2", "--kv-cache-memory-bytes", "1G"])
    assert split["required_bytes_per_gpu"] == GIB // 2
    assert split["required_bytes_per_replica"] == 8 * GIB
    assert split["capacity_tokens"] == 65536


@pytest.mark.parametrize(("latent", "index", "width"), [("fp8", "fp8", 16992), ("nvfp4", "mxfp4", 10080)])
def test_packed_kimi_rows(latent, index, width):
    flags = [
        "-tp",
        "8",
        "--kv-cache-dtype",
        latent,
        "--attention-config",
        json.dumps({"indexer_kv_dtype": index}),
        "--kv-cache-memory-bytes=49G",
    ]
    out = estimate({"config": KIMI}, "vllm", flags)
    assert out["attention_bytes_per_token_per_gpu"] == width
    assert out["block_tokens"] == 2048
    state = next(e for e in out["components"] if e["unit"] == "request")
    assert state["bytes_per_gpu"] == 814080 * 69
    assert out["required_bytes_per_gpu"] == width * (32768 + 3 * 2048)
    dcp = estimate({"config": KIMI}, "vllm", flags + ["-dcp", "8"])
    assert dcp["attention_bytes_per_token_per_gpu"] == width / 8
    assert dcp["components"][-1]["bytes_per_gpu"] == out["components"][-1]["bytes_per_gpu"] / (
        1 if out["components"][-1]["unit"] == "request" else 8
    )


def test_explicit_override_rounding_prefix_and_independent_scheduler_limits():
    flags = [
        "--kv-cache-memory-bytes=1G",
        "--kv-cache-memory-bytes=16G",
        "--block-size=16",
        "--max-num-seqs=1",
        "--max-model-len=64000",
    ]
    out = estimate({"config": QWEN}, "vllm", flags, scenario=Scenario(32768, 2, 16001))
    assert out["capacity_tokens"] == 65536 and out["complete_requests"] == 1
    assert out["required_bytes_per_gpu"] == 16 * GIB
    assert out["shared_required_bytes_per_gpu"] == 16 * GIB - 16000 * 262144
    assert estimate({"config": QWEN}, "vllm", flags, scenario=Scenario(64001))["complete_requests"] == 0
    out = estimate({"config": QWEN}, "vllm", flags, scenario=Scenario(3))
    assert out["capacity_tokens"] == 65536


def test_missing_metadata_actual_zero_and_estimated_range():
    assert estimate({"config": QWEN}, "vllm", [])["capacity_tokens"] is None
    assert estimate({"config": QWEN}, "vllm", [], observed_budget=0)["capacity_tokens"] == 0
    out = estimate({"config": QWEN, "weight_bytes": 1 << 30}, "vllm", [], vram_gb=80)
    assert out["capacity_tokens"] is None and out["capacity_range"][0] == 0 < out["capacity_range"][1]
    unknown = estimate({"config": {**QWEN, "model_type": "next_architecture"}}, "vllm", ["--kv-cache-memory-bytes=8G"])
    assert unknown["capacity_tokens"] is None and unknown["missing"]


@pytest.mark.parametrize(("weights", "vram_gb"), [(32 * GIB, None), (None, 80), (None, None)])
def test_request_memory_is_available_before_device_kv_budget(weights, vram_gb):
    out = estimate({"config": QWEN, "weight_bytes": weights}, "vllm", [], vram_gb=vram_gb)
    assert out["required_bytes_per_gpu"] == 8 * GIB
    assert out["budget_source"] == "unknown" and out["budget_bytes_per_gpu"] is None
    assert out["capacity_tokens"] is None and out["complete_requests"] is None
    assert "capacity_range" not in out


@pytest.mark.parametrize("args", [["--kv-cache-memory-bytes=16G"], ["--num-gpu-blocks-override=4096"]])
def test_configured_kv_larger_than_one_device_preserves_requirement_and_refuses_capacity(args):
    out = estimate({"config": QWEN}, "vllm", args, vram_gb=12)
    assert out["budget_source"] == "explicit" and out["budget_bytes_per_gpu"] == [16 * GIB, 16 * GIB]
    assert out["budget_exceeds_vram"] and "12 GB" in out["missing"][-1]
    assert out["required_bytes_per_gpu"] == 8 * GIB
    assert out["capacity_tokens"] is None and out["complete_requests"] is None and "capacity_range" not in out
    assert estimate({"config": QWEN}, "vllm", args, vram_gb=24)["capacity_tokens"] == 65536


def test_configured_budget_checks_device_before_metadata_and_distinguishes_zero_budget():
    out = estimate({}, "vllm", ["--kv-cache-memory-bytes=16G"], vram_gb=16)
    assert out["budget_exceeds_vram"] and out["capacity_tokens"] is None
    out = estimate({"config": QWEN}, "vllm", ["--kv-cache-memory-bytes=0"])
    assert out["budget_source"] == "unknown" and out["capacity_tokens"] is None


@pytest.mark.parametrize(
    "args",
    [["--cpu-offload-gb=8"], ["--offload-group-size=4"], ["--quantization=bitsandbytes"], ["--load-format=gguf"]],
)
def test_weight_residency_changes_require_explicit_or_observed_budget(args):
    meta = {"config": QWEN, "weight_bytes": 60 * GIB}
    out = estimate(meta, "vllm", args, vram_gb=80)
    assert out["required_bytes_per_gpu"] == 8 * GIB
    assert out["budget_source"] == "unknown" and "capacity_range" not in out
    assert estimate(meta, "vllm", args, observed_budget=8 * GIB)["capacity_tokens"] == 32768
    explicit = estimate(meta, "vllm", [*args, "--kv-cache-memory-bytes=4G"], vram_gb=80, observed_budget=8 * GIB)
    assert explicit["budget_source"] == "explicit" and explicit["capacity_tokens"] == 16384


def test_unchanged_weight_loading_permits_only_weight_adjusted_profile_ceiling():
    flags = ["--cpu-offload-gb=0", "--load-format=safetensors", "--dtype=bf16", "--gpu-memory-utilization=0.9"]
    meta = {"config": QWEN, "weight_bytes": 60 * GIB}
    out = estimate(meta, "vllm", flags, vram_gb=80)
    assert out["budget_bytes_per_gpu"] == [0, 72_000_000_000 - 60 * GIB]
    assert out["budget_source"] == "profile_upper_bound" and out["capacity_tokens"] is None

    # The same serialized file size is no longer a bound on resident weights after narrowing.
    meta["config"] = {**QWEN, "torch_dtype": "float32"}
    for cast_flags in (flags, []):
        out = estimate(meta, "vllm", cast_flags, vram_gb=80)
        assert out["required_bytes_per_gpu"] == 8 * GIB
        assert out["budget_source"] == "unknown" and "capacity_range" not in out


def test_hybrid_sliding_and_recurrent_bytes_are_separate():
    cfg = {
        **QWEN,
        "layer_types": ["full_attention", "sliding_attention", "linear_attention"],
        "num_hidden_layers": 3,
        "sliding_window": 128,
        "linear_num_key_heads": 4,
        "linear_num_value_heads": 8,
        "linear_key_head_dim": 64,
        "linear_value_head_dim": 64,
        "linear_conv_kernel_dim": 4,
    }
    out = estimate({"config": cfg}, "vllm", ["--kv-cache-memory-bytes=8G"])
    assert [c["unit"] for c in out["components"]] == ["token", "token", "request"]
    assert out["required_bytes_per_gpu"] < 3 * 4096 * 32768
    assert out["capacity_tokens"] is None and out["capacity_range"][1] > 0
    cfg["layer_types"][2] = "unrecognized"
    partial = estimate({"config": cfg}, "vllm", ["--kv-cache-memory-bytes=8G"])
    assert len(partial["components"]) == 2 and "unrecognized" in partial["missing"]


def test_local_metadata_only_reads_json(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps(QWEN))
    (tmp_path / "model.safetensors.index.json").write_text(
        json.dumps({"metadata": {"total_size": 1234}, "weight_map": {"example": "intentionally-absent.safetensors"}})
    )
    (tmp_path / "model.py").write_text('raise ValueError("must never import")')
    out = inspect(str(tmp_path), None)
    assert out["config_digest"] == hashlib.sha256((tmp_path / "config.json").read_bytes()).hexdigest()
    assert out["weight_bytes"] == 1234 and out["config"]["num_hidden_layers"] == 64 and out["digest"]


def test_metadata_job_reuses_revision_regardless_of_load_flags_and_requests_no_gpu():
    dep = SimpleNamespace(cluster_id="other", namespace="serving")
    pod = {"volumes": [{"name": "models", "persistentVolumeClaim": {"claimName": "offline"}}]}
    c = {
        "image": "internal/engine:1",
        "volumeMounts": [{"name": "models", "mountPath": "/models"}],
        "env": [{"name": "HF_TOKEN", "value": "private"}, {"name": "VLLM_API_KEY", "value": "unused"}],
    }
    name, job, secret, _ = metadata_job(dep, pod, c, "/models/qwen", "rev")
    assert name == metadata_job(dep, pod, {**c, "args": ["-tp", "8"]}, "/models/qwen", "rev")[0]
    assert "unused" not in str(job) and "private" not in str(job) and secret.manifest()
    helper = job["spec"]["template"]["spec"]["containers"][0]
    assert helper["volumeMounts"][0]["readOnly"] and set(helper["resources"]["limits"]) == {"memory", "cpu"}
    assert (
        name
        != metadata_job(SimpleNamespace(cluster_id="another", namespace="serving"), pod, c, "/models/qwen", "rev")[0]
    )


async def test_rendered_pool_overrides_use_separate_model_and_flags():
    from app.services.pd_serving import LABEL_PD_ROLE

    dep = SimpleNamespace(cluster_id="other", namespace="research", pd_config={}, gpu_type=None)

    def doc(role, tp):
        return {
            "kind": "Deployment",
            "spec": {
                "replicas": 3,
                "template": {
                    "metadata": {"labels": {LABEL_PD_ROLE: role}},
                    "spec": {
                        "containers": [
                            {
                                "name": "vllm",
                                "image": "vllm",
                                "args": ["--model", "/models/" + role, "-tp", str(tp), "--kv-cache-memory-bytes=8G"],
                            }
                        ]
                    },
                },
            },
        }

    read = AsyncMock(return_value={"config": QWEN})
    with patch("app.services.recipe_capacity.metadata_for", read):
        out = await deployment_estimate(None, dep, manifests=[doc("prefill", 1), doc("decode", 8)])
    pre, dec = out["pools"]
    assert (pre["role"], pre["capacity_tokens"], pre["model_path"]) == ("prefill", 32768, "/models/prefill")
    assert dec["capacity_tokens"] == 8 * 32768 and dec["fingerprint"] != pre["fingerprint"]


@pytest.mark.parametrize(
    ("engine", "flag", "budget_source", "capacity"),
    [
        ("vllm", "--kv-cache-memory-bytes=0", "startup", 32768),
        ("vllm", "--kv-cache-memory-bytes=0.0G", "startup", 32768),
        ("vllm", "--kv-cache-memory-bytes=1G", "explicit", 4096),
        ("vllm", "--kv-cache-memory-bytes=bogus", None, None),
        ("sglang", "--kv-cache-memory-bytes=1G", "startup", 32768),
    ],
)
async def test_rendered_pool_uses_matching_startup_only_for_profiled_memory(engine, flag, budget_source, capacity):
    container = {"name": engine, "image": "engine", "args": ["--model", "Qwen/Qwen3-32B", flag]}
    manifest = {
        "kind": "Deployment",
        "spec": {"template": {"metadata": {"labels": {}}, "spec": {"containers": [container]}}},
    }
    dep = SimpleNamespace(cluster_id="cluster", namespace="ns", pd_config={}, gpu_type="a100")
    matched = AsyncMock(return_value=8 * GIB)
    with (
        patch("app.services.recipe_capacity.metadata_for", AsyncMock(return_value={"config": QWEN})),
        patch.dict(sys.modules, {"app.services.kv_observations": SimpleNamespace(matching_budget=matched)}),
    ):
        result = await deployment_estimate(None, dep, manifests=[manifest], base_profile=SimpleNamespace(name="a100"))
    pool = result["pools"][0]
    assert pool.get("budget_source") == budget_source and pool["capacity_tokens"] == capacity
    assert matched.await_count == int(budget_source == "startup")


def test_partial_layout_still_reports_configured_gpu_budget_and_parallelism():
    result = estimate({"error": "unavailable PVC"}, "vllm", ["-tp", "8", "--kv-cache-memory-bytes=2G"])
    assert result["capacity_tokens"] is None and result["tp"] == 8
    assert result["budget_bytes_per_gpu"] == [2 * GIB, 2 * GIB]
    assert result["missing"] == ["unavailable PVC"]


@pytest.mark.parametrize(("heads", "expected"), [(64, 8 * 262144), (8, 262144), (1, 32768)])
def test_attention_head_sharing(heads, expected):
    out = estimate({"config": {**QWEN, "num_key_value_heads": heads}}, "vllm", ["--block-size=16"])
    assert out["attention_bytes_per_token_per_gpu"] == expected
