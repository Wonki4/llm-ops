"""GLM5Next cache usage for the pinned GLM-5.3-Flash serving recipes."""

import pytest

from app.services.kv_capacity import Scenario, estimate
from app.services.kv_metadata import pinned_reference

CHECKPOINTS = (
    ("zai-org/GLM-5.3-Flash", "46a050bec13002ad29ee795c16e61cf4fa3c4a9c"),
    ("zai-org/GLM-5.3-Flash-BF16", "f9fed3bbc82291073373d67452e9f25317ef7577"),
    ("RedHatAI/GLM-5.3-Flash-NVFP4", "4756632d3fdf88095feaa87974d41354b2afdca4"),
)


@pytest.mark.parametrize("model,rev", CHECKPOINTS)
@pytest.mark.parametrize(
    "gpu,tp,flags,dtype,latent_bytes",
    (
        ("H200", 8, [], "bfloat16", 1024),
        ("B200", 8, ["--kv-cache-dtype=fp8"], "fp8", 512),
        ("GB300", 4, [], "fp8", 512),
        ("MI355X", 8, ["--attention-backend=ROCM_AITER_MLA_SPARSE"], "bfloat16", 1024),
    ),
)
def test_pinned_checkpoint_cache_payload_depends_on_device_and_tp(model, rev, gpu, tp, flags, dtype, latent_bytes):
    meta = pinned_reference({}, {}, model, rev)
    out = estimate(
        meta,
        "vllm",
        [f"--tensor-parallel-size={tp}", "--kv-cache-memory-bytes=4G", *flags],
        gpu_type=gpu,
        scenario=Scenario(context=32768, requests=3),
    )
    assert not out["missing"]
    assert out["kv_dtype"] == dtype
    per_token = 11 * (latent_bytes + (128 + 4) // 4)
    assert out["attention_bytes_per_token_per_gpu"] == per_token
    # A TP-local recurrent matrix and three convolution histories per KDA head,
    # plus four raw BF16 head/gate pairs per pooled indexer per request.
    kda = 34 * (64 // tp) * (128**2 * 4 + 3 * 128 * 3 * 2)
    tail = 11 * 4 * (2 * 128 * 2)
    by_kind = {c["kind"]: c for c in out["components"]}
    assert by_kind["KDA state"]["bytes_per_gpu"] == kda
    assert by_kind["BF16 index tail"]["bytes_per_gpu"] == tail
    assert out["required_bytes_per_gpu"] == 3 * (per_token * 32768 + kda + tail)
    assert out["required_bytes_per_replica"] == tp * out["required_bytes_per_gpu"]
    assert out["capacity_range"][1] >= 3 * 32768
    assert out["capacity_tokens"] is None and out["payload_lower_bound"]


def test_prefix_and_dcp_share_attention_only_and_pp_keeps_decoder_order():
    meta = pinned_reference({}, {}, *CHECKPOINTS[0])
    out = estimate(
        meta,
        "vllm",
        ["-tp", "8", "-pp", "2", "-dcp", "2", "--kv-cache-memory-bytes=2G"],
        gpu_type="B200",
        scenario=Scenario(context=32768, requests=2, prefix=16384),
        env={"VLLM_PP_LAYER_PARTITION": "1,44"},
    )
    assert not out["missing"]
    assert out["attention_bytes_per_token_per_gpu"] == 5995 / 2
    assert out["required_bytes_per_gpu"] - out["shared_required_bytes_per_gpu"] == 16384 * 5995 // 2
    r0 = [c for c in out["components"] if c["pipeline_rank"] == 0]
    assert len(r0) == 1 and r0[0]["kind"] == "KDA state" and r0[0]["layers"] == 1


def test_recurrent_bfloat16_option_reduces_request_state_but_preserves_token_rows():
    meta = pinned_reference({}, {}, *CHECKPOINTS[0])
    out = estimate(
        meta, "vllm", ["-tp", "8", "--kv-cache-memory-bytes=2G", "--mamba-ssm-cache-dtype=bfloat16"], gpu_type="B200"
    )
    assert out["attention_bytes_per_token_per_gpu"] == 5995
    assert next(c["bytes_per_gpu"] for c in out["components"] if c["kind"] == "KDA state") == (
        34 * 8 * (128**2 * 2 + 3 * 128 * 3 * 2)
    )


def test_pooled_index_aligns_uneven_requests_to_kernel_page():
    meta = pinned_reference({}, {}, *CHECKPOINTS[0])
    out = estimate(
        meta, "vllm", ["-tp", "8", "--kv-cache-memory-bytes=2G"], gpu_type="B200", scenario=Scenario(context=129)
    )
    assert out["block_tokens"] == 128
    assert out["required_bytes_per_gpu"] == 256 * 5995 + 18_475_008


@pytest.mark.parametrize(
    "gpu,args,changes",
    (
        ("H100", ["--kv-cache-dtype=fp8"], {}),
        ("B200", [], {"index_kpool": 8}),
        ("B200", [], {"index_topk": None}),
        ("B200", [], {"index_kpool_compress": False}),
        ("B200", ["--block-size=16"], {}),
        ("B200", ["--attention-config.indexer-kv-dtype=mxfp4"], {}),
        ("B200", ['--speculative-config={"method":"mtp","num_speculative_tokens":5}'], {}),
    ),
)
def test_unknown_cache_options_do_not_yield_a_fabricated_total(gpu, args, changes):
    meta = pinned_reference({}, {}, *CHECKPOINTS[0])
    cfg = meta["config"]
    changed = {**meta, "config": {**cfg, "text_config": {**cfg["text_config"], **changes}}}
    out = estimate(changed, "vllm", ["-tp", "8", "--kv-cache-memory-bytes=4G", *args], gpu_type=gpu)
    assert out["missing"] and not out.get("capacity_range")
