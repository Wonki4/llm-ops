"""Per-rank physical KV layouts. All sizes are bytes; layer numbers in Kimi config are one-based."""

import json
from collections import Counter
from dataclasses import dataclass
from math import ceil

# These vLLM decoders partition Q heads across TP ranks and replicate KV heads when needed.
# The GLM/Qwen vision towers contribute resident weights, but no decoder token cache.
_VL_AND_GLM_GQA = {
    "glm4",
    "glm4_moe",
    "glm4v_text",
    "glm4v_moe_text",
    "qwen2_5_vl",
    "qwen2_5_vl_text",
    "qwen3_vl_text",
    "qwen3_vl_moe_text",
}


class UnknownLayoutError(ValueError):
    """The available evidence cannot identify this engine layout."""


def positive(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise UnknownLayoutError(name)
    try:
        result = int(value)
    except ValueError:
        raise UnknownLayoutError(name) from None
    if result <= 0:
        raise UnknownLayoutError(name)
    return result


def dtype_bytes(dtype):
    if dtype in ("float16", "bfloat16", "half", "bf16", "fp16"):
        return 2
    if dtype in ("float32", "float", "fp32"):
        return 4
    if dtype in ("fp8", "fp8_e4m3", "fp8_e5m2", "fp8_e4m3fn"):
        return 1
    raise UnknownLayoutError(f"cache dtype: {dtype}")


def shard(n, parts):
    if n % parts:
        raise UnknownLayoutError("uneven sharding")
    return n // parts


@dataclass(frozen=True)
class Component:
    kind: str
    width: int  # Per token for attention, per request for recurrent state
    window: int = 0
    state: bool = False
    payload_floor: bool = False


def text_config(config):
    return config.get("text_config") or config.get("language_config") or config.get("llm_config") or config


def resolve_dtype(explicit, fallback):
    return fallback if explicit in ("auto", None) else explicit


def state_width(cfg, kind, tp, dtype, flags):
    conv_dtype = dtype_bytes(resolve_dtype(flags.get("mamba-cache-dtype"), dtype))
    temporal_dtype = dtype_bytes(
        resolve_dtype(flags.get("mamba-ssm-cache-dtype"), cfg.get("mamba_ssm_dtype", "float32"))
    )
    if kind == "kda":
        linear = cfg["linear_attn_config"]
        heads = shard(positive(linear["num_heads"], "KDA heads"), tp)
        dim = positive(linear["head_dim"], "KDA dimension")
        conv = positive(linear["short_conv_kernel_size"], "KDA kernel") - 1
        return heads * (dim * dim * temporal_dtype + 3 * dim * conv * conv_dtype)
    if kind == "linear_attention":
        kh = shard(positive(cfg["linear_num_key_heads"], "linear key heads"), tp)
        vh = shard(positive(cfg["linear_num_value_heads"], "linear value heads"), tp)
        kd = positive(cfg["linear_key_head_dim"], "linear key dimension")
        vd = positive(cfg["linear_value_head_dim"], "linear value dimension")
        conv = positive(cfg["linear_conv_kernel_dim"], "linear kernel") - 1
        return vh * kd * vd * temporal_dtype + (2 * kh * kd + vh * vd) * conv * conv_dtype
    inner = positive(
        cfg.get("intermediate_size") or cfg.get("mamba_d_inner") or (cfg.get("hidden_size", 0) * cfg.get("expand", 0)),
        "Mamba intermediate size",
    )
    state = positive(cfg.get("state_size") or cfg.get("mamba_d_state"), "Mamba state size")
    kernel = positive(cfg.get("conv_kernel") or cfg.get("mamba_d_conv"), "Mamba conv kernel")
    groups = cfg.get("n_groups", cfg.get("mamba_n_groups", 0))
    local_channels = shard(inner, tp)
    conv_channels = local_channels + 2 * ceil(groups / tp) * state
    return local_channels * state * temporal_dtype + conv_channels * (kernel - 1) * conv_dtype


def layer_kinds(cfg, kimi):
    n = positive(cfg.get("num_hidden_layers"), "num_hidden_layers")
    if n > 4096:
        raise UnknownLayoutError("layer count exceeds supported range")
    if kimi:
        dsa = set(cfg["dsa_config"]["dsa_layers"])
        kda = set(cfg["linear_attn_config"]["kda_layers"])
        if dsa | kda != set(range(1, n + 1)) or dsa & kda:
            raise UnknownLayoutError("Kimi DSA/KDA layer map")
        return ["dsa" if i in dsa else "kda" for i in range(1, n + 1)]
    kinds = cfg.get("layer_types") or cfg.get("layers_block_type")
    if kinds:
        if not isinstance(kinds, list) or len(kinds) != n:
            raise UnknownLayoutError("layer_types")
        return kinds
    if cfg.get("model_type") in ("mamba", "mamba2"):
        return ["mamba"] * n
    if cfg.get("linear_attn_config") or cfg.get("linear_num_key_heads") or cfg.get("hybrid_override_pattern"):
        raise UnknownLayoutError("hybrid layer map")
    dense_families = {
        "qwen2",
        "qwen2_moe",
        "qwen3",
        "qwen3_moe",
        "llama",
        "mistral",
        "mixtral",
        "deepseek_v2",
        "deepseek_v3",
        "deepseek_v32",
        "phi3",
        "cohere",
        "granite",
        "gemma",
    }
    model_type = cfg.get("model_type")
    if model_type not in dense_families and model_type not in _VL_AND_GLM_GQA:
        raise UnknownLayoutError("unrecognized cache layout: " + str(cfg.get("model_type")))
    if model_type in ("qwen2_5_vl", "qwen2_5_vl_text"):
        # The config can specify a window even when the decoder has disabled SWA.
        if not cfg.get("use_sliding_window") or not cfg.get("sliding_window"):
            return ["full_attention"] * n
        first_window_layer = cfg.get("max_window_layers", 80)
        return ["sliding_attention" if i >= first_window_layer else "full_attention" for i in range(n)]
    if cfg.get("sliding_window") and cfg.get("use_sliding_window", True):
        sliding = cfg.get("max_window_layers", n)
        return ["sliding_attention" if i < sliding else "full_attention" for i in range(n)]
    return ["full_attention"] * n


def sparse_index_layers(cfg, n):
    """Match vLLM compute_skip_topk_layers before partitioning the backbone into PP ranks."""
    types = cfg.get("indexer_types")
    if types is not None:
        if not isinstance(types, list) or len(types) != n or any(t not in ("full", "shared") for t in types):
            raise UnknownLayoutError("sparse MLA indexer_types")
        return [t == "full" for t in types]
    pattern = cfg.get("index_topk_pattern")
    if pattern is not None:
        if not isinstance(pattern, str) or set(pattern) - {"F", "S"} or len(pattern) > n:
            raise UnknownLayoutError("sparse MLA index_topk_pattern")
        return [i >= len(pattern) or pattern[i] == "F" for i in range(n)]
    # A legacy projected config did not retain index reuse settings. Charge only
    # the latents in that case so it can still give a ceiling on token capacity.
    if "index_topk_freq" not in cfg:
        return [False] * n
    freq = positive(cfg["index_topk_freq"], "index_topk_freq")
    offset = cfg.get("index_skip_topk_offset", 2)
    if type(offset) is not int or offset < 0:
        raise UnknownLayoutError("sparse MLA index_skip_topk_offset")
    return [max(i - offset + 1, 0) % freq == 0 for i in range(n)]


def sparse_mla_layout(cfg, flags, notes):
    """vLLM DeepSeek-V3.2 / GLM-MoE-DSA rows, replicated across TP ranks."""
    n = positive(cfg.get("num_hidden_layers"), "num_hidden_layers")
    if n > 4096 or (cfg.get("kv_lora_rank"), cfg.get("qk_rope_head_dim"), cfg.get("index_head_dim")) != (512, 64, 128):
        raise UnknownLayoutError("sparse MLA latent/index dimensions")
    if cfg.get("sliding_window") or cfg.get("layer_types") or cfg.get("linear_attn_config"):
        raise UnknownLayoutError("mixed sparse MLA layer layout")
    opts = json.loads(flags.get("attention-config", "{}"))
    index = flags.get("attention-config.indexer-kv-dtype", opts.get("indexer_kv_dtype", "auto"))
    key_width = {"auto": 132, "fp8": 132, "mxfp4": 68}.get(index)
    if key_width is None:
        raise UnknownLayoutError(f"sparse MLA index dtype: {index}")
    kv = flags.get("kv-cache-dtype", "auto")
    backend = flags.get("attention-backend") or opts.get("backend")
    if kv in ("fp8_ds_mla", "nvfp4_ds_mla"):
        latent = 656 if kv == "fp8_ds_mla" else 352
    elif kv in ("fp8", "fp8_e4m3", "fp8_e5m2"):
        # FlashMLA converts FP8 to a 656-byte row. Other backends may use plain FP8.
        latent = 656 if backend == "FLASHMLA_SPARSE" else 576
    elif kv == "auto":
        # The GPU backend and checkpoint can select the packed format at startup.
        # Use the smallest supported physical row, not the weight dtype.
        latent = 352
        notes.append("Sparse MLA auto format unresolved: using the 352-byte packed latent row as a lower bound.")
    elif kv == "bfloat16" and cfg["model_type"] == "deepseek_v32" and backend in (None, "FLASHINFER_MLA_SPARSE_SM120"):
        # vLLM normalizes explicit BF16 to auto on V3.2; the SM120 backend uses a packed row.
        latent = 656
    else:
        latent = 576 * dtype_bytes(kv)
    has_index = sparse_index_layers(cfg, n)
    if not any(has_index):
        notes.append("Index rows are excluded from the floor because their placement was not established.")
    kinds = ["indexed_mla" if full else "shared_index_mla" for full in has_index]
    row = Component("MLA " + kv, latent, payload_floor=True)
    layouts = {
        "indexed_mla": [row, Component("index " + resolve_dtype(index, "fp8"), key_width)],
        "shared_index_mla": [row],
    }
    return kinds, layouts, [], kv


def components(config, flags, engine, tp, architecture_notes, *, gpu_type=None):
    cfg = text_config(config)
    if engine == "vllm" and cfg.get("model_type") == "glm5_next_text":
        from app.services.kv_glm_flash import glm_flash_layout

        return glm_flash_layout(cfg, flags, tp, gpu_type, architecture_notes)
    if engine == "vllm" and cfg.get("model_type") in ("deepseek_v32", "glm_moe_dsa") and cfg.get("index_topk"):
        return sparse_mla_layout(cfg, flags, architecture_notes)
    kimi = config.get("model_type") == "kimi_k31" or (cfg.get("model_type") == "kimi_linear" and cfg.get("dsa_config"))
    dtype = resolve_dtype(flags.get("dtype"), cfg.get("torch_dtype") or cfg.get("dtype"))
    if dtype == "float32" and flags.get("dtype", "auto") == "auto":
        dtype = "float16"
        architecture_notes.append("auto dtype: float32 checkpoint → float16")
    kv = resolve_dtype(flags.get("kv-cache-dtype"), "fp8" if kimi and engine == "vllm" else dtype)
    kinds = layer_kinds(cfg, kimi)
    layouts = {}
    missing = []
    for kind in dict.fromkeys(kinds):
        try:
            if kind in ("linear_attention", "kda", "mamba", "mamba2"):
                width = state_width(cfg, kind, tp, dtype, flags)
                result = [Component(kind, width, state=True)]
            elif kind == "dsa":
                if engine != "vllm" or cfg.get("kv_lora_rank") != 512 or cfg["dsa_config"]["index_head_dim"] != 128:
                    raise UnknownLayoutError("Kimi DSA backend and dimensions")
                raw = flags.get("attention-config", "{}")
                attention = json.loads(raw)
                index = flags.get("attention-config.indexer-kv-dtype", attention.get("indexer_kv_dtype", "auto"))
                index = resolve_dtype(index, "fp8")
                latent = 576 if str(kv).startswith("fp8") else {"nvfp4": 352, "nvfp4_ds_mla": 352}.get(kv)
                key = {"fp8": 132, "mxfp4": 68}.get(index)
                if not latent or not key:
                    raise UnknownLayoutError("Kimi latent/index format")
                result = [Component("MLA " + kv, latent), Component("index " + index, key)]
            elif kind in ("attention", "full_attention", "sliding_attention"):
                window = positive(cfg.get("sliding_window"), "sliding_window") if kind == "sliding_attention" else 0
                if cfg.get("kv_lora_rank"):
                    if (
                        engine == "vllm"
                        and kv in ("fp8_ds_mla", "nvfp4_ds_mla")
                        and cfg.get("kv_lora_rank") == 512
                        and cfg.get("qk_rope_head_dim") == 64
                    ):
                        width = 656 if kv == "fp8_ds_mla" else 352
                        result = [Component("MLA " + kv, width)]
                        if cfg.get("index_topk"):
                            index = json.loads(flags.get("attention-config", "{}")).get("indexer_kv_dtype", "auto")
                            if cfg.get("index_head_dim") != 128 or index not in ("auto", "fp8", "mxfp4"):
                                raise UnknownLayoutError("DeepSeek index dimensions/format")
                            result.append(Component("index " + index, 68 if index == "mxfp4" else 132))
                        layouts[kind] = result
                        continue
                    if cfg.get("index_topk") or cfg.get("dsa_config") or (dtype_bytes(kv) == 1):
                        raise UnknownLayoutError("MLA packed cache layout")
                    width = (int(cfg["kv_lora_rank"]) + int(cfg["qk_rope_head_dim"])) * dtype_bytes(kv)
                    result = [Component("MLA", width, window)]
                else:
                    heads = positive(cfg.get("num_attention_heads"), "num_attention_heads")
                    if engine == "vllm" and cfg.get("model_type") in _VL_AND_GLM_GQA:
                        shard(heads, tp)
                    kv_heads = positive(cfg.get("num_key_value_heads", heads), "num_key_value_heads")
                    local_heads = shard(kv_heads, tp) if kv_heads >= tp else 1
                    if kv_heads < tp:
                        shard(tp, kv_heads)
                    dim = positive(cfg.get("head_dim") or shard(cfg["hidden_size"], heads), "head_dim")
                    result = [Component(kind, 2 * local_heads * dim * dtype_bytes(kv), window)]
            else:
                raise UnknownLayoutError(str(kind))
            layouts[kind] = result
        except (TypeError, ValueError, KeyError, AttributeError) as exc:
            missing.append(str(exc))
            layouts[kind] = []
    return kinds, layouts, missing, kv


def pp_ranks(kinds, layouts, pp, partition=None):
    if partition:
        sizes = [positive(s, "VLLM_PP_LAYER_PARTITION") for s in partition.split(",")]
        if sum(sizes) != len(kinds) or len(sizes) != pp:
            raise UnknownLayoutError("VLLM_PP_LAYER_PARTITION")
    else:
        base, extra = divmod(len(kinds), pp)
        if not base:
            raise UnknownLayoutError("pipeline stages exceed layers")
        # vLLM puts remainder in the penultimate stages to leave room for embeddings.
        sizes = [base] * pp
        for i in range(extra):
            sizes[pp - 2 - i] += 1
    ranks = []
    start = 0
    for size in sizes:
        ranks.append(Counter(component for kind in kinds[start : start + size] for component in layouts[kind]))
        start += size
    return ranks
