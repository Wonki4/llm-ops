"""CPU metadata job entrypoint. Read only known JSON files; never import model code or open shards."""

import hashlib
import json
import os
import tempfile
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

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
    linear_attn_config linear_num_key_heads linear_num_value_heads linear_key_head_dim linear_value_head_dim
    linear_conv_kernel_dim mamba_ssm_dtype intermediate_size mamba_d_inner state_size mamba_d_state
    conv_kernel mamba_d_conv n_groups mamba_n_groups hybrid_override_pattern""".split()
)


def structural(raw):
    cfg = {k: raw[k] for k in FIELDS if k in raw}
    for key in ("text_config", "language_config", "llm_config"):
        if isinstance(raw.get(key), dict):
            cfg[key] = structural(raw[key])
    return cfg


def read_json(path):
    if not path.is_file():
        return None
    with path.open("rb") as handle:
        data = handle.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("metadata file exceeds 16 MiB")
    return json.loads(data)


def inspect(model, revision):
    root = Path(model)
    local = root.is_dir()
    if root.is_absolute() and not local:
        raise ValueError("model directory unavailable on the selected mount")
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
                return read_json(root / name)
            path = try_to_load_from_cache(model, name, revision=revision)
            if not isinstance(path, str):
                if offline:
                    return None
                try:
                    path = hf_hub_download(model, name, revision=revision, cache_dir=scratch)
                except (EntryNotFoundError, LocalEntryNotFoundError):
                    return None
            path = Path(path)
            if path.parent.parent.name == "snapshots":
                revision = path.parent.name
            return read_json(path)

        raw = get(FILES[0])
        if not isinstance(raw, dict):
            raise ValueError("config.json unavailable")
        cfg = structural(raw)
        weights = None
        for name in FILES[1:3]:
            index = get(name)
            if index:
                weights = (index.get("metadata") or {}).get("total_size")
                break
        if not weights and local:
            # stat gives the serialized size of an unindexed checkpoint without opening it.
            shard = root / "model.safetensors"
            if shard.is_file():
                weights = shard.stat().st_size
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
            "engine_versions": versions,
            "config": cfg,
            "revision": revision,
            "digest": digest,
            "weight_bytes": weights,
            "weight_format": (quant or {}).get("quant_method"),
            "source": "mount" if local else "hub",
        }


if __name__ == "__main__":
    try:
        result = inspect(os.environ["KV_MODEL"], os.getenv("KV_REVISION") or None)
    except Exception as exc:
        result = {"error": str(exc) if type(exc) is ValueError else type(exc).__name__}
    print("KV_METADATA=" + json.dumps(result, separators=(",", ":")), flush=True)
