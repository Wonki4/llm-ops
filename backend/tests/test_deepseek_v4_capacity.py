"""Verify physical cache owners using the same pinned model files as the recipe preview."""

import json

import pytest

from app.services.kv_capacity import Scenario, estimate
from app.services.kv_metadata import pinned_reference

FLASH = ("deepseek-ai/DeepSeek-V4-Flash", "60d8d70770c6776ff598c94bb586a859a38244f1")
DSPARK = ("deepseek-ai/DeepSeek-V4-Pro-0813", "72e1d3230f6c080a530b0a1d46f8eb4602340597")
VISION = ("deepseek-ai/DeepSeek-V4-Flash-Vision-Exp", "6821d6ad3681a4b137b066b76094fa82ebd0a380")
V41 = ("deepseek-ai/DeepSeek-V4.1-Flash", "314113f8a7dadb388215396ba5fbee2c610a04d6")


def run(target, *args, tp=8, gpu="B200", scenario=Scenario(), metadata=None):
    return estimate(
        metadata or pinned_reference({}, {}, *target),
        "vllm",
        ["--model", target[0], f"-tp={tp}", "--kv-cache-memory-bytes=8G", *args],
        scenario=scenario,
        gpu_type=gpu,
    )


def spec(n, **kwargs):
    return "--speculative-config=" + json.dumps({"method": "dspark", "num_speculative_tokens": n, **kwargs})


def test_v4_flash_distinguishes_csa_hca_compression_and_preserves_tp_replication():
    r = run(FLASH, "--kv-cache-dtype=fp8", "--block-size=256")
    assert r["missing"] == [] and r["payload_lower_bound"]
    # 43 SWA, 21 compressed sparse attention, 20 heavily compressed attention.
    width = 21 * (584 + 132) / 4 + 20 * 584 / 128
    assert r["attention_bytes_per_token_per_gpu"] == width
    sliding = 43 * 128 * 584
    csa_state = 21 * 8 * 2 * 2 * (512 + 128) * 4
    hca_state = 20 * 128 * 2 * 512 * 4
    assert r["required_bytes_per_gpu"] == 32768 * width + sliding + csa_state + hca_state
    assert r["required_bytes_per_replica"] == 8 * r["required_bytes_per_gpu"]
    single = run(FLASH, "--kv-cache-dtype=fp8", "--block-size=256", tp=1)
    assert single["capacity_range"] == r["capacity_range"]
    assert single["required_bytes_per_gpu"] == r["required_bytes_per_gpu"]


@pytest.mark.parametrize(
    ("target", "gpu", "backend", "dtype", "index", "latent", "idx"),
    [
        (FLASH, "GB200", "FLASHINFER_MLA_SPARSE_DSV4", "fp8", "mxfp4", 512, 68),
        (FLASH, "RTX-PRO-6000", "FLASHINFER_MLA_SPARSE_DSV4", "fp8", "fp8", 584, 132),
        (V41, "GB200", "FLASHINFER_MLA_SPARSE_DSV41", "fp8", "mxfp4", 512, 68),
        (V41, "B200", "FLASHMLA_MEGA_ATTN_DSV41", "fp8_ds_mla", "fp8", 528, 132),
        (V41, "GB300", "FLASHMLA_MEGA_ATTN_DSV41", "fp8", "mxfp4", 288, 68),
        (V41, "GB300", "FLASHMLA_SPARSE_DSV41", "fp8", "mxfp4", 528, 68),
        (V41, "MI325X", "ROCM_AITER_MLA_SPARSE", "fp8_e4m3", "fp8", 512, 132),
    ],
)
def test_cache_width_uses_recipe_backend_dtype_and_gpu(target, gpu, backend, dtype, index, latent, idx):
    r = run(
        target,
        f'--attention-config={{"backend":"{backend}","indexer_kv_dtype":"{index}"}}',
        f"--kv-cache-dtype={dtype}",
        gpu=gpu,
    )
    assert r["missing"] == []
    # V4.1 has 40 backbone layers and 8 query/index sources. Only 4 own the
    # persistent latent AND index keys: 3 half-rate and 1 full-rate owner.
    width = 2.5 * (latent + idx) if target == V41 else 21 * (latent + idx) / 4 + 20 * latent / 128
    assert r["attention_bytes_per_token_per_gpu"] == width
    assert r["capacity_range"][0] == 0 and r["capacity_range"][1] > 0 and r["capacity_tokens"] is None


def test_v41_native_ring_reserves_max_width_per_active_request_and_expands_at_power_of_two():
    args = [
        "--kv-cache-dtype=fp8",
        "--block-size=128",
        "--max-num-seqs=8",
        "--max-num-active-seqs=4",
        '--attention-config={"backend":"FLASHINFER_MLA_SPARSE_DSV41","indexer_kv_dtype":"mxfp4"}',
    ]
    scenario = Scenario(32768, 12)
    rows = [run(V41, *args, spec(k), scenario=scenario) for k in (4, 7)]
    for k, r, ring_slots in zip((4, 7), rows, (8, 16), strict=True):
        assert not r["missing"] and r["requests"] == 4
        assert r["integrated_speculation"]["candidate_tokens"] == k * 4
        components = {c["kind"]: c for c in r["components"]}
        assert components["FP32 compressor ring"]["bytes_per_gpu"] == 3 * 2 * 512 * 4 * ring_slots
        assert components["DSpark sliding-window MLA"]["layers"] == 3
        assert components["Sliding-window MLA"]["layers"] == 40
        assert r["capacity_range"][1] > 0
    assert rows[1]["required_bytes_per_gpu"] > rows[0]["required_bytes_per_gpu"]
    assert rows[1]["capacity_range"][1] < rows[0]["capacity_range"][1]


def test_native_draft_uses_same_checkpoint_and_recipe_width_even_above_training_block():
    for target, k in ((VISION, 3), (DSPARK, 7)):
        result = run(target, spec(k, model=target[0], enable_adaptive_verification=True))
        assert result["missing"] == []
        assert result["integrated_speculation"]["draft_tokens"] == k
    r = run(DSPARK, spec(7, model="other/repository"))
    assert r["missing"] and not r.get("capacity_range")
    assert not run(DSPARK, f"--revision={DSPARK[1]}", spec(7, revision=DSPARK[1]))["missing"]
    assert run(DSPARK, f"--revision={DSPARK[1]}", spec(7, revision="different"))["missing"]


def test_native_schedule_resolves_batch_and_keeps_max_width_for_engine_ring():
    cfg = spec(7, num_speculative_tokens_per_batch_size=[[1, 4, 7], [5, 24, 1]])
    r = run(V41, cfg, "--max-num-batched-tokens=12", scenario=Scenario(4096, 12))
    assert r["missing"] == []
    assert r["integrated_speculation"]["candidate_tokens"] == 6
    assert r["integrated_speculation"]["active_requests"] == 6
    assert r["integrated_speculation"]["draft_tokens"] == 1


def test_full_prefix_blocks_share_persistent_rows_and_retain_per_request_rings():
    r = run(V41, "--block-size=128", scenario=Scenario(32768, 3, 8193))
    assert r["missing"] == []
    saved = r["required_bytes_per_gpu"] - r["shared_required_bytes_per_gpu"]
    assert saved == 8192 * 2 * r["attention_bytes_per_token_per_gpu"]
    assert r["complete_requests_range"][1] > 0


def test_unknown_and_measured_zero_budget_are_distinct():
    r = run(FLASH, "--kv-cache-memory-bytes=1")
    assert r["capacity_range"] == [0, 0] and not r["missing"]
    for args in (
        ["--block-size=16"],
        ["-dcp=2"],
        ["-pp=2"],
        ["--kv-cache-dtype=nvfp4_ds_mla"],
        ["--speculative-config={}"],
        ["--hf-overrides={}"],
    ):
        r = run(FLASH, *args)
        assert r["missing"] and not r.get("capacity_range")


def test_window_accounts_for_trailing_partial_page_at_the_actual_request_length():
    # Both lengths occupy the same compressed page except for the one new
    # page per persistent owner when advancing from block-aligned 32768.
    args = ("--block-size=128", "--kv-cache-dtype=fp8", "--attention-backend=FLASHINFER_MLA_SPARSE_DSV41")
    start = run(V41, *args, scenario=Scenario(32768, 1))
    after = run(V41, *args, scenario=Scenario(32769, 1))
    compressed_page = (512 + 132) * (1 + 3 / 2) * 128
    # The current token opens a new SWA page, but the first page containing
    # retained keys cannot be freed until position 32799.
    swa_page = 32 * 512 * 40
    assert after["required_bytes_per_gpu"] - start["required_bytes_per_gpu"] == compressed_page + swa_page
