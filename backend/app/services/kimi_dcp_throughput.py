"""Kimi-K3 full MLA + KDA, with ROCm DSpark on a single eight-MI355X node."""

import math

from app.services.kv_layout import text_config

COMMIT = "df6603f0583375f244d1ac6d36265462b7490392"
IMAGE = f"vllm/vllm-openai-rocm:nightly-rocm100-{COMMIT}"
SOURCE = f"https://github.com/vllm-project/vllm/blob/{COMMIT}/vllm/v1/worker/gpu/spec_decode/dflash/speculator.py"
DISPATCH = f"https://github.com/vllm-project/vllm/blob/{COMMIT}/vllm/models/kimi_k3/nvidia/dspark_mla.py"
HARDWARE = "https://www.amd.com/en/products/accelerators/instinct/mi350/mi355x.html"


def layout(pool, model, options):
    """Restrict to the runner, checkpoint family and kernel actually inspected."""
    flags = pool["flags"]
    if (
        model != "Inferact/Kimi-K3-DSpark"
        or options.get("method") != "dspark"
        or pool.get("image") != IMAGE
        or pool.get("gpu_count") != 8
        or not any(str(pool.get(k)).upper() in {"MI355X", "MI355X OAM"} for k in ("gpu_type", "gpu_label_value"))
        or pool.get("v2_runner") not in (None, "1", "true")
        or (pool.get("tp"), pool.get("dcp"), pool.get("pp", 1)) != (8, 8, 1)
        or pool.get("role") not in ("aggregated", None)
        or flags.get("kv-cache-dtype") != "fp8"
        or options.get("kv_cache_dtype") != "fp8"
        or options.get("attention_backend") != "ROCM_AITER_MLA"
        or flags.get("attention-backend") != "ROCM_AITER_MLA"
        or flags.get("dcp-comm-backend") != "a2a"
        or flags.get("dtype", "auto") not in ("auto", "bfloat16")
        or flags.get("mamba-cache-dtype", "auto") not in ("auto", "bfloat16")
        or flags.get("mamba-ssm-cache-dtype", "auto") != "auto"
        or any(
            int(flags.get(k, 1)) != 1
            for k in ("cp-kv-cache-interleave-size", "dcp-kv-cache-interleave-size", "prefill-context-parallel-size")
        )
        or "draft_decode_context_parallel_size" in options
    ):
        # This image predates a separate CLI draft DCP override. Its DSpark
        # worker inherits the target's DCP for MLA, and forces 1 for other drafts.
        return None
    cfg = text_config(pool["metadata"]["config"])
    linear = cfg.get("linear_attn_config") or {}
    mla, kda = linear.get("full_attn_layers") or [], linear.get("kda_layers") or []
    if (
        len(mla) != 24
        or len(kda) != 69
        or set(mla) | set(kda) != set(range(1, 94))
        or cfg.get("kv_lora_rank") != 512
        or cfg.get("qk_rope_head_dim") != 64
        or cfg.get("num_attention_heads") != 96
        or linear.get("num_heads") != 96
        or linear.get("head_dim") != 128
        or linear.get("short_conv_kernel_size") != 4
    ):
        return None
    # FP32 recurrent matrix and BF16 Q/K/V convolution history per local
    # attention head. KDA state is TP-sharded across heads, never over context.
    heads = 96 // 8
    width = options["num_speculative_tokens"]
    state = 69 * (heads * 128 * 128 * 4 + 3 * heads * 128 * (3 + width) * 2)
    # BF16 weights: ReplicatedLinear context projection from five target
    # taps, its duplicate cross-layer KV writer, and fused TP-disabled Q/KV-A.
    context_params = 7168 * (5 * 7168 + 5 * 576)
    decode_replicated_params = 5 * 7168 * (1536 + 576)
    return {
        "ranks": 8,
        "context_params": context_params,
        "decode_replicated_params": decode_replicated_params,
        "replicated_draft_bytes": 2 * (context_params + decode_replicated_params),
        "target_layers": 24,
        "target_heads": 96,
        "target_row": 576,
        "target_state": state,
        "kda_heads": 96,
        "kda_layers": 69,
        "kda_dim": 128,
        "qk_dim": 192,
        "value_dim": 128,
        "latent": 512,
    }


def kv_bytes(layout, length):
    """Per-rank target tensor payload; excludes allocator padding and spare state pages."""
    local = math.ceil(length / layout["ranks"])
    return local * layout["target_row"] * layout["target_layers"] + layout["target_state"]


def exchange_time(layout, plan, batch, rounds, verify_width, candidates, fast):
    # The full-mesh board has 7 bidirectional 153 GB/s links per GPU. Model
    # one-way issue at 30–75% of that physical ceiling. Each peer receives Q
    # (FP8 or BF16 depending on fusion) and exchanges its BF16 O + FP32 LSE
    # for the head-wise A2A combine. Counts below are traffic on one rank.
    ranks = layout["ranks"]
    peer_fraction = (ranks - 1) / ranks
    bw = _link_bandwidth(fast)
    row = layout["target_row"] * (1 if fast else 2) + layout["latent"] * 2 + 4
    target = layout["target_heads"] * layout["target_layers"] * verify_width
    draft = plan["heads"] * len(plan["windows"]) * candidates  # sample_from_anchor
    return rounds * batch * (target + draft) * peer_fraction * row / bw


def _link_bandwidth(fast):
    return 7 * 153e9 / 2 * (0.75 if fast else 0.30)


def prefill_transfer(layout, batch, new_tokens, cached, max_batched_tokens, fast):
    """Chunked prefill gathers previously populated KV into the prefill workspace."""
    chunk = max(1, max_batched_tokens // batch)
    chunks = math.ceil(new_tokens / chunk)
    history = cached * chunks + chunk * chunks * (chunks - 1) // 2
    peer_fraction = (layout["ranks"] - 1) / layout["ranks"]
    return batch * history * layout["target_layers"] * layout["target_row"] * peer_fraction / _link_bandwidth(fast)
