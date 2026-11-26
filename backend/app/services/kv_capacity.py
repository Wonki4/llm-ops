"""Block-aligned replica capacity at a specified workload; no GPU/engine dependency."""

import math
import re
from dataclasses import dataclass, replace

from app.services.kv_checkpoint_residency import DEEPSEEK_V4_TYPES, resident_weight_floor
from app.services.kv_draft_capacity import dspark_config, fit_active_draft, prepare_draft
from app.services.kv_layout import (
    UnknownLayoutError,
    components,
    dtype_bytes,
    positive,
    pp_ranks,
    resolve_dtype,
    text_config,
)

RULE_VERSION = "2026.11.7"
GIB = 1 << 30
_ALIASES = {
    "-tp": "tensor-parallel-size",
    "tp-size": "tensor-parallel-size",
    "tp": "tensor-parallel-size",
    "-pp": "pipeline-parallel-size",
    "pp-size": "pipeline-parallel-size",
    "pp": "pipeline-parallel-size",
    "-dcp": "decode-context-parallel-size",
    "context-length": "max-model-len",
    "max-running-requests": "max-num-seqs",
    "page-size": "block-size",
    "device-memory-utilization": "gpu-memory-utilization",
}


def final_flags(argv):
    result = {}
    i = 0
    while i < len(argv):
        key, sep, value = argv[i].partition("=")
        if key.startswith("--") or key in _ALIASES:
            key = key[2:] if key.startswith("--") else key
            key = _ALIASES.get(key.replace("_", "-"), key.replace("_", "-"))
            if not sep:
                if i + 1 < len(argv) and (not argv[i + 1].startswith("-") or argv[i + 1] == "-1"):
                    i += 1
                    value = argv[i]
                else:
                    value = "true"
            if key == "cpu-offload-params":
                names = [value] if value else []
                while i + 1 < len(argv) and not argv[i + 1].startswith("-"):
                    i += 1
                    names.append(argv[i])
                value = " ".join(names)
            result[key] = value
        i += 1
    return result


def memory_size(value):
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([kKmMgGtT]?)(i?[bB])?", str(value))
    if not match:
        raise UnknownLayoutError("kv-cache-memory-bytes")
    unit = match[2]
    base = 1024 if unit.isupper() or (match[3] or "").startswith("i") else 1000
    return int(float(match[1]) * base ** ("kmgt".index(unit.lower()) + 1 if unit else 0))


@dataclass(frozen=True)
class Scenario:
    context: int = 32768
    requests: int = 1
    prefix: int = 0


def configured_budget(out, size, vram_gb):
    out.update(budget_source="explicit", budget_bytes_per_gpu=[size, size])
    floor = out.get("resident_weight_floor_bytes_per_gpu")
    uva_estimate = out.get("weight_offload", {}).get("max_bytes_per_gpu") and not out.get("weight_residency")
    if not vram_gb or floor is None or uva_estimate or out.get("weight_load_multiplier"):
        out["weight_fit_unverified"] = True
    draft = out.get("speculation") or {}
    workspace = draft.get("verification_workspace_bytes_per_gpu") or 0
    # The separate drafter's known tensors also occupy the GPU. A configured
    # CPU weight offloader may share its quota with the drafter.
    draft_weights = 0 if out.get("weight_offload") else draft.get("draft_weight_bytes_per_gpu") or 0
    if vram_gb and size + (floor or 0) + draft_weights + workspace > vram_gb * 10**9:
        out["budget_exceeds_vram"] = True


def budget_range(flags, engine, vram_gb, metadata, observed, notes, floor):
    if observed is not None:
        return [observed, observed], "startup"
    if not vram_gb or floor is None:
        return None, "unknown"
    fraction = flags.get("gpu-memory-utilization" if engine == "vllm" else "mem-fraction-static")
    if fraction is None:
        version = metadata.get("engine_versions", {}).get(engine, "")
        if engine == "vllm" and re.match(r"0\.(?:[5-9]|10|11)\.", version):
            fraction = 0.9
        else:
            fraction = 1
            notes.append("Memory utilization is engine-selected. Using full VRAM for the upper bound.")
    fraction = float(fraction)
    if not 0 < fraction <= 1:
        raise UnknownLayoutError("GPU memory utilization")
    upper = max(0, int(vram_gb * 10**9 * fraction) - floor)
    notes.append(
        f"VRAM {vram_gb} GB × {fraction:g} minus a minimum resident weight payload of {floor:,} bytes per GPU. "
        "Runtime allocations and uneven sharding may reduce the available space."
    )
    return [0, upper], "profile_upper_bound"


def roundup(n, divisor):
    return (n + divisor - 1) // divisor * divisor


def estimate(
    metadata, engine, argv, *, vram_gb=None, gpu_type=None, observed_budget=None, scenario=Scenario(), env=None,
):
    result = {
        "rule_version": RULE_VERSION,
        "capacity_tokens": None,
        "complete_requests": None,
        "missing": [],
        "assumptions": [],
        "components": [],
    }
    try:
        _estimate(result, metadata, engine, argv, vram_gb, observed_budget, scenario, env or {}, gpu_type)
    except (UnknownLayoutError, ValueError, KeyError, TypeError, ArithmeticError, AttributeError) as exc:
        result["missing"].append(str(exc)[:140])
    if result.get("budget_exceeds_vram"):
        result["missing"].append(
            f"Configured KV cache plus GPU-resident weights exceeds GPU profile VRAM ({vram_gb} GB per GPU)."
        )
    return result


def _estimate(out, metadata, engine, argv, vram, observed, scenario, env, gpu_type):
    notes = out["assumptions"]
    flags = final_flags(argv)
    tp = positive(flags.get("tensor-parallel-size", 1), "TP")
    pp = positive(flags.get("pipeline-parallel-size", 1), "PP")
    dcp = positive(flags.get("decode-context-parallel-size", 1), "DCP")
    if tp % dcp:
        raise UnknownLayoutError("DCP must divide TP")
    # Explicit launch evidence remains useful when the selected storage is offline
    # or a new architecture has no layout rule yet.
    out.update(
        tp=tp,
        pp=pp,
        dcp=dcp,
        kv_dtype=flags.get("kv-cache-dtype", "auto"),
        context_tokens=scenario.context,
        requests=scenario.requests,
    )
    if engine in {"vllm", "sglang"} and "cpu-offload-gb" in flags:
        cpu_limit = float(flags["cpu-offload-gb"])
        if not math.isfinite(cpu_limit) or cpu_limit < 0:
            raise UnknownLayoutError("cpu-offload-gb")
        if cpu_limit:
            out["weight_offload"] = {
                "requested_bytes_per_gpu": int(cpu_limit * GIB), "min_bytes_per_gpu": None, "max_bytes_per_gpu": None,
            }
    explicit = 0
    if engine == "vllm" and "kv-cache-memory-bytes" in flags:
        explicit = memory_size(flags["kv-cache-memory-bytes"])
    floor = None
    if vram and metadata.get("weight_bytes") and (observed is None or explicit or "num-gpu-blocks-override" in flags):
        try:
            floor = resident_weight_floor(metadata, flags, engine, tp, pp, out)
        except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
            notes.append(f"Cannot establish GPU-resident weight bytes: {exc}")
        if floor is not None:
            out["resident_weight_floor_bytes_per_gpu"] = floor
            if floor > vram * 10**9:
                out["weights_exceed_vram"] = True
    if explicit:
        configured_budget(out, explicit, vram)
    # An unsupported cache layout can still have a known per-device memory budget.
    if "budget_bytes_per_gpu" not in out:
        try:
            budget, source = budget_range(flags, engine, vram, metadata, observed, notes, floor)
        except (ValueError, TypeError, UnknownLayoutError) as exc:
            notes.append(f"Cannot determine GPU memory budget: {exc}")
            budget, source = None, "unknown"
        out.update(budget_source=source, budget_bytes_per_gpu=budget)
    if not metadata.get("config"):
        out["missing"].append(metadata.get("error") or "reading config.json")
        return
    config = metadata["config"]
    cfg = text_config(config)
    if any(int(flags[k]) != 1 for k in ("data-parallel-size", "dp-size", "dp") if k in flags):
        out["missing"].append("engine-local data-parallel rank placement")
    if flags.get("hf-overrides"):
        out["missing"].append("effective architecture after --hf-overrides")
    if flags.get("hf-config-path"):
        out["missing"].append("effective architecture from --hf-config-path")
    if cfg.get("model_type") in DEEPSEEK_V4_TYPES:
        from app.services.kv_deepseek_v4 import deepseek_v4_estimate

        if engine != "vllm":
            out["missing"].append("DeepSeek-V4 cache allocation for " + engine)
        if not out["missing"]:
            deepseek_v4_estimate(out, cfg, flags, tp=tp, pp=pp, dcp=dcp, scenario=scenario, env=env,
                                 gpu_type=gpu_type, metadata=metadata)
        return
    kinds, layouts, missing, kv = components(config, flags, engine, tp, notes, gpu_type=gpu_type)
    out["missing"].extend(missing)
    max_len = flags.get("max-model-len")
    max_len = cfg.get("max_position_embeddings") if max_len in (None, "auto", "-1") else max_len
    if max_len:
        max_len = memory_size(max_len)
    max_seqs = positive(flags["max-num-seqs"], "max-num-seqs") if "max-num-seqs" in flags else None
    if not max_seqs:
        notes.append("Scheduler concurrency is engine-selected; request counts are KV-limited.")
    packed = "kda" in kinds and "dsa" in kinds and not missing
    draft = None
    spec = dspark_config(flags)
    if spec is not None and packed and engine == "vllm":
        draft = prepare_draft(out, metadata, spec, flags, tp, pp, dcp, scenario, max_seqs)
        if explicit:
            configured_budget(out, explicit, vram)
        # KimiK3DeltaAttention.get_state_shape extends each convolution history
        # by K rows. The recurrent state remains separate from this history.
        linear = cfg["linear_attn_config"]
        local_dim = linear["num_heads"] // tp * linear["head_dim"]
        model_dtype = resolve_dtype(flags.get("dtype"), cfg.get("torch_dtype") or cfg.get("dtype"))
        conv_dtype = resolve_dtype(flags.get("mamba-cache-dtype"), model_dtype)
        extra_state = 3 * local_dim * dtype_bytes(conv_dtype) * out["speculation"]["draft_tokens"]
        layouts["kda"] = [replace(c, width=c.width + extra_state) for c in layouts["kda"]]
        out["assumptions"].append(
            "KDA convolution state includes K verification rows. Extra rollback or "
            "kernel workspaces depend on the engine build."
        )
    elif (
        any(k == "speculative-config" or k.startswith("speculative-config.") for k in flags)
        or flags.get("num-speculative-tokens")
        or flags.get("speculative-algorithm")
    ):
        out["missing"].append("speculative draft cache/reserved slots")
    ranks = pp_ranks(kinds, layouts, pp, env.get("VLLM_PP_LAYER_PARTITION"))
    if (
        dcp > 1
        and not cfg.get("kv_lora_rank")
        and tp < dcp * cfg.get("num_key_value_heads", cfg.get("num_attention_heads", 1))
    ):
        out["missing"].append("DCP KV head replication layout")
    payload_floor = any(c.payload_floor for r in ranks for c in r)
    if draft or payload_floor:
        out["payload_lower_bound"] = True
        if "num-gpu-blocks-override" in flags:
            out["missing"].append("physical sparse MLA block size for --num-gpu-blocks-override")
    block = positive(flags.get("block-size", 1 if engine == "sglang" else 16), "block-size")
    if cfg.get("model_type") == "glm5_next_text" and engine == "vllm":
        if "block-size" in flags and block % 128:
            raise UnknownLayoutError("GLM pooled index cache requires a multiple of 128 tokens per page")
        block = max(128, block)
    if packed:
        for rank in ranks:
            states = [(c.width, count) for c, count in rank.items() if c.state]
            layers = sum(n for c, n in rank.items() if c.kind.startswith("MLA"))
            per_token = sum(c.width * n for c, n in rank.items() if not c.state)
            if not layers or not per_token:
                raise UnknownLayoutError("Kimi packed PP partition")
            for width, count in states:
                groups = math.ceil(count / layers)
                min_page = math.ceil(math.ceil(count / groups) * width / per_token)
                block = max(block, 64, 1 << (min_page - 1).bit_length())
        notes.append("Kimi vLLM packed DSA rows, one recurrent row per KDA group/request.")
    elif "block-size" not in flags:
        notes.append(f"Assumed engine page size {block}; startup allocator may choose another size.")
    logical_block = block * dcp
    # All layers in a vLLM hybrid group use equal-sized physical pages. The generic
    # rule supplies a bound when the exact allocator grouping cannot be recovered.
    hybrid_unpacked = (
        not packed and not payload_floor and (len(set(kinds)) > 1 or any(c.state for r in ranks for c in r))
    )
    if hybrid_unpacked:
        notes.append(
            "Hybrid cache grouping and padding depend on the engine allocator. Layout totals are payload "
            "estimates and capacity is an upper bound until startup."
        )
    if flags.get("disable-hybrid-kv-cache-manager", "").lower() in ("true", "1"):
        notes.append("Hybrid allocator disabled: sliding layers retain full-length pages.")
    requested = scenario.context
    prefix = scenario.prefix // logical_block * logical_block

    def cost(rank, length, requests, shared=0):
        draft_bytes = 0
        if draft is not None:
            draft_bytes = draft.kv_bytes(length) * requests
            if shared:
                # At most one copy per full shared page is necessary. Discount
                # only the prefix still inside the drafter's retained window.
                common = max(0, min(shared, length) - max(0, length - draft.window + 1))
                draft_bytes -= common // 16 * 16 * draft.row_bytes * (requests - 1)
            length += draft.lookahead
        attention = 0
        recurrent = 0
        for comp, count in rank.items():
            if comp.state:
                recurrent += comp.width * count * requests
                continue
            local_length = (
                min(length, comp.window + logical_block - 1)
                if comp.window and not flags.get("disable-hybrid-kv-cache-manager")
                else length
            )
            pages = roundup(local_length, logical_block) * requests
            if shared and not comp.window:
                pages -= min(local_length // logical_block * logical_block, shared) * max(0, requests - 1)
            attention += comp.width * count * (pages // dcp)
        if packed:
            layers = sum(n for c, n in rank.items() if c.kind.startswith("MLA"))
            state_count = sum(n for c, n in rank.items() if c.state)
            row = sum(c.width * n * block for c, n in rank.items() if not c.state)
            recurrent = math.ceil(state_count / layers) * row * requests
        return attention + recurrent + draft_bytes

    for index, rank in enumerate(ranks):
        for comp, count in rank.items():
            out["components"].append(
                {
                    "pipeline_rank": index,
                    "kind": comp.kind,
                    "layers": count,
                    "unit": "request" if comp.state else "token",
                    "bytes_per_gpu": comp.width * count if comp.state else comp.width * count / dcp,
                    "window": comp.window or None,
                }
            )
    out.update(
        tp=tp,
        pp=pp,
        dcp=dcp,
        kv_dtype=kv,
        block_tokens=logical_block,
        max_model_len=max_len,
        max_num_seqs=max_seqs,
        context_tokens=requested,
        requests=draft.active if draft else scenario.requests,
        attention_bytes_per_token_per_gpu=max(
            sum(c.width * n for c, n in r.items() if not c.state) / dcp for r in ranks
        ),
    )
    # Unknown groups must not turn the sum of known groups into a purported total.
    if out["missing"]:
        return
    out["required_bytes_per_gpu"] = max(cost(r, requested, out["requests"]) for r in ranks)
    out["required_bytes_per_replica"] = tp * sum(cost(r, requested, out["requests"]) for r in ranks)
    if prefix:
        out["shared_required_bytes_per_gpu"] = max(cost(r, requested, out["requests"], prefix) for r in ranks)
    if out.get("budget_exceeds_vram"):
        return
    blocks = flags.get("num-gpu-blocks-override") if engine == "vllm" else None
    if blocks and all(not c.state and not c.window for r in ranks for c in r):
        size = positive(blocks, "num-gpu-blocks-override") * max(cost(r, logical_block, 1) for r in ranks)
        configured_budget(out, size, vram)
        if out.get("budget_exceeds_vram"):
            return
    budget = out.get("budget_bytes_per_gpu")
    source = out["budget_source"]
    if budget is None:
        out["missing"].append("KV memory budget")
        return
    if not all(cost(r, requested, 1) for r in ranks):
        raise UnknownLayoutError("empty cache layout")

    def fit(limit):
        if draft is not None:
            # Only a single pipeline stage is supported by the verified draft layout.
            return fit_active_draft(
                limit,
                lambda n: cost(ranks[0], n, 1),
                draft.active,
                requested,
                max_seqs,
                max_len,
                draft.lookahead,
                logical_block,
            )
        whole = min(limit // cost(r, requested, 1) for r in ranks)
        complete = min(whole, max_seqs) if max_seqs else whole
        if max_len and requested > max_len:
            complete = 0
        if not hybrid_unpacked and not packed and all(not c.window and not c.state for r in ranks for c in r):
            blocks = min(limit // cost(r, logical_block, 1) for r in ranks)
            return blocks * logical_block, complete
        # Report raw cache tokens independent of scheduler limits. Charge a state per
        # target-length request and determine how much of a final request also fits.
        residual = [limit - whole * cost(r, requested, 1) for r in ranks]
        low, high = 0, (requested - 1) // logical_block
        while low < high:
            mid = (low + high + 1) // 2
            if all(cost(r, mid * logical_block, 1) <= rem for r, rem in zip(ranks, residual)):
                low = mid
            else:
                high = mid - 1
        return whole * requested + low * logical_block, complete

    low, high = (fit(n) for n in budget)
    if draft and max_len:
        out["speculation"]["context_limited_capacity_tokens"] = min(
            high[0], draft.active * max(0, max_len - draft.lookahead)
        )
    if engine == "sglang" and flags.get("max-total-tokens"):
        cap = positive(flags["max-total-tokens"], "max-total-tokens") // logical_block * logical_block

        def capped(pair):
            return min(cap, pair[0]), min(cap // roundup(requested, logical_block), pair[1])

        low, high = capped(low), capped(high)
    if draft or hybrid_unpacked or payload_floor:
        low = (0, 0)
    out.update(capacity_range=[low[0], high[0]], complete_requests_range=[low[1], high[1]])
    if source != "profile_upper_bound" and not draft and not hybrid_unpacked and not payload_floor:
        out.update(capacity_tokens=high[0], complete_requests=high[1])
