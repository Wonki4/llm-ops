"""GPU-resident KV for a Kimi DSA/KDA target with a windowed GQA DSpark drafter."""

import json
from dataclasses import dataclass

from app.services.kv_draft_metadata import resolve_draft
from app.services.kv_layout import UnknownLayoutError, dtype_bytes, positive, resolve_dtype, shard, text_config


def dspark_config(flags):
    """vLLM appends dotted options as one JSON argument after all whole-object options."""
    dotted = {
        key.removeprefix("speculative-config.").replace("-", "_"): value
        for key, value in flags.items()
        if key.startswith("speculative-config.")
    }
    if dotted:
        config = {}
        for key, value in dotted.items():
            try:
                config[key] = json.loads(value)
            except (ValueError, TypeError):
                config[key] = value
    else:
        try:
            config = json.loads(flags.get("speculative-config", "{}"))
        except (ValueError, TypeError):
            raise UnknownLayoutError("invalid --speculative-config JSON") from None
    if not isinstance(config, dict):
        raise UnknownLayoutError("--speculative-config must contain a JSON object")
    return config if config.get("method") == "dspark" else None


@dataclass(frozen=True)
class DraftCache:
    lookahead: int
    window: int
    row_bytes: int
    active: int

    def kv_bytes(self, context):
        # At least W-1 preceding positions and the lookahead must coexist during
        # a full-width step. FLASH_ATTN pages contain a multiple of 16 slots.
        # The pinned image may pad these pages to match the target block pool.
        slots = min(context, self.window - 1) + self.lookahead
        return ((slots + 15) // 16) * 16 * self.row_bytes


def prepare_draft(out, metadata, config, flags, tp, pp, dcp, scenario, max_seqs):
    model = config.get("model")
    if not isinstance(model, str) or not model:
        raise UnknownLayoutError("DSpark draft model")
    revision = config.get("revision")
    if "resolved_draft" in metadata:
        reference = metadata["resolved_draft"]
    else:
        reference = resolve_draft(model, revision)
    if reference is None:
        raise UnknownLayoutError(metadata.get("draft_error") or "DSpark draft checkpoint metadata or revision")
    if pp != 1:
        raise UnknownLayoutError("DSpark draft pipeline rank placement")
    known_options = {
        "model",
        "method",
        "revision",
        "num_speculative_tokens",
        "draft_tensor_parallel_size",
        "draft_decode_context_parallel_size",
        "kv_cache_dtype",
        "draft_sample_method",
        "rejection_sample_method",
        "attention_backend",
        "enforce_eager",
        "enable_adaptive_verification",
    }
    unknown = sorted(set(config) - known_options)
    if unknown:
        raise UnknownLayoutError("DSpark unsupported draft configuration: " + ", ".join(unknown))
    if config.get("enable_adaptive_verification"):
        raise UnknownLayoutError("DSpark adaptive verification width")
    if revision is not None and not isinstance(revision, str):
        raise UnknownLayoutError("DSpark draft revision")
    width = positive(config.get("num_speculative_tokens", reference["max_draft_tokens"]), "DSpark draft token count")
    if width > reference["max_draft_tokens"]:
        raise UnknownLayoutError("DSpark draft token count exceeds checkpoint width")
    draft_tp = positive(config.get("draft_tensor_parallel_size", tp), "DSpark draft TP")
    draft_dcp = positive(config.get("draft_decode_context_parallel_size", 1), "DSpark draft DCP")
    if draft_tp not in (1, tp) or draft_dcp != 1:
        raise UnknownLayoutError("DSpark sliding-window GQA draft TP/DCP placement")
    if config.get("attention_backend") not in (None, "FLASH_ATTN"):
        raise UnknownLayoutError("DSpark draft attention page alignment")
    if any(flags.get(k) for k in ("num-gpu-blocks-override", "disable-hybrid-kv-cache-manager")):
        raise UnknownLayoutError("DSpark draft/target cache pool layout override")
    heads = positive(reference["num_key_value_heads"], "DSpark KV heads")
    shard(reference["num_attention_heads"], draft_tp)
    if heads >= draft_tp:
        local_heads = shard(heads, draft_tp)
    else:
        shard(draft_tp, heads)
        local_heads = 1
    dtype = resolve_dtype(config.get("kv_cache_dtype"), resolve_dtype(flags.get("dtype"), reference["dtype"]))
    row_bytes = 2 * reference["num_hidden_layers"] * local_heads * reference["head_dim"] * dtype_bytes(dtype)
    active = min(scenario.requests, max_seqs) if max_seqs else scenario.requests
    batched = flags.get("max-num-batched-tokens")
    if batched is not None:
        # The target verifies one regular position and up to K candidate positions.
        simultaneous = positive(batched, "max-num-batched-tokens") // (1 + width)
        if simultaneous == 0:
            raise UnknownLayoutError("DSpark batch token budget cannot fit one full-width verification")
        active = min(active, simultaneous)
    lookahead = width if reference["sample_from_anchor"] else width + 1
    draft = DraftCache(lookahead, reference["sliding_window"], row_bytes, active)
    weights = reference.get("weight_bytes")
    weight_floor = (weights + draft_tp - 1) // draft_tp if weights else None
    workspace = None
    if flags.get("use-replayssm", "").lower() in ("true", "1") and max_seqs:
        target = text_config(metadata["config"])
        linear = target["linear_attn_config"]
        per_rank_dim = shard(linear["num_heads"], tp) * linear["head_dim"]
        activation = resolve_dtype(flags.get("dtype"), target.get("torch_dtype") or target.get("dtype"))
        # KDARecoverSSMRecords.allocate keeps an FP32 correction [D] and a
        # key/gate [2D] in activation dtype per layer, scheduler row and verify
        # position. Records are allocated at engine startup outside the KV pool.
        record_bytes = per_rank_dim * (4 + 2 * dtype_bytes(activation))
        workspace = len(linear["kda_layers"]) * max_seqs * (width + 1) * record_bytes
    if out["budget_source"] == "profile_upper_bound":
        if weight_floor is None:
            raise UnknownLayoutError("DSpark draft resident weight bytes for GPU profile budget")
        low, high = out["budget_bytes_per_gpu"]
        deduction = weight_floor + (workspace or 0)
        out["budget_bytes_per_gpu"] = [max(0, low - deduction), max(0, high - deduction)]
    out["speculation"] = {
        "method": "dspark",
        "draft_model": model,
        "revision": reference.get("revision"),
        "assumed_revision": reference.get("assumed_revision", False),
        "draft_tokens": width,
        "lookahead_tokens_per_request": lookahead,
        "active_requests": active,
        "candidate_tokens": width * active,
        "lookahead_tokens": lookahead * active,
        "scenario_requests": scenario.requests,
        "max_num_seqs": max_seqs,
        "draft_tp": draft_tp,
        "draft_dcp": draft_dcp,
        "target_dcp": dcp,
        "draft_cache_dtype": dtype,
        "draft_window_tokens": draft.window,
        "draft_bytes_per_token_per_gpu": row_bytes,
        "draft_kv_bytes_per_gpu": draft.kv_bytes(scenario.context) * active,
        "draft_weight_bytes_per_gpu": weight_floor,
        "verification_workspace_bytes_per_gpu": workspace,
    }
    out["assumptions"].append(
        "DSpark: committed tokens at the selected active concurrency; each decode runs the full draft width. "
        "GQA window pages use 16-token minimum alignment. Draft weights use even TP sharding and are deducted only "
        "from a VRAM-derived budget, as are known fixed RecoverSSM verification records. Shared-pool padding, "
        "replicated weight copies, CUDA graphs and additional runtime "
        "workspaces can reduce usable capacity. CPU-offloaded KV is excluded."
    )
    if reference.get("assumed_revision"):
        out["assumptions"].append("Draft revision is not pinned; using the displayed checkpoint reference.")
    return draft


def fit_active_draft(budget, per_request_cost, active, requested, max_seqs, max_len, lookahead, block):
    """Fill logical target pages, reserving draft state and lookahead for each active decode."""
    full = budget // per_request_cost(requested)
    if max_seqs:
        full = min(full, max_seqs)
    if max_len and requested + lookahead > max_len:
        full = 0

    if budget < active * per_request_cost(1):
        return 0, full

    # Divide whole target pages between active requests. Enforcing exactly
    # equal *token* lengths would leave up to active-1 available pages idle.
    def length(pages):
        return max(1, pages * block - lookahead)

    per_request = budget // active
    low, high = 0, 1
    while per_request_cost(length(high)) <= per_request:
        low, high = high, high * 2
    while high - low > 1:
        middle = (low + high) // 2
        if per_request_cost(length(middle)) <= per_request:
            low = middle
        else:
            high = middle
    base_length, next_length = length(low), length(high)
    base_cost = per_request_cost(base_length)
    marginal_cost = per_request_cost(next_length) - base_cost
    extra, residual = divmod(budget - base_cost * active, marginal_cost)
    tokens = active * base_length + extra * (next_length - base_length)

    # Filling part of the next target page may save draft window pages.
    lo, hi = 0, next_length - base_length - 1
    while lo < hi:
        n = (lo + hi + 1) // 2
        if per_request_cost(base_length + n) - base_cost <= residual:
            lo = n
        else:
            hi = n - 1
    return tokens + lo, full
