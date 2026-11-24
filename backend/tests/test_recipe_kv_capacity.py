import hashlib
import json
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


def test_partial_layout_still_reports_configured_gpu_budget_and_parallelism():
    result = estimate({"error": "unavailable PVC"}, "vllm", ["-tp", "8", "--kv-cache-memory-bytes=2G"])
    assert result["capacity_tokens"] is None and result["tp"] == 8
    assert result["budget_bytes_per_gpu"] == [2 * GIB, 2 * GIB]
    assert result["missing"] == ["unavailable PVC"]


@pytest.mark.parametrize(("heads", "expected"), [(64, 8 * 262144), (8, 262144), (1, 32768)])
def test_attention_head_sharing(heads, expected):
    out = estimate({"config": {**QWEN, "num_key_value_heads": heads}}, "vllm", ["--block-size=16"])
    assert out["attention_bytes_per_token_per_gpu"] == expected
