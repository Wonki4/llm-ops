"""Offline evidence for the Qwen3 sliding-window DSpark cache layout."""

# These commits have identical config.json and model.safetensors. File sizes include
# a 6,656-byte safetensors header; the tensor payload below excludes it. The
# checkpoint contains no target token embedding or LM head tensors.
_KIMI_MODEL = "Inferact/Kimi-K3.1-Pro-DSpark"
_KIMI_REVISIONS = (
    "50f5a10c9385fb9a225c534dda8f7a1f5ae87110",
    "e4e6e6261e9f8b6d5f249f9627eb6b109d762d5f",
)


def resolve_draft(model: str, revision: str | None = None) -> dict | None:
    """Look up public reference data. A floating default is explicitly an assumption."""
    if model != _KIMI_MODEL:
        return None
    floating = revision is None or revision == "main"
    if floating:
        pinned = _KIMI_REVISIONS[0]
    elif isinstance(revision, str) and len(revision) >= 8:
        matches = [commit for commit in _KIMI_REVISIONS if commit.startswith(revision)]
        if len(matches) != 1:
            return None
        pinned = matches[0]
    else:
        return None
    return {
        "revision": pinned,
        "assumed_revision": floating,
        "source": f"https://huggingface.co/{_KIMI_MODEL}/tree/{pinned}",
        "num_hidden_layers": 5,
        "num_attention_heads": 96,
        "num_key_value_heads": 16,
        "head_dim": 64,
        "sliding_window": 2048,
        "max_draft_tokens": 7,
        "sample_from_anchor": True,
        "dtype": "bfloat16",
        "weight_bytes": 4_959_953_154,
    }


def draft_from_metadata(metadata: dict) -> dict | None:
    """Resolve an inspected checkpoint when its entire draft cache uses windowed GQA."""
    cfg = metadata.get("config")
    if not isinstance(cfg, dict) or cfg.get("speculators_model_type") != "dspark" or metadata.get("weight_format"):
        return None
    backbone = cfg.get("transformer_layer_config")
    if not isinstance(backbone, dict) or backbone.get("model_type") != "qwen3":
        return None
    dtype = cfg.get("dtype") or cfg.get("torch_dtype")
    if dtype not in ("bfloat16", "float16") or type(cfg.get("sample_from_anchor")) is not bool:
        return None
    shape = {
        "num_hidden_layers": backbone.get("num_hidden_layers"),
        "num_attention_heads": backbone.get("num_attention_heads"),
        "num_key_value_heads": backbone.get("num_key_value_heads"),
        "head_dim": backbone.get("head_dim"),
        "sliding_window": backbone.get("sliding_window"),
        "max_draft_tokens": cfg.get("block_size"),
    }
    if any(type(n) is not int or n <= 0 for n in shape.values()):
        return None
    if not cfg["sample_from_anchor"]:
        # The fill-in layout uses the first position as a bonus anchor.
        shape["max_draft_tokens"] -= 1
        if shape["max_draft_tokens"] < 1:
            return None
    layer_types = backbone.get("layer_types")
    if not isinstance(layer_types, list) or len(layer_types) != shape["num_hidden_layers"]:
        return None
    if any(t != "sliding_attention" for t in layer_types) or backbone.get("use_sliding_window") is False:
        return None
    if shape["num_attention_heads"] % shape["num_key_value_heads"]:
        return None
    identity = (
        metadata.get("digest")
        if metadata.get("source") == "mount"
        else metadata.get("revision") or metadata.get("digest")
    )
    if not identity or not metadata.get("source"):
        return None
    # Explicit and startup KV budgets have already excluded the model weights.
    # Never substitute whole-file size when the unique draft payload is unknown.
    weight_bytes = metadata.get("draft_weight_bytes")
    if weight_bytes is not None and (type(weight_bytes) is not int or weight_bytes <= 0):
        return None
    return {
        **shape,
        "weight_bytes": weight_bytes,
        "revision": identity,
        "assumed_revision": False,
        "source": metadata["source"],
        "sample_from_anchor": cfg["sample_from_anchor"],
        "dtype": dtype,
    }
