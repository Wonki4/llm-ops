"""CPU metadata job. Read configuration, indexes and local safetensors headers, never tensor data."""

import hashlib
import json
import math
import os
import re
import tempfile
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

HUB_ENV = frozenset({
    "HF_ENDPOINT", "HF_HOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE",
    "HF_TOKEN", "HF_TOKEN_PATH", "HF_HUB_DISABLE_IMPLICIT_TOKEN", "HUGGING_FACE_HUB_TOKEN",
    "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE",
})

FILES = (
    "config.json",
    "model.safetensors.index.json",
    "pytorch_model.bin.index.json",
    "quantization_config.json",
    "quantize_config.json",
    "quant_config.json",
)
MAX_BYTES = 16 << 20
# Only structural fields used by the calculator leave the namespace.
FIELDS = set(
    """model_type num_hidden_layers hidden_size num_attention_heads num_key_value_heads head_dim
    layer_types layers_block_type full_attention_interval max_window_layers sliding_window use_sliding_window
    torch_dtype dtype max_position_embeddings kv_lora_rank qk_rope_head_dim index_topk index_head_dim expand dsa_config
    indexer_types index_topk_pattern index_topk_freq index_skip_topk_offset
    linear_attn_config linear_num_key_heads linear_num_value_heads linear_key_head_dim linear_value_head_dim
    linear_conv_kernel_dim mamba_ssm_dtype intermediate_size mamba_d_inner state_size mamba_d_state
    conv_kernel mamba_d_conv n_groups mamba_n_groups hybrid_override_pattern
    compress_ratios compress_rates kv_source_layer_ids index_source_layer_ids
    candidate_source_layer_id candidate_topk_blocks candidate_block_size dspark_block_size
    num_nextn_predict_layers n_mtp_layers dspark_target_layer_ids index_kpool index_kpool_compress
    index_kpool_always_select_tail mla_use_nope linear_num_heads linear_head_dim
    index_n_heads engram_layer_ids engram_num_embeddings engram_head_dim""".split()
)


def structural(raw):
    cfg = {k: raw[k] for k in FIELDS if k in raw}
    architectures = raw.get("architectures")
    if isinstance(architectures, list) and len(architectures) <= 16 and all(
        isinstance(value, str) and len(value) <= 128 for value in architectures
    ):
        cfg["architectures"] = architectures
    if cfg.get("model_type") in {"llama", "qwen2", "qwen3", "qwen3_moe", "mistral"}:
        for key in ("vocab_size", "num_experts", "num_experts_per_tok", "moe_intermediate_size", "decoder_sparse_step"):
            if type(raw.get(key)) is int and 0 < raw[key] <= 2**32:
                cfg[key] = raw[key]
        if type(raw.get("tie_word_embeddings")) is bool:
            cfg["tie_word_embeddings"] = raw["tie_word_embeddings"]
        dense_layers = raw.get("mlp_only_layers")
        if isinstance(dense_layers, list) and len(dense_layers) <= 4096 and all(type(i) is int for i in dense_layers):
            cfg["mlp_only_layers"] = dense_layers
    # V4.1 stores its MoE dimensions on the text backbone but specifies
    # the expert packing separately on the multimodal checkpoint.
    if cfg.get("model_type") in ("deepseek_v41", "deepseek_v41_text"):
        for key in ("n_routed_experts", "moe_intermediate_size"):
            if type(raw.get(key)) is int and raw[key] > 0:
                cfg[key] = raw[key]
        quant = raw.get("quantization_config")
        if isinstance(quant, dict):
            packing = {k: quant[k] for k in ("quant_method", "expert_dtype")
                       if isinstance(quant.get(k), str) and 0 < len(quant[k]) <= 64}
            if packing:
                cfg["quantization_config"] = packing
    if raw.get("speculators_model_type") == "dspark":
        for key in ("speculators_model_type", "sample_from_anchor", "block_size"):
            if key in raw:
                cfg[key] = raw[key]
        if isinstance(raw.get("transformer_layer_config"), dict):
            cfg["transformer_layer_config"] = structural(raw["transformer_layer_config"])
    # Models can skip the sparse-attention indexer on selected layers. Record the
    # default explicitly so older projected configs cannot imply index use on every layer.
    if cfg.get("model_type") in ("glm_moe_dsa", "deepseek_v32"):
        cfg.setdefault("index_topk_freq", 1)
    for key in ("text_config", "language_config", "llm_config"):
        if isinstance(raw.get(key), dict):
            cfg[key] = structural(raw[key])
    return cfg


def read_json(path, digests=None):
    if not path.is_file():
        return None
    # Some sharded MoE releases have over 280,000 tensor names (24 MiB index).
    limit = 64 << 20 if path.name in (*FILES[1:3], "consolidated.safetensors.index.json") else MAX_BYTES
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"{path.name} exceeds {limit >> 20} MiB")
    if digests is not None and path.name in ("config.json", "params.json"):
        digests["config"] = hashlib.sha256(data).hexdigest()
    if digests is not None and path.name in (*FILES[1:3], "consolidated.safetensors.index.json"):
        digests["index"] = hashlib.sha256(data).hexdigest()
    return json.loads(data)


def local_tensor_directory(path, header_limit=MAX_BYTES, digests=None):
    """Read the safetensors JSON directory without mapping the tensor payload."""
    if not path.is_file():
        return None
    size = path.stat().st_size
    with path.open("rb") as handle:
        prefix = handle.read(8)
        header_size = int.from_bytes(prefix, "little")
        if not 0 < header_size <= min(header_limit, size - 8):
            return None
        try:
            data = handle.read(header_size)
            header = json.loads(data)
            if digests is not None:
                digests["tensor"] = hashlib.sha256(prefix + data).hexdigest()
        except ValueError:
            return None
        return (header, size - 8 - header_size, header_size) if isinstance(header, dict) else None


def weight_group(name, layers):
    if name.startswith(("vision.", "aligner.", "model.visual.")):
        return "vision"
    if re.fullmatch(r"layers\.\d+\.engram\.embed\.(weight|scale)", name):
        return "engram_embeddings"
    match = re.match(r"(?:model\.language_model\.)?layers\.(\d+)\.", name)
    if name.startswith("mtp.") or (match and int(match[1]) >= layers):
        return "draft"
    return None


def optional_weight_bytes(root, cfg, index):
    """Resolve selectable checkpoint weights using the mounted shard index and byte offsets."""
    text = cfg.get("text_config") or cfg
    layers = text.get("num_hidden_layers")
    if text.get("model_type") not in {"deepseek_v4", "deepseek_v41_text", "glm5_next_text"} or type(layers) is not int:
        return None
    weight_map = (index or {}).get("weight_map")
    if not isinstance(weight_map, dict) or not weight_map:
        return None
    groups = {"engram_embeddings": 0, "draft": 0, "vision": 0}
    selected = {}
    for name, shard in weight_map.items():
        group = weight_group(name, layers)
        if group:
            if not isinstance(shard, str) or Path(shard).name != shard or not shard.endswith(".safetensors"):
                return None
            selected.setdefault(shard, []).append((name, group))
    # Optional sizing must not walk a large weight store to return the basic
    # model config. The audited public checkpoints need at most six headers.
    if len(selected) > 64:
        return None
    header_budget = 64 << 20
    for shard, entries in selected.items():
        try:
            directory = local_tensor_directory(root / shard, min(MAX_BYTES, header_budget))
        except OSError:
            return None
        if directory is None:
            return None
        header, size, header_size = directory
        header_budget -= header_size
        for name, group in entries:
            offsets = header.get(name, {}).get("data_offsets")
            if (not isinstance(offsets, list) or len(offsets) != 2 or not all(type(v) is int for v in offsets)
                    or not 0 <= offsets[0] <= offsets[1] <= size):
                return None
            groups[group] += offsets[1] - offsets[0]
    return groups


def draft_weights(root, index, local):
    """Count draft checkpoint payload without double charging the target embedding/output head."""
    def shared(name):
        return bool({"embed_tokens", "lm_head"} & set(name.split(".")))

    if index:
        names = index.get("weight_map")
        size = (index.get("metadata") or {}).get("total_size")
        if isinstance(names, dict) and names and not any(shared(name) for name in names):
            return size if type(size) is int and size > 0 else None
        return None
    path = root / "model.safetensors"
    directory = local_tensor_directory(path) if local else None
    if directory is None:
        return None
    header, payload_size, _ = directory
    offset = total = 0
    for name, tensor in sorted(
        ((k, v) for k, v in header.items() if k != "__metadata__"),
        key=lambda item: item[1]["data_offsets"][0],
    ):
        start, end = tensor["data_offsets"]
        shape = tensor["shape"]
        if (tensor["dtype"] not in ("BF16", "F16") or start != offset or not isinstance(shape, list)
                or not all(type(d) is int and d >= 0 for d in shape) or end - start != 2 * math.prod(shape)):
            return None
        offset = end
        if not shared(name):
            total += end - start
    return total if total > 0 and offset == payload_size else None


class MetadataReadError(ValueError):
    def __init__(self, code, fmt=None):
        self.code = code
        self.fmt = fmt
        super().__init__(code)


def safe_failure(exc):
    # HTTP exception text can include signed URLs, request headers or local paths.
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(exc, MetadataReadError):
        code = exc.code
    elif status in (401, 403) or type(exc).__name__ == "GatedRepoError":
        code = "hub_access_required"
    elif type(exc).__name__ == "RevisionNotFoundError":
        code = "hub_revision_missing"
    elif isinstance(exc, PermissionError):
        code = "mount_unavailable"
    else:
        code = "metadata_job_unavailable"
    data = {"error": code, "metadata_issue": code}
    if isinstance(exc, MetadataReadError) and exc.fmt in {"step35_q8_0", "flint_nvfp4"}:
        data["metadata_format"] = exc.fmt
    return data


def native_mistral_config(raw):
    """Project a sparse MLA checkpoint's native serving configuration without running remote code."""
    names = {"dim": "hidden_size", "n_heads": "num_attention_heads", "n_kv_heads": "num_key_value_heads",
             "n_layers": "num_hidden_layers", "head_dim": "head_dim", "kv_lora_rank": "kv_lora_rank",
             "qk_rope_head_dim": "qk_rope_head_dim", "max_position_embeddings": "max_position_embeddings"}
    if not all(type(raw.get(k)) is int and 0 < raw[k] <= 2**32 for k in names):
        raise MetadataReadError("native_config_unsupported")
    dsa = raw.get("dsa") or {}
    if not (isinstance(dsa, dict) and isinstance(raw.get("kv_sharing"), dict)
            and 0 < raw["n_layers"] <= 4096 and type(dsa.get("topk")) is int and dsa["topk"] > 0
            and type(dsa.get("head_dim")) is int and dsa["head_dim"] > 0):
        raise MetadataReadError("native_config_unsupported")
    sharing = raw["kv_sharing"].get("source_layers_str", "").split(",")
    indexer = dsa.get("sharing", "").split(",")
    if (len(sharing) != raw["n_layers"] or len(indexer) != len(sharing)
            or not all(v.isdecimal() and int(v) <= layer for layer, v in enumerate(sharing))
            or not set(indexer) <= {"full", "shared"}):
        raise MetadataReadError("native_config_unsupported")
    return {"model_type": "mistral_large4_text", **{v: raw[k] for k, v in names.items()},
            "kv_source_layer_ids": list(map(int, sharing)), "indexer_types": indexer,
            "index_topk": dsa["topk"], "index_head_dim": dsa["head_dim"]}


def apply_hub_env():
    # These names are generated only for allowlisted named refs. Resolve absent
    # optional keys and multiple sources in Kubernetes declaration order.
    values = []
    for name, value in os.environ.items():
        match = re.fullmatch(r"KV_HUB_(\d+)_(.+)", name)
        if match and match[2] in HUB_ENV:
            values.append((int(match[1]), match[2], value))
    for _, name, value in sorted(values):
        os.environ[name] = value


def selected_gguf(path):
    # Exact released GGUF header prefixes classify the affected quantization even
    # if renamed on a mount. Neither filename nor shape proves the quant type.
    with path.open("rb") as handle:
        signature = hashlib.sha256(handle.read(65536)).hexdigest()
    return {
        "a4436c36605e966e33e2810dd9a9151ee33002db87464bf15b1e07e3cc6ca9a8": "step35_q8_0",
        "c4982ddc908912ff105d2053293f7f35d87b56927b66e465cc6d67e0c1c45c3f": "flint_nvfp4",
    }.get(signature)


def inspect(model, revision, config_format="auto"):
    digests = {}
    root = Path(model)
    local = root.is_dir()
    if root.is_file() and root.suffix.lower() == ".gguf":
        raise MetadataReadError("gguf_layout_unsupported", selected_gguf(root))
    if not root.is_absolute() and (root.suffix.lower() == ".gguf" or re.fullmatch(r"[\w.-]+/[\w.-]+:[\w.-]+", model)):
        raise MetadataReadError("gguf_layout_unsupported")
    if root.is_absolute() and not local:
        raise MetadataReadError("mount_unavailable")
    if not local:
        from huggingface_hub import hf_hub_download, try_to_load_from_cache
        from huggingface_hub.errors import EntryNotFoundError, LocalEntryNotFoundError

    offline = any(
        os.getenv(key, "").lower() in ("1", "true", "yes") for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")
    )
    with tempfile.TemporaryDirectory() as scratch:

        def get(name):
            nonlocal revision
            if local:
                return read_json(root / name, digests)
            path = try_to_load_from_cache(model, name, revision=revision)
            if not isinstance(path, str):
                if offline:
                    return None
                try:
                    path = hf_hub_download(model, name, revision=revision, cache_dir=scratch)
                except LocalEntryNotFoundError:
                    raise MetadataReadError("metadata_job_unavailable") from None
                except EntryNotFoundError:
                    return None
            path = Path(path)
            if path.parent.parent.name == "snapshots":
                revision = path.parent.name
            return read_json(path, digests)

        raw = None if config_format == "mistral" else get(FILES[0])
        native = False
        if raw is None and config_format in ("auto", "mistral"):
            raw = get("params.json")
            native = raw is not None
        if not isinstance(raw, dict):
            if local and next(root.glob("*.gguf"), None):
                raise MetadataReadError("gguf_selection_required")
            raise MetadataReadError("offline_cache_missing" if offline and not local else "config_missing")
        cfg = native_mistral_config(raw) if native else structural(raw)
        weights = None
        safetensor_index = None
        for name in (("consolidated.safetensors.index.json",) if native else FILES[1:3]):
            index = get(name)
            if index:
                weights = (index.get("metadata") or {}).get("total_size")
                if name == "model.safetensors.index.json":
                    safetensor_index = index
                break
        if not weights and local:
            # stat gives the serialized size of an unindexed checkpoint without opening it.
            shard = root / ("consolidated.safetensors" if native else "model.safetensors")
            if shard.is_file():
                weights = shard.stat().st_size
                local_tensor_directory(shard, header_limit=1 << 20, digests=digests)
        quant = raw.get("quantization_config")
        if not quant:
            for name in FILES[3:]:
                quant = get(name)
                if quant:
                    break
        digest = hashlib.sha256(json.dumps([raw, weights, quant], sort_keys=True).encode()).hexdigest()
        versions = {}
        for package in ("vllm", "sglang", "flashinfer-python"):
            try:
                versions[package] = version(package)
            except PackageNotFoundError:
                pass
        return {
            **({"weight_components": group_bytes}
               if local and (group_bytes := optional_weight_bytes(root, cfg, safetensor_index)) is not None else {}),
            **({"draft_weight_bytes": draft_weights(root, safetensor_index, local)}
               if cfg.get("speculators_model_type") == "dspark" and not quant else {}),
            "engine_versions": versions,
            "config": cfg,
            "revision": revision,
            "digest": digest,
            "config_digest": digests.get("config"),
            **({"weight_index_digest": digests["index"]} if "index" in digests else {}),
            **({"speculative_tensor_digest": digests["tensor"]} if "tensor" in digests else {}),
            "weight_bytes": weights,
            "weight_format": (quant or {}).get("quant_method"),
            "source": "mount" if local else "hub",
            **({"hub_origin": "mirror"} if not local and os.getenv("HF_ENDPOINT", "").rstrip("/")
               not in ("", "https://huggingface.co") else {}),
        }


if __name__ == "__main__":
    try:
        apply_hub_env()
        result = inspect(os.environ["KV_MODEL"], os.getenv("KV_REVISION") or None,
                         os.getenv("KV_CONFIG_FORMAT", "auto"))
    except Exception as exc:
        result = safe_failure(exc)
    print("KV_METADATA=" + json.dumps(result, separators=(",", ":")), flush=True)
