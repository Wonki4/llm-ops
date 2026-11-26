"""GPU-resident weight bounds for pre-launch KV memory estimates."""

import json

from app.services.kv_draft_capacity import dspark_config
from app.services.kv_layout import UnknownLayoutError, dtype_bytes, positive, shard, text_config

DEEPSEEK_V4_TYPES = {"deepseek_v4", "deepseek_v41_text"}


def _boolean(value, option):
    if value is True or value == "true":
        return True
    if value is False or value == "false":
        return False
    raise UnknownLayoutError(option)


def _offloads_engram(flags):
    # vLLM appends the combined dotted object after the standalone JSON object.
    dotted = {k.removeprefix("engram-config.").replace("-", "_"): v for k, v in flags.items()
              if k.startswith("engram-config.")}
    config = dotted or json.loads(flags.get("engram-config", "{}"))
    if not isinstance(config, dict):
        raise UnknownLayoutError("engram-config must be an object")
    if int(config.get("peer_group_size") or 1) > 1 or config.get("peer_group_store"):
        raise UnknownLayoutError("cross-engine Engram table placement")
    return _boolean(config.get("cpu_offload", True), "engram-config.cpu_offload")


def _v41_uva_expert_bytes(config, flags):
    """vLLM UVA checks the limit before moving each complete routed-expert parameter."""
    cfg = text_config(config)
    quant = config.get("quantization_config") or cfg.get("quantization_config") or {}
    if (cfg.get("model_type") != "deepseek_v41_text" or flags.get("offload-backend", "auto") not in ("auto", "uva")
            or quant.get("expert_dtype") != "fp4" or quant.get("quant_method") != "fp8"
            or flags.get("enable-eplb", "").lower() in ("true", "1")):
        raise UnknownLayoutError("GPU-resident bytes with the selected weight offloader")
    experts = positive(cfg.get("n_routed_experts"), "routed expert count")
    hidden = positive(cfg.get("hidden_size"), "expert hidden size")
    intermediate = positive(cfg.get("moe_intermediate_size"), "expert intermediate size")
    layers = positive(cfg.get("num_hidden_layers"), "routed expert layers")
    # This MXFP4 layout is verified for aligned, unpadded weights on one rank.
    if hidden % 256 or intermediate % 256:
        raise UnknownLayoutError("routed expert weight padding")
    w2 = experts * hidden * intermediate // 2
    tensors = {"routed_experts.w13_weight": 2 * w2, "routed_experts.w2_weight": w2}
    selected = set(flags.get("cpu-offload-params", "").split())
    if not selected or not selected <= tensors.keys():
        raise UnknownLayoutError("UVA weight placement for the selected parameters")
    # vLLM registers w13 then w2. Their scale tensors stay on the device.
    sizes = [size for name, size in tensors.items() if name in selected]
    threshold = int(float(flags["cpu-offload-gb"]) * (1 << 30))
    per_layer = sum(sizes)
    full, remainder = divmod(threshold, per_layer)
    if full >= layers:
        return layers * per_layer
    moved = full * per_layer
    for size in sizes:
        if remainder <= 0:
            break
        moved += size
        remainder -= size
    return moved


def _vision_disabled(flags):
    if _boolean(flags.get("language-model-only", False), "language-model-only"):
        return True
    limits = json.loads(flags.get("limit-mm-per-prompt", "{}"))
    if not isinstance(limits, dict):
        raise UnknownLayoutError("limit-mm-per-prompt must be an object")
    for modality in ("image", "video"):
        value = flags.get("limit-mm-per-prompt." + modality, limits.get(modality))
        if isinstance(value, dict):
            value = value.get("count")
        if value in (0, "0") or flags.get(f"limit-mm-per-prompt.{modality}.count") == "0":
            # An image/video tower may be shared. Its omission here gives the
            # optimistic memory ceiling when the engine's tower choice is unknown.
            return True
    return False


def checkpoint_residency(metadata, flags):
    """Subtract disjoint checkpoint tensors the recipe keeps off this GPU replica."""
    model_type = text_config(metadata["config"]).get("model_type")
    if model_type not in DEEPSEEK_V4_TYPES and model_type != "glm5_next_text":
        return None
    total = metadata.get("weight_bytes")
    if type(total) is not int or total <= 0:
        raise UnknownLayoutError("checkpoint tensor payload")
    parts = metadata.get("weight_components") or {}
    local_model = flags.get("model") or flags.get("model-path")
    draft = dspark_config(flags)
    integrated = draft is not None and draft.get("model") in (None, local_model)
    omit_vision = _vision_disabled(flags)
    engram_cpu = model_type == "deepseek_v41_text" and _offloads_engram(flags)
    excluded = {}
    for key, omit in (("draft", not integrated), ("vision", omit_vision), ("engram_embeddings", engram_cpu)):
        if omit:
            size = parts.get(key)
            if type(size) is not int or size < 0:
                raise UnknownLayoutError("checkpoint tensor sizes for " + key)
            excluded[key] = size
    if float(flags.get("cpu-offload-gb", 0)) > 0:
        excluded["routed_experts"] = _v41_uva_expert_bytes(metadata["config"], flags)
    resident = total - sum(excluded.values())
    if resident <= 0:
        raise UnknownLayoutError("checkpoint component sizes exceed weight payload")
    return {"checkpoint_bytes": total, "resident_weight_bytes": resident, "excluded_bytes": excluded}


def _dense_layer_parameters(cfg, tp):
    """Elements in vLLM's rank-local packed decoder tensors for these dense architectures."""
    if cfg.get("model_type") not in {"qwen2", "qwen3", "llama", "mistral"}:
        raise UnknownLayoutError("dense decoder tensor shapes for the selected weight offloader/precision")
    hidden = positive(cfg.get("hidden_size"), "hidden_size")
    heads = positive(cfg.get("num_attention_heads"), "attention heads")
    kv_heads = positive(cfg.get("num_key_value_heads", heads), "KV heads")
    dim = positive(cfg.get("head_dim") or hidden // heads, "head_dim")
    query = shard(heads, tp) * dim
    if kv_heads < tp:
        shard(tp, kv_heads)
    kv = (1 if kv_heads < tp else shard(kv_heads, tp)) * dim
    mlp = shard(positive(cfg.get("intermediate_size"), "intermediate_size"), tp)
    return {
        "self_attn.qkv_proj.weight": (query + 2 * kv) * hidden,
        "self_attn.o_proj.weight": query * hidden,
        "mlp.gate_up_proj.weight": 2 * mlp * hidden,
        "mlp.down_proj.weight": mlp * hidden,
    }


def _dense_uva_range(cfg, flags, tp, pp, width, requested):
    # UVA checks the threshold BEFORE each whole parameter and can exceed it by
    # less than the largest eligible tensor. Embeddings/lm_head are outside make_layers.
    mandatory = _dense_layer_parameters(cfg, tp)
    elements = mandatory.copy()
    hidden = cfg["hidden_size"]
    dim = cfg.get("head_dim") or hidden // cfg["num_attention_heads"]
    elements.update({
        "input_layernorm.weight": hidden,
        "post_attention_layernorm.weight": hidden,
        # Include optional biases to keep a safe ceiling across checkpoint variants.
        "self_attn.qkv_proj.bias": elements["self_attn.qkv_proj.weight"] // hidden,
        "self_attn.o_proj.bias": hidden,
        "mlp.gate_up_proj.bias": elements["mlp.gate_up_proj.weight"] // hidden,
        "mlp.down_proj.bias": hidden,
    })
    if cfg["model_type"] == "qwen3":
        elements.update({"self_attn.q_norm.weight": dim, "self_attn.k_norm.weight": dim})
    filters = flags.get("cpu-offload-params", "").split()
    # Prefixed layer paths depend on model wrappers and the engine version.
    if any("layers" in name.split(".") for name in filters):
        raise UnknownLayoutError("prefix-qualified CPU offload parameter selection")
    selected = {name: n * width for name, n in elements.items()
                if not filters or any(f".{prefix}." in f".{name}." for prefix in filters)}
    if not selected or not requested:
        return 0, 0
    layers = positive(cfg.get("num_hidden_layers"), "number of layers")
    # PP partitions can be explicitly uneven. With one stage every rank owns
    # all these decoder layers and always has at least the mandatory projections.
    eligible = sum(selected.get(name, 0) for name in mandatory) * layers if pp == 1 else 0
    maximum = min(layers * sum(selected.values()), requested + max(selected.values()) - 1)
    return min(requested, eligible), maximum


def resident_weight_floor(metadata, flags, engine, tp, pp, out):
    """Minimum payload on the heaviest rank, before runtime allocations and replication."""
    weights = metadata.get("weight_bytes")
    config = metadata.get("config")
    if not config or not isinstance(weights, int) or weights <= 0:
        raise UnknownLayoutError("checkpoint weight payload")
    cfg = text_config(config)
    cpu = float(flags.get("cpu-offload-gb", 0))
    if (float(flags.get("offload-group-size", 0)) > 0 or cpu < 0
            or (cpu and (engine != "vllm" or flags.get("offload-backend", "auto") not in {"auto", "uva"}))
            or flags.get("quantization") or flags.get("hf-overrides") or flags.get("hf-config-path")
            or flags.get("load-format", "auto") not in ("auto", "safetensors", "fastsafetensors", "pt")):
        raise UnknownLayoutError("GPU-resident weights with the selected loading options")
    model_type = cfg.get("model_type")
    special = model_type in DEEPSEEK_V4_TYPES or model_type == "glm5_next_text"
    stored = cfg.get("torch_dtype") or cfg.get("dtype") or config.get("torch_dtype") or config.get("dtype")
    load = flags.get("dtype", "auto")
    if load == "auto":
        load = "float16" if stored in ("float", "float32", "fp32") else stored
    if not stored:
        raise UnknownLayoutError("checkpoint weight dtype")
    changing = load != stored and dtype_bytes(load) != dtype_bytes(stored)
    if changing and dtype_bytes(load) < dtype_bytes(stored):
        raise UnknownLayoutError("resident weight payload after precision conversion")
    quantized = (metadata.get("weight_format") not in (None, "", "safetensors", "pt")
                 or config.get("quantization_config") or cfg.get("quantization_config"))
    residency = None
    if special and engine == "vllm":
        if cpu and (model_type != "deepseek_v41_text" or tp * pp != 1):
            raise UnknownLayoutError("selected rank-local expert weight placement")
        residency = checkpoint_residency(metadata, flags)
        weights = residency["resident_weight_bytes"]
    elif not special:
        optional = []
        if cfg.get("num_nextn_predict_layers"):
            optional.append("draft")
        if config is not cfg and _vision_disabled(flags):
            optional.append("vision")
            if _boolean(flags.get("language-model-only", False), "language-model-only"):
                optional.append("audio")
        if optional:
            parts = metadata.get("weight_components") or {}
            if any(type(parts.get(k)) is not int or parts[k] < 0 for k in optional):
                # Optional tensors can account for an unknown portion of the index.
                # The device limit is a physical ceiling, never an exact KV budget.
                out["device_only_weight_bound"] = True
                out["weight_fit_unverified"] = True
                out["assumptions"].append("Optional checkpoint component sizes are unknown. Only the GPU profile "
                                          "limits the KV upper bound until the engine reports its memory budget.")
                return 0
            excluded = {k: parts[k] for k in optional}
            weights -= sum(excluded.values())
            if weights <= 0:
                raise UnknownLayoutError("checkpoint component sizes exceed weight payload")
            residency = {"checkpoint_bytes": metadata["weight_bytes"], "resident_weight_bytes": weights,
                         "excluded_bytes": excluded}
            if "draft" in optional or not _boolean(flags.get("language-model-only", False), "language-model-only"):
                out["weight_fit_unverified"] = True
    if changing:
        stored_width, load_width = dtype_bytes(stored), dtype_bytes(load)
        if quantized or config is not cfg:
            raise UnknownLayoutError("resident weight precision of a mixed or quantized checkpoint")
        # An index total and config dtype alone cannot certify that every tensor
        # uses the same dtype. These dense projections must all use the load dtype.
        layers = positive(cfg.get("num_hidden_layers"), "number of layers")
        projections = sum(_dense_layer_parameters(cfg, 1).values()) * layers
        weights = max(weights, projections * load_width)
        out["weight_load_multiplier"] = load_width / stored_width
        out["assumptions"].append("Loaded precision is applied to the dense projection tensors; "
                                  "other tensors may also grow.")
    if residency:
        out["weight_residency"] = residency
        if cpu and special:
            moved = residency["excluded_bytes"]["routed_experts"]
            out["weight_offload"].update(min_bytes_per_gpu=moved, max_bytes_per_gpu=moved)
    workers = tp * pp
    minimum = (weights + workers - 1) // workers
    if cpu and not special:
        if quantized or config is not cfg or flags.get("model-impl", "auto") not in {"auto", "vllm"}:
            raise UnknownLayoutError("CPU weight placement for the selected model implementation")
        requested = out["weight_offload"]["requested_bytes_per_gpu"]
        low, high = _dense_uva_range(cfg, flags, tp, pp, dtype_bytes(load), requested)
        # The heaviest GPU carries at least the replica average minus rank-local offload.
        minimum = max(0, minimum - high)
        out["weight_offload"].update(min_bytes_per_gpu=low, max_bytes_per_gpu=high)
        out["assumptions"].append("CPU weight offload is per GPU. The KV ceiling allows up to the requested "
                                  "bytes plus the final eligible whole parameter; actual placement can be smaller.")
    return minimum
