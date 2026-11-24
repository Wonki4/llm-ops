"""Block-aligned replica capacity at a specified workload; no GPU/engine dependency."""

import math
import re
from dataclasses import dataclass

from app.services.kv_layout import UnknownLayoutError, components, positive, pp_ranks, text_config

RULE_VERSION = "2026.11.1"
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


def budget_range(flags, engine, vram_gb, metadata, workers, observed, notes):
    if engine == "vllm" and "kv-cache-memory-bytes" in flags:
        size = memory_size(flags["kv-cache-memory-bytes"])
        return [size, size], "explicit"
    if observed is not None:
        return [observed, observed], "startup"
    weights = metadata.get("weight_bytes")
    if vram_gb and weights:
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
        if {"cpu-offload-gb", "offload-group-size", "quantization", "load-format"} & flags.keys():
            notes.append("Weight residency changes at load time; disk size cannot establish resident bytes.")
            return [0, int(vram_gb * 10**9 * fraction)], "profile_upper_bound"
        upper = max(0, int(vram_gb * 10**9 * fraction) - math.ceil(weights / workers))
        notes.append(
            f"VRAM {vram_gb} GB × {fraction:g}; checkpoint bytes / {workers} assuming even sharding. "
            "Runtime allocations and replicated weights are unmeasured; upper bound only."
        )
        return [0, upper], "profile_upper_bound"
    return None, "unknown"


def roundup(n, divisor):
    return (n + divisor - 1) // divisor * divisor


def estimate(metadata, engine, argv, *, vram_gb=None, observed_budget=None, scenario=Scenario(), env=None):
    result = {
        "rule_version": RULE_VERSION,
        "capacity_tokens": None,
        "complete_requests": None,
        "missing": [],
        "assumptions": [],
        "components": [],
    }
    try:
        _estimate(result, metadata, engine, argv, vram_gb, observed_budget, scenario, env or {})
    except (UnknownLayoutError, ValueError, KeyError, TypeError, ArithmeticError, AttributeError) as exc:
        result["missing"].append(str(exc)[:140])
    return result


def _estimate(out, metadata, engine, argv, vram, observed, scenario, env):
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
    if engine == "vllm" and "kv-cache-memory-bytes" in flags:
        size = memory_size(flags["kv-cache-memory-bytes"])
        out.update(budget_source="explicit", budget_bytes_per_gpu=[size, size])
    if not metadata.get("config"):
        out["missing"].append(metadata.get("error") or "reading config.json")
        return
    config = metadata["config"]
    cfg = text_config(config)
    kinds, layouts, missing, kv = components(config, flags, engine, tp, notes)
    out["missing"].extend(missing)
    ranks = pp_ranks(kinds, layouts, pp, env.get("VLLM_PP_LAYER_PARTITION"))
    max_len = flags.get("max-model-len")
    max_len = cfg.get("max_position_embeddings") if max_len in (None, "auto", "-1") else max_len
    if max_len:
        max_len = memory_size(max_len)
    max_seqs = positive(flags["max-num-seqs"], "max-num-seqs") if "max-num-seqs" in flags else None
    if not max_seqs:
        notes.append("Scheduler concurrency is engine-selected; request counts are KV-limited.")
    if any(int(flags[k]) != 1 for k in ("data-parallel-size", "dp-size", "dp") if k in flags):
        out["missing"].append("engine-local data-parallel rank placement")
    if flags.get("speculative-config") or flags.get("num-speculative-tokens") or flags.get("speculative-algorithm"):
        out["missing"].append("speculative draft cache/reserved slots")
    if flags.get("hf-overrides"):
        out["missing"].append("effective architecture after --hf-overrides")
    if (
        dcp > 1
        and not cfg.get("kv_lora_rank")
        and tp < dcp * cfg.get("num_key_value_heads", cfg.get("num_attention_heads", 1))
    ):
        out["missing"].append("DCP KV head replication layout")
    packed = "kda" in kinds and "dsa" in kinds and not missing
    block = positive(flags.get("block-size", 1 if engine == "sglang" else 16), "block-size")
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
    hybrid_unpacked = not packed and (len(set(kinds)) > 1 or any(c.state for r in ranks for c in r))
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
        return attention + recurrent

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
        requests=scenario.requests,
        attention_bytes_per_token_per_gpu=max(
            sum(c.width * n for c, n in r.items() if not c.state) / dcp for r in ranks
        ),
    )
    # Unknown groups must not turn the sum of known groups into a purported total.
    if out["missing"]:
        return
    out["required_bytes_per_gpu"] = max(cost(r, requested, scenario.requests) for r in ranks)
    out["required_bytes_per_replica"] = tp * sum(cost(r, requested, scenario.requests) for r in ranks)
    if prefix:
        out["shared_required_bytes_per_gpu"] = max(cost(r, requested, scenario.requests, prefix) for r in ranks)
    budget, source = budget_range(flags, engine, vram, metadata, tp * pp, observed, notes)
    out.update(budget_source=source, budget_bytes_per_gpu=budget)
    blocks = flags.get("num-gpu-blocks-override") if engine == "vllm" else None
    if blocks and all(not c.state and not c.window for r in ranks for c in r):
        budget = [positive(blocks, "num-gpu-blocks-override") * max(cost(r, logical_block, 1) for r in ranks)] * 2
        source = "explicit"
        out.update(budget_source=source, budget_bytes_per_gpu=budget)
    if budget is None:
        out["missing"].append("KV memory budget")
        return
    if not all(cost(r, requested, 1) for r in ranks):
        raise UnknownLayoutError("empty cache layout")

    def fit(limit):
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
    if engine == "sglang" and flags.get("max-total-tokens"):
        cap = positive(flags["max-total-tokens"], "max-total-tokens") // logical_block * logical_block

        def capped(pair):
            return min(cap, pair[0]), min(cap // roundup(requested, logical_block), pair[1])

        low, high = capped(low), capped(high)
    if hybrid_unpacked:
        low = (0, 0)
    out.update(capacity_range=[low[0], high[0]], complete_requests_range=[low[1], high[1]])
    if source != "profile_upper_bound" and not hybrid_unpacked:
        out.update(capacity_tokens=high[0], complete_requests=high[1])
