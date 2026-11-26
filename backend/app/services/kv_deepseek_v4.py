"""DeepSeek-V4 compressed attention, SWA and compressor payload per vLLM TP rank.

Engine geometry is pinned to vllm-project/vllm@65ada0c40e18d1100deee9c3deb351326a309e82,
vllm/models/{deepseek_v4,deepseek_v41}/{attention,compressor}.py and
vllm/v1/attention/backends/mla/{indexer,sparse_swa}.py. Hybrid group packing
and scratch allocations are resolved at startup, so payload yields a ceiling.
"""

import json
import math
from collections import Counter
from dataclasses import dataclass

from app.services.kv_draft_capacity import dspark_config, fit_active_draft
from app.services.kv_layout import UnknownLayoutError, positive


def _aligned(n, size):
    return (n + size - 1) // size * size


@dataclass(frozen=True)
class _Rows:
    kind: str
    layers: int
    width: int
    ratio: int = 1
    window: int = 0
    page: int = 1
    fixed: bool = False

    def payload(self, length, width, count, prefix, phase_known):
        if self.fixed:
            return self.width * self.layers * count
        # At a decode step the last W-1 committed positions are still visible.
        # The scheduler reserves extra logical positions for the candidate block.
        live = min(length, self.window - 1) if self.window else length
        # In a concrete scenario the page containing the oldest necessary
        # token stays allocated even when only one of its slots is still live.
        first_page = (length - live) // self.page * self.page
        if phase_known:
            positions = _aligned(length + width, self.page) - first_page
        else:
            # Capacity search spans unknown end positions. The minimum page
            # footprint is monotone even as the oldest page is released.
            positions = _aligned(live + width, self.page)
        positions *= count
        if prefix and count > 1:
            common = max(0, prefix // self.page * self.page - first_page)
            positions -= common * (count - 1)
        return positions // self.ratio * self.width * self.layers

    def describe(self):
        return {
            "pipeline_rank": 0,
            "kind": self.kind,
            "layers": self.layers,
            "bytes_per_gpu": self.width * self.layers / self.ratio,
            "unit": "request" if self.fixed else "token",
            "window": self.window or None,
            **({"compression_ratio": self.ratio} if self.ratio != 1 else {}),
        }


def _formats(v41, flags, env, gpu, notes):
    config = json.loads(flags.get("attention-config", "{}"))
    if not isinstance(config, dict):
        raise UnknownLayoutError("DeepSeek attention-config must be an object")
    backend = (
        flags.get("attention-config.backend")
        or flags.get("attention-backend")
        or config.get("backend")
        or env.get("VLLM_ATTENTION_BACKEND")
        or "AUTO"
    ).upper()
    kv = flags.get("kv-cache-dtype", "auto").lower()
    gpu = (gpu or "").upper()
    sm100 = any(name in gpu for name in ("B200", "B300", "GB200", "GB300"))
    sm120 = any(name in gpu for name in ("RTX-PRO-6000", "RTX PRO 6000", "GB10"))
    ampere = gpu in {"A100", "A800"}
    amd = gpu.startswith("MI") or backend.startswith("ROCM")
    supported_backends = {
        "AUTO",
        "FLASHMLA_SPARSE",
        "FLASHMLA_SPARSE_DSV4",
        "FLASHMLA_SPARSE_DSV41",
        "FLASHINFER_MLA_SPARSE_DSV4",
        "FLASHINFER_MLA_SPARSE_DSV41",
        "FLASHMLA_MEGA_ATTN_DSV41",
        "ROCM_AITER_MLA_SPARSE",
    }
    if backend not in supported_backends or not v41 and backend.endswith("DSV41"):
        raise UnknownLayoutError("DeepSeek compressed-cache attention backend " + backend)
    idx = (flags.get("attention-config.indexer-kv-dtype") or config.get("indexer_kv_dtype") or "auto").lower()
    if idx == "auto":
        # vLLM sets MXFP4 automatically on ROCm CDNA4 with aiter; when an
        # automatically chosen kernel is ambiguous, use the smaller row.
        idx = "mxfp4" if gpu.startswith(("MI35", "MI45")) or not gpu or (v41 and ampere) else "fp8"
        notes.append(f"DeepSeek auto index format uses {idx} in the payload estimate.")
    if idx not in ("fp8", "mxfp4"):
        raise UnknownLayoutError("DeepSeek index key format " + idx)
    if kv not in ("auto", "fp8", "fp8_e4m3", "fp8_ds_mla", "nvfp4_ds_mla", "bfloat16"):
        raise UnknownLayoutError("DeepSeek latent cache format " + kv)
    plain = (backend.startswith("FLASHINFER_MLA") and not sm120) or (amd and kv == "fp8_e4m3")
    if plain:
        if kv.endswith("_ds_mla"):
            raise UnknownLayoutError("packed DeepSeek cache requested on a plain-row backend")
        swa = compressed = 512 if kv.startswith("fp8") else 1024
    else:
        if (kv == "bfloat16" or (v41 and kv == "fp8_e4m3")) and gpu:
            raise UnknownLayoutError("DeepSeek packed attention backend does not support " + kv)
        swa = 528 if v41 and (sm100 or ampere) else 584
        compressed = swa
        if not gpu:
            swa = compressed = 512
        if v41 and (sm100 or ampere or not gpu):
            if kv == "nvfp4_ds_mla" or (kv in ("auto", "fp8") and backend in ("AUTO", "FLASHMLA_MEGA_ATTN_DSV41")):
                compressed = 288
                if backend == "AUTO":
                    notes.append("Engine-selected V4.1 backend: using the NVFP4 compressed-row payload floor.")
        elif kv == "nvfp4_ds_mla":
            raise UnknownLayoutError("DeepSeek NVFP4 compressed cache requires a V4.1 compatible GPU")
    notes.append(
        f"{backend}: sliding row {swa} B, compressed latent row {compressed} B, "
        f"{idx} compressed index row {68 if idx == 'mxfp4' else 132} B per owner."
    )
    return swa, compressed, 68 if idx == "mxfp4" else 132


def _native_draft(cfg, ratios, layers, flags, out, scenario, max_seqs):
    config = dspark_config(flags)
    active = min(scenario.requests, max_seqs or scenario.requests)
    if config is None:
        if any(
            k.startswith("speculative-config") or k in ("num-speculative-tokens", "speculative-algorithm")
            for k in flags
        ):
            raise UnknownLayoutError("DeepSeek speculative cache method")
        return 0, 0, 0, scenario.requests
    if (config.get("model") not in (None, flags.get("model") or flags.get("model-path"))
            or config.get("revision") not in (None, flags.get("revision"))):
        raise UnknownLayoutError("DeepSeek external draft checkpoint layout")
    known = {
        "model",
        "revision",
        "method",
        "num_speculative_tokens",
        "num_speculative_tokens_per_batch_size",
        "draft_sample_method",
        "rejection_sample_method",
        "enable_adaptive_verification",
        "dspark_draft_topk",
    }
    if set(config) - known:
        raise UnknownLayoutError("DeepSeek DSpark options: " + ", ".join(sorted(set(config) - known)))
    targets = cfg.get("dspark_target_layer_ids")
    if (
        not isinstance(targets, list)
        or not targets
        or any(type(i) is not int or not 0 <= i < layers for i in targets)
        or not cfg.get("dspark_block_size")
    ):
        raise UnknownLayoutError("DeepSeek integrated DSpark checkpoint metadata")
    # V4's integrated draft uses n_mtp_layers or three; V4.1 also honors
    # num_nextn_predict_layers. The raw ratio map includes each draft layer.
    draft_layers = (
        cfg.get("n_mtp_layers")
        or (cfg.get("num_nextn_predict_layers") if cfg["model_type"] == "deepseek_v41_text" else None)
        or 3
    )
    if ratios[layers:] != [0] * draft_layers:
        raise UnknownLayoutError("DeepSeek integrated DSpark layer geometry")
    maximum = positive(config.get("num_speculative_tokens", cfg["dspark_block_size"]), "DeepSeek DSpark width")
    schedule = config.get("num_speculative_tokens_per_batch_size")
    batches = [(1, active, maximum)] if schedule is None else schedule
    if not isinstance(batches, list):
        raise UnknownLayoutError("DeepSeek DSpark batch schedule")
    batched = (
        positive(flags["max-num-batched-tokens"], "max-num-batched-tokens")
        if "max-num-batched-tokens" in flags
        else None
    )
    fitting = []
    previous_end = 0
    for entry in batches:
        if (
            not isinstance(entry, (tuple, list))
            or len(entry) != 3
            or any(type(x) is not int for x in entry)
            or not previous_end < entry[0] <= entry[1]
            or not 0 <= entry[2] <= maximum
        ):
            raise UnknownLayoutError("DeepSeek DSpark batch schedule")
        start, previous_end, width = entry
        candidate = min(previous_end, active)
        if batched is not None:
            candidate = min(candidate, batched // (width + 1))
        if candidate >= start:
            fitting.append((candidate, width))
    if not fitting:
        raise UnknownLayoutError("DeepSeek batch budget cannot schedule a full verification step")
    active, width = max(fitting)
    out["integrated_speculation"] = {
        "method": "dspark",
        "draft_tokens": width,
        "lookahead_tokens_per_request": width,
        "active_requests": active,
        "candidate_tokens": width * active,
        "max_num_seqs": max_seqs,
    }
    out["assumptions"].append(
        f"Integrated DSpark uses {draft_layers} additional sliding-window layers. "
        "The scenario reserves a full candidate block per active request; adaptive verification "
        "may verify fewer positions. Compressor rings use the configured maximum width."
    )
    return draft_layers, width, maximum, active


def deepseek_v4_estimate(out, cfg, flags, *, tp, pp, dcp, scenario, env, gpu_type=None, metadata=None):
    from app.services.kv_capacity import memory_size

    if pp != 1 or dcp != 1 or int(flags.get("prefill-context-parallel-size", 1)) != 1:
        raise UnknownLayoutError("DeepSeek compressed-cache PP/context-parallel placement")
    if (
        flags.get("num-gpu-blocks-override")
        or flags.get("kv-cache-dtype-skip-layers")
        or flags.get("disable-hybrid-kv-cache-manager", "").lower() in ("1", "true")
    ):
        raise UnknownLayoutError("DeepSeek cache pool or per-layer dtype override")
    v41 = cfg["model_type"] == "deepseek_v41_text"
    layers = positive(cfg.get("num_hidden_layers"), "DeepSeek layer count")
    ratios = cfg.get("compress_ratios")
    window = positive(cfg.get("sliding_window"), "DeepSeek sliding window")
    if (
        not isinstance(ratios, list)
        or len(ratios) < layers
        or any(type(r) is not int for r in ratios)
        or (cfg.get("head_dim"), cfg.get("qk_rope_head_dim"), cfg.get("index_head_dim")) != (512, 64, 128)
    ):
        raise UnknownLayoutError("DeepSeek compression map or row dimensions")
    allowed = {0, 1, 2} if v41 else {0, 4, 128}
    if not set(ratios[:layers]) <= allowed:
        raise UnknownLayoutError("DeepSeek layer compression ratios")
    if cfg.get("num_attention_heads") and cfg["num_attention_heads"] % tp:
        raise UnknownLayoutError("DeepSeek TP must divide query heads")
    context = positive(scenario.context, "context length")
    max_len = flags.get("max-model-len")
    if max_len in (None, "auto", "-1"):
        max_len = cfg.get("max_position_embeddings")
    max_len = memory_size(max_len) if max_len else None
    max_seqs = positive(flags["max-num-seqs"], "max-num-seqs") if "max-num-seqs" in flags else None
    if "max-num-active-seqs" in flags:
        max_active = positive(flags["max-num-active-seqs"], "max-num-active-seqs")
        max_seqs = min(max_seqs or max_active, max_active)
    draft, width, maximum, active = _native_draft(cfg, ratios, layers, flags, out, scenario, max_seqs)
    swa, compressed, index = _formats(v41, flags, env, gpu_type, out["assumptions"])
    if v41:
        owners = cfg.get("kv_source_layer_ids")
        query = cfg.get("index_source_layer_ids")
        if (
            not isinstance(owners, list)
            or not owners
            or owners != sorted(set(owners))
            or not isinstance(query, list)
            or not set(owners) <= set(query)
            or any(type(i) is not int or not 0 <= i < layers or not ratios[i] for i in owners + query)
        ):
            raise UnknownLayoutError("DeepSeek V4.1 KV/index source ownership map")
        compressed_counts = Counter(ratios[i] for i in owners)
        out["assumptions"].append(
            f"{len(owners)} V4.1 KV/index storage owners shared by {len(query)} index query "
            "sources; candidate/top-k query scratch is outside the persistent KV payload."
        )
    else:
        compressed_counts = Counter(r for r in ratios[:layers] if r)
    if not compressed_counts:
        raise UnknownLayoutError("DeepSeek persistent compressed cache layers")
    # The minimum legal logical page stores an integral number of compressed
    # rows. Missing CLI page size can understate padding from the chosen kernel.
    minimum_block = math.lcm(*compressed_counts)
    block = positive(flags.get("block-size", minimum_block), "DeepSeek block size")
    if block % minimum_block:
        raise UnknownLayoutError("DeepSeek block size must be a multiple of its compression ratios")
    if "block-size" not in flags:
        out["assumptions"].append(f"Engine-selected block size: using minimum compression alignment {block} tokens.")
    window_page = 32 if v41 else 64
    groups = [_Rows("Sliding-window MLA", layers, swa, window=window, page=window_page)]
    if draft:
        groups.append(_Rows("DSpark sliding-window MLA", draft, swa, window=window, page=window_page))
    for ratio, count in sorted(compressed_counts.items()):
        groups.append(_Rows(f"Compressed MLA (1/{ratio})", count, compressed, ratio, page=block))
        if v41 or ratio == 4:
            groups.append(_Rows(f"Compressed index (1/{ratio})", count, index, ratio, page=block))
        if v41 and ratio > 1:
            ring = max(8, 1 << (maximum + 1).bit_length())
            groups.append(_Rows("FP32 compressor ring", count, 2 * 512 * 4 * ring, fixed=True))
        if not v41:
            # Overlap doubles both the latent and index compressor dimensions in CSA.
            coeff = 2 if ratio == 4 else 1
            dim = 512 + (128 if ratio == 4 else 0)
            groups.append(
                _Rows(
                    f"FP32 compressor state (1/{ratio})",
                    count,
                    2 * coeff * dim * 4,
                    window=coeff * ratio,
                    page=4 if ratio == 4 else 8,
                )
            )

    def cost(length, requests=1, prefix=0, *, phase_known=True):
        return sum(g.payload(length, width, requests, prefix, phase_known) for g in groups)

    required = cost(context, active)
    out.update(
        components=[g.describe() for g in groups],
        required_bytes_per_gpu=required,
        required_bytes_per_replica=tp * required,
        requests=active,
        block_tokens=block,
        max_model_len=max_len,
        max_num_seqs=max_seqs,
        payload_lower_bound=True,
        attention_bytes_per_token_per_gpu=sum(
            g.width * g.layers / g.ratio for g in groups if not g.fixed and not g.window
        ),
    )
    out["assumptions"].append(
        "One latent and index owner copy per TP rank. Payload excludes allocator group "
        "padding, partially retained pages, runtime scratch and inactive requests. "
        "Capacity is a ceiling at the selected workload, pending engine startup."
    )
    if scenario.prefix and flags.get("no-enable-prefix-caching", "").lower() not in ("true", "1"):
        out["shared_required_bytes_per_gpu"] = cost(context, active, min(scenario.prefix, context))
    if out.get("budget_exceeds_vram"):
        return
    budget = out.get("budget_bytes_per_gpu")
    if budget is None:
        out["missing"].append("KV memory budget")
        return
    limit = budget[1]
    whole, residual = divmod(limit, cost(context))
    complete = min(whole, max_seqs or whole) if not max_len or context + width <= max_len else 0
    if draft:
        tokens, _ = fit_active_draft(
            limit, lambda n: cost(n, phase_known=False), active, context, max_seqs, max_len, width, block
        )
    else:
        lo, hi = 0, context - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if cost(mid, phase_known=False) <= residual:
                lo = mid
            else:
                hi = mid - 1
        tokens = whole * context + lo
    out.update(capacity_range=[0, tokens], complete_requests_range=[0, complete])
