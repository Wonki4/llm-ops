"""GLM5Next NoPE sparse MLA and KDA cache payload.

Verified against vLLM commit 65ada0c40e18d1100deee9c3deb351326a309e82,
specifically models/glm5next/common/{attention,kda}.py and kda_state_dtype.
"""

import json

from app.services.kv_layout import (
    Component,
    UnknownLayoutError,
    dtype_bytes,
    positive,
    resolve_dtype,
    shard,
    state_width,
)

_BLACKWELL = {"B200", "GB200", "B300", "GB300"}
_HOPPER = {"H100", "H200", "GH200"}
_ROCM = {"MI300X", "MI325X", "MI350X", "MI355X"}


def glm_flash_layout(cfg, flags, tp, gpu_type, notes):
    """Mirror glm5next/common/attention.py's pooled index and per-request raw tail.

    Scalar cache metadata in this architecture describes the 512-dimensional MLA
    latent. The KDA convolution and recurrent tensors have independently selected
    dtypes and a TP-local head dimension.
    """
    n = positive(cfg.get("num_hidden_layers"), "GLM Flash layer count")
    kinds = cfg.get("layer_types")
    linear = cfg.get("linear_attn_config")
    if n > 4096 or not isinstance(kinds, list) or len(kinds) != n or not isinstance(linear, dict):
        raise UnknownLayoutError("GLM Flash sparse/KDA layer map")
    sparse = [i for i, kind in enumerate(kinds) if kind == "deepseek_sparse_attention"]
    kda = [i for i, kind in enumerate(kinds) if kind == "linear_attention"]
    if (
        not sparse
        or not kda
        or len(sparse) + len(kda) != n
        or sparse != linear.get("full_attn_layers")
        or kda != linear.get("kda_layers")
    ):
        raise UnknownLayoutError("GLM Flash sparse/KDA layer map")
    positive(cfg.get("index_topk"), "GLM Flash sparse index selection")
    pool = cfg.get("index_kpool")
    if (cfg.get("kv_lora_rank"), cfg.get("qk_rope_head_dim"), cfg.get("index_head_dim"), pool) != (512, 0, 128, 4):
        raise UnknownLayoutError("GLM Flash NoPE MLA or pooled index geometry")
    if (
        cfg.get("index_kpool_compress") is not True
        or cfg.get("mla_use_nope") is not True
        or cfg.get("index_kpool_always_select_tail") is not True
        or (cfg.get("indexer_types") is not None and cfg["indexer_types"] != ["full"] * n)
    ):
        raise UnknownLayoutError("GLM Flash compressed index layout")
    if flags.get("kv-cache-dtype-skip-layers"):
        raise UnknownLayoutError("GLM Flash per-layer KV dtype overrides")
    shard(positive(cfg.get("num_attention_heads"), "GLM Flash query heads"), tp)
    dtype = resolve_dtype(flags.get("dtype"), cfg.get("dtype") or cfg.get("torch_dtype"))
    gpu = (gpu_type or "").upper()
    kv = flags.get("kv-cache-dtype", "auto")
    if kv == "auto" and gpu in _BLACKWELL:
        kv = "fp8"
        notes.append("GLM Flash auto KV selects FP8 on SM100-family datacenter GPUs.")
    elif kv == "auto" and (gpu in _HOPPER | _ROCM or flags.get("attention-backend") == "ROCM_AITER_MLA_SPARSE"):
        kv = dtype
        notes.append(f"GLM Flash auto KV uses the loaded {dtype} model dtype on this accelerator.")
    if kv not in ("auto", "bf16", "bfloat16", "half", "float16", "fp16", "fp8", "fp8_e4m3", "fp8_e4m3fn"):
        raise UnknownLayoutError(f"GLM Flash NoPE MLA cache format: {kv}")
    if gpu in _HOPPER and kv.startswith("fp8"):
        raise UnknownLayoutError("GLM Flash Hopper sparse attention requires a 16-bit latent cache")
    if kv == "auto":
        notes.append("Unknown GPU family: the Flash MLA payload uses the smaller supported FP8 row as a lower bound.")
    latent_width = 512 * (1 if kv == "auto" else dtype_bytes(kv))
    attention = json.loads(flags.get("attention-config", "{}"))
    if not isinstance(attention, dict):
        raise UnknownLayoutError("GLM Flash attention config")
    index_dtype = resolve_dtype(
        flags.get("attention-config.indexer-kv-dtype", attention.get("indexer_kv_dtype")), "fp8"
    )
    if index_dtype != "fp8":
        raise UnknownLayoutError(f"GLM Flash compressed index format: {index_dtype}")

    recurrent_dtype = resolve_dtype(flags.get("mamba-ssm-cache-dtype"), "float32")
    if recurrent_dtype not in ("float32", "bfloat16"):
        raise UnknownLayoutError("GLM Flash KDA recurrent state dtype")
    if flags.get("mamba-cache-dtype", "auto") not in ("auto", "float32", "bfloat16", "float16"):
        raise UnknownLayoutError("GLM Flash KDA convolution state dtype")
    state = state_width(cfg, "kda", tp, dtype, {**flags, "mamba-ssm-cache-dtype": recurrent_dtype})
    # Indexer FP8[128] + a FP32 scale is one entry per four logical tokens,
    # replicated across TP. For each incomplete pool, every request holds one
    # BF16 key and one BF16 gate vector per slot on every rank.
    entry_bytes = 128 + 4
    tail = 2 * 128 * 2 * pool
    notes.append(
        f"GLM Flash: {len(sparse)} NoPE sparse MLA layers, FP8 index entries of {entry_bytes} B per {pool} tokens, "
        f"plus one BF16 key/gate tail per request and sparse layer. {len(kda)} KDA layers partition state heads by TP. "
        "Shared-pool padding and engine checkpoints can consume additional bytes; capacity is a payload-based ceiling."
    )
    return (
        kinds,
        {
            "deepseek_sparse_attention": [
                Component("NoPE MLA " + ("FP8 minimum" if kv == "auto" else kv), latent_width, payload_floor=True),
                Component(f"FP8 index (one row/{pool} tokens)", entry_bytes // pool),
                Component("BF16 index tail", tail, state=True),
            ],
            "linear_attention": [Component("KDA state", state, state=True)],
        },
        [],
        kv,
    )
