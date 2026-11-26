"""Offline GPU specifications and model-size evidence for text generation forecasts.

Device values are per GPU, with dense tensor throughput in decimal TFLOP/s
and device memory bandwidth in GB/s. No peak is a serving throughput result.
"""

import json
import math
import re
from functools import lru_cache
from pathlib import Path

from app.services.kv_layout import text_config

_AIC = (
    "https://github.com/ai-dynamo/aiconfigurator/blob/"
    "6cd6f9686ab7de6e8fb28171d6e350ee0aa4ed39/aic-core/src/aiconfigurator_core/systems/"
)
_AMD = "https://rocm.docs.amd.com/projects/ai-ecosystem/en/latest/optimization/workload-optimization.html"
_RTX = "https://images.nvidia.com/aem-dam/Solutions/geforce/blackwell/nvidia-rtx-blackwell-gpu-architecture.pdf"

# (BF16, FP8 where supported, memory BW, specification, assumed board/derivation)
_GPUS = {
    "h100": (989, 1978, 3350, _AIC + "h100_sxm.yaml", "H100 SXM"),
    "h100-pcie": (756.5, 1513, 2000, _AIC + "h100_pcie.yaml", "H100 PCIe"),
    "h200": (989, 1978, 4800, _AIC + "h200_sxm.yaml", "H200 SXM"),
    "a100-80gb": (312, None, 2039, _AIC + "a100_sxm.yaml", "A100 SXM 80 GB"),
    "a100-80gb-pcie": (312, None, 1935, _AIC + "a100_pcie.yaml", "A100 PCIe 80 GB"),
    "l4": (121, 242, 300, _AIC + "l4.yaml", "L4"),
    "l40s": (362.05, 733, 864, _AIC + "l40s.yaml", "L40S"),
    "b200": (2250, 4500, 7700, _AIC + "b200_sxm.yaml", "B200 SXM"),
    "b300": (2250, 4500, 7750, _AIC + "b300_sxm.yaml", "B300 SXM"),
    "gb200": (2500, 5000, 8000, _AIC + "gb200.yaml", "GB200 NVLink"),
    "gb300": (2500, 5000, 8000, _AIC + "gb300.yaml", "GB300 NVLink"),
    "rtx-pro-6000": (467.8, 935.6, 1792, _AIC + "rtx_pro_6000_server.yaml", "RTX PRO 6000 Blackwell Server"),
    "rtx-4090": (165.2, 330.3, 1008, _RTX, "RTX 4090, FP32 accumulation"),
    "rtx-5090": (209.5, 419, 1792, _RTX, "RTX 5090, FP32 accumulation"),
    "mi300x": (1300, 2600, 5300, _AMD, "MI300X OAM"),
    "mi325x": (1300, 2600, 6000, _AMD, "MI325X OAM"),
    "mi350x": (2300, 4600, 8000, _AMD, "MI350X OAM"),
    "mi355x": (2500, 5000, 8000, _AMD, "MI355X OAM"),
    "arc-pro-b60": (98.304, None, 456, _AIC + "b60.yaml", "Arc Pro B60"),
    # Xe2 XMX dense BF16 has half the operations/cycle of INT8 (367 TOPS on B70).
    "arc-pro-b70": (183.5, None, 608, "https://dgpu-docs.intel.com/overview/architecture/gpu-architecture.html",
                    "Arc Pro B70, BF16 derived from Xe2 XMX INT8 rate"),
    "vr200": (4000, 17500, 22000, "https://www.nvidia.com/en-au/data-center/hgx/", "Rubin, preliminary"),
    # GB10 SM12x: published sparse FP4 peak / 16 for BF16 with FP32 accumulation,
    # as in the Blackwell GB202 precision table. This is a cross-precision estimate.
    "dgx-spark-gb10": (62.5, 125, 273, "https://docs.nvidia.com/dgx/dgx-spark/hardware.html",
                       "GB10, SM12x BF16/FP8 ratios applied to sparse FP4 specification"),
    # GB300 Desktop has a 20-PFLOP sparse FP4 spec. Use the GB300 family's 8:1
    # sparse FP4 / dense BF16 ratio, and the *desktop* 7.1 TB/s HBM bandwidth.
    "dgx-station-gb300": (2500, 5000, 7100, "https://docs.nvidia.com/dgx/dgx-station-development-guide/overview.html",
                          "DGX Station GB300, Blackwell Ultra compute ratio and desktop HBM"),
}
_GPU_ALIASES = {
    "h100-sxm": "h100", "h100-80gb": "h100", "h100-sxm5-80gb": "h100", "h200-sxm": "h200",
    "h200-141gb": "h200", "b200-sxm": "b200", "b300-sxm": "b300", "a100-sxm4-80gb": "a100-80gb",
    "a100-pcie-80gb": "a100-80gb-pcie", "rtx-pro-6000-blackwell-server-edition": "rtx-pro-6000",
    "geforce-rtx-5090": "rtx-5090", "geforce-rtx-4090": "rtx-4090", "gb10": "dgx-spark-gb10",
}


def gpu_evidence(gpu_type):
    if not isinstance(gpu_type, str):
        return None
    key = re.sub(r"[ _]+", "-", gpu_type.strip().lower())
    key = key.removeprefix("nvidia-").removeprefix("amd-instinct-").removeprefix("intel-")
    row = _GPUS.get(_GPU_ALIASES.get(key, key))
    if row:
        bf16, fp8, bandwidth, url, variant = row
        return {"bf16_tflops": bf16, "fp8_tflops": fp8, "bandwidth_gbps": bandwidth,
                "source": "hardware_spec", "source_url": url, "variant": variant}
    return None


@lru_cache(maxsize=1)
def _model_records():
    return json.loads((Path(__file__).with_name("data") / "recipe_throughput_models.json").read_text())


def _published(metadata, model_path):
    if (metadata.get("source") not in ("pinned_reference", "hub") or metadata.get("hub_origin") == "mirror"
            or not isinstance(model_path, str) or not re.fullmatch(r"[0-9a-f]{40}", metadata.get("revision") or "")):
        return None
    item = _model_records()["models"].get(model_path.casefold())
    return item if item and metadata.get("revision") == item["revision"] else None


def throughput_metadata(metadata, model_path, flags, *, public_hub):
    """Resolve verified native-format headers for the rate model at the exact launch revision."""
    if metadata.get("config") or not public_hub:
        return metadata
    pinned = {"source": "pinned_reference", "revision": flags.get("revision")}
    item = _published(pinned, model_path)
    if not item:
        return metadata
    fmt = flags.get("config-format", "auto")
    if fmt in {"auto", "hf"} and item.get("public_architecture"):
        # An explicitly documented architecture supplies a forecast, even when
        # the checkpoint itself requires access. This is not a config-file digest.
        return {**pinned, **item["public_architecture"]}
    native = item.get("native_metadata") if fmt in {"auto", "mistral"} else None
    return {**pinned, **native} if native else metadata


_ISSUE_SOURCES = {
    "hub_access_required": "hub", "hub_revision_missing": "hub", "mount_unavailable": "mount",
    "offline_cache_missing": "offline_cache", "config_missing": "metadata_job",
    "native_config_unsupported": "metadata_job", "gguf_selection_required": "metadata_job",
    "gguf_layout_unsupported": "metadata_job", "metadata_job_unavailable": "metadata_job",
}


def _public_gguf_issue(model_path, revision):
    if not isinstance(model_path, str) or not revision:
        return None
    model = model_path.casefold()
    for repo, record in _model_records().get("public_gguf_files", {}).items():
        name, fmt = record["filename"], record["format"]
        choices = {repo, repo + ":" + record["quant"], repo + "/" + name.lower()}
        if revision != record["revision"] or model not in choices:
            continue
        # The Step file has an underscore before Q8_0; the plugin's Hub selector
        # matches the hyphenated *-Q8_0-* family only. A first-shard path is explicit.
        selected = model_path.endswith("/" + name) or model_path.endswith(":nvfp4") and fmt == "flint_nvfp4"
        return {"code": "gguf_layout_unsupported" if selected else "gguf_selection_required",
                "source": "public_audit", "format": fmt}
    return None


def throughput_metadata_diagnostic(metadata, model_path, flags, *, public_hub):
    """Identify live reader errors or a matching historical public probe, using codes only."""
    if metadata.get("config") or metadata.get("pending"):
        return None
    code = metadata.get("metadata_issue") or metadata.get("error_code")
    if public_hub and code in (None, "gguf_layout_unsupported", "config_missing", "metadata_job_unavailable"):
        gguf = _public_gguf_issue(model_path, flags.get("revision"))
        if gguf:
            return gguf
    if isinstance(code, str) and code in _ISSUE_SOURCES and code not in {"config_missing", "metadata_job_unavailable"}:
        out = {"code": code, "source": _ISSUE_SOURCES[code]}
        if code == "gguf_layout_unsupported" and metadata.get("metadata_format") in ("step35_q8_0", "flint_nvfp4"):
            out["format"] = metadata["metadata_format"]
        return out
    if public_hub and isinstance(model_path, str):
        audited = _model_records().get("public_metadata_audit", {}).get(model_path.casefold())
        requested = flags.get("revision") or "main"
        if audited and requested == audited["revision"] and code != "config_missing":
            return {"code": audited["code"], "source": "public_audit"}
    if code == "config_missing":
        return {"code": code, "source": _ISSUE_SOURCES[code]}
    if metadata.get("error") or code:
        return {"code": "metadata_job_unavailable", "source": "metadata_job"}
    return None


def workload_kind(metadata, model_path):
    item = _published(metadata, model_path)
    if item:
        return item.get("workload", "text")
    cfg = metadata.get("config") or {}
    text = text_config(cfg)
    models = [str(m).lower() for c in (cfg, text) for m in c.get("architectures") or []]
    if any(m.endswith(("forsequenceclassification", "fortokenclassification", "fortextembedding", "forembedding"))
           for m in models):
        return "pooling"
    kinds = {cfg.get("model_type"), text.get("model_type")}
    if kinds & {"whisper", "qwen3_tts", "qwen3_asr", "moss_tts_delay", "moss_tts_realtime", "fish_qwen3"}:
        return "audio"
    if kinds & {"diffusion_gemma", "diffusion_gemma_text"}:
        return "diffusion"
    if kinds & {"jina_embeddings_v5"}:
        return "pooling"
    if kinds & {"glm_ocr", "glm_ocr_text", "paddleocr_vl", "unlimited_ocr", "deepseek_vl_v2_ocr"}:
        return "vision_input"
    return "unknown"


def _positive(value):
    return type(value) in (int, float) and 0 < value <= 1e18 and math.isfinite(value)


def _decoder_params(cfg):
    """Text projection + gated MLP parameters used per generated token, excluding norm/bias."""
    kind = cfg.get("model_type")
    if kind not in {"llama", "qwen2", "qwen3", "qwen3_moe", "mistral"}:
        return None
    names = ("hidden_size", "num_attention_heads", "num_key_value_heads", "num_hidden_layers",
             "intermediate_size", "vocab_size")
    if not all(type(cfg.get(k)) is int and 0 < cfg[k] <= 2**32 for k in names):
        return None
    h, heads, kv, layers, inner, vocab = (cfg[k] for k in names)
    dim = cfg.get("head_dim", h // heads)
    if (not _positive(dim) or kv > heads or heads % kv or layers > 4096
            or type(cfg.get("tie_word_embeddings")) is not bool):
        return None
    # Wq, Wk, Wv, Wo and the full vocabulary output head.
    active = 2 * h * dim * (heads + kv) * layers + h * vocab
    total = active if cfg["tie_word_embeddings"] else active + h * vocab
    if kind == "qwen3_moe":
        experts, topk, moe_width, step = (cfg.get(k) for k in
                                         ("num_experts", "num_experts_per_tok", "moe_intermediate_size",
                                          "decoder_sparse_step"))
        if not all(type(i) is int and 0 < i <= 2**32 for i in (experts, topk, moe_width, step)) or topk > experts:
            return None
        dense_layers = cfg.get("mlp_only_layers", [])
        if not isinstance(dense_layers, list) or not all(type(i) is int and 0 <= i < layers for i in dense_layers):
            return None
        sparse = layers // step - sum((i + 1) % step == 0 for i in set(dense_layers))
        dense_proj = 3 * inner * h * (layers - sparse)
        active += dense_proj + sparse * (experts * h + topk * 3 * h * moe_width)
        total += dense_proj + sparse * (experts * h + experts * 3 * h * moe_width)
    else:
        active += layers * 3 * h * inner
        total += layers * 3 * h * inner
    return total, active


def model_evidence(metadata, model_path):
    """Published total and active parameters, or dense decoder weights from its own config."""
    item = _published(metadata, model_path)
    if workload_kind(metadata, model_path) not in {"text", "unknown"}:
        return None
    weight_bytes = metadata.get("weight_bytes")
    if item and item.get("active_params") and item.get("total_params"):
        url = item.get("source_url")
        if not url:
            url = _model_records()["recipe_source"] + item["recipe"] + ".yaml"
        estimated_weights = {}
        if not _positive(weight_bytes):
            # A complete immutable shard inventory bounds the tensor payload.
            # The lower end excludes safetensors headers and optional MTP files.
            if file_sizes := item.get("weight_bytes_range"):
                estimated_weights = {"weight_bytes_range": file_sizes, "weight_basis": "shard_files",
                                     "weight_source_url": item["weight_source_url"]}
            elif precision := item.get("weight_precision"):
                # A sensitivity scenario for rounded parameter counts, mixed
                # unquantized tensors and scales. Never claim a measured size.
                ranges = {"fp8": (0.9, 2.1), "int4": (0.45, 2.1)}
                if precision in ranges:
                    estimated_weights = {"weight_bytes_range": [int(item["total_params"] * n)
                                                                for n in ranges[precision]],
                                         "weight_basis": "precision_scenario", "weight_source_url": url}
        if metadata.get("architecture_source") == "public_model_spec":
            estimated_weights.update(architecture_source="public_model_spec", config_url=metadata.get("config_url"),
                                     weight_source_url=item["weight_source_url"])
        return {"total_params": item["total_params"], "active_params": item["active_params"],
                "active_prefill_params": item.get("active_prefill_params", item["active_params"]),
                "kind": item["kind"], "weight_bytes": weight_bytes, "source_url": url, "source": "model_spec",
                **estimated_weights}
    cfg = text_config(metadata.get("config") or {})
    params = _decoder_params(cfg)
    if params:
        total, active = params
        return {"total_params": total, "active_params": active, "active_prefill_params": active,
                "weight_bytes": weight_bytes, "kind": "moe" if cfg["model_type"] == "qwen3_moe" else "dense",
                "source_url": metadata.get("config_url"), "source": "decoder_geometry"}
    # Only known dense text decoders can use checkpoint bytes/element. Never
    # infer MoE activation from file size or a user-supplied display name.
    dense = cfg.get("model_type") in {"llama", "qwen2", "qwen3", "mistral", "phi3", "gemma", "gemma2"}
    bits = {"float16": 16, "bfloat16": 16, "float32": 32}.get(cfg.get("dtype") or cfg.get("torch_dtype"))
    if not dense or not bits or not _positive(weight_bytes) or metadata.get("weight_format"):
        return None
    count = weight_bytes * 8 / bits
    return {"total_params": count, "active_params": count, "active_prefill_params": count, "kind": "dense",
            "weight_bytes": weight_bytes, "source": "checkpoint_bytes", "source_url": metadata.get("config_url")}
