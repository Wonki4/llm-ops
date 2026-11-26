"""Checkpoint-backed draft costs for GPU-free vLLM throughput scenarios.

Profiles use tensor payloads (safetensors header offsets), matrix shapes, routed
expert counts and cache geometry at immutable public revisions. Separate head
traffic from residency: several bundled drafters reuse the target's LM head.
"""

import json
import math
from functools import lru_cache
from pathlib import Path

from app.services import kimi_dcp_throughput

_ENGINE = "https://github.com/vllm-project/vllm/blob/65ada0c40e18d1100deee9c3deb351326a309e82/vllm/"
_METHODS = {"eagle3", "mtp", "dspark", "dfly"}


class DraftEvidenceError(ValueError):
    def __init__(self, method, missing):
        self.method = method if isinstance(method, str) and method in _METHODS else "other"
        self.missing = missing


@lru_cache(maxsize=1)
def _profiles():
    return json.loads(Path(__file__).with_name("data").joinpath("speculative_cost_profiles.json").read_text())


def speculative_options(flags):
    """Match vLLM's final whole-JSON/dotted override semantics; never echo raw values."""
    dotted = {}
    for key, val in flags.items():
        if key.startswith("speculative-config."):
            try:
                val = json.loads(val)
            except (ValueError, TypeError):
                pass
            dotted[key.removeprefix("speculative-config.").replace("-", "_")] = val
    if dotted:
        return dotted
    if "speculative-config" in flags:
        try:
            value = json.loads(flags["speculative-config"])
        except (ValueError, TypeError):
            return {}
        return value if isinstance(value, dict) else {}
    if "speculative-algorithm" in flags or "num-speculative-tokens" in flags:
        return {"method": str(flags.get("speculative-algorithm") or "").lower()}
    return None


def _public_revision(profile, revision):
    if revision in (None, "main"):
        return profile["revision"]
    if isinstance(revision, str) and len(revision) >= 8:
        matches = [
            value
            for value in (profile["revision"], *profile.get("equivalent_revisions", []))
            if value.startswith(revision)
        ]
        if len(matches) == 1:
            return matches[0]
    return None


def public_draft_known(model, revision):
    row = _profiles().get(model) if isinstance(model, str) else None
    return bool(row and not row["integrated"] and _public_revision(row, revision))


def _matches_geometry(raw, proof):
    # Header/manifest identities establish tensor shapes and storage costs only.
    return bool(proof and len(proof) >= 2 and all(raw.get(key) == value for key, value in proof.items()))


def target_by_geometry(metadata):
    """Recover a public topology for the analytical model from observed file directories."""
    if not metadata.get("config_digest") or not metadata.get("weight_index_digest"):
        return None
    for model, row in _profiles().items():
        if row["integrated"] and _matches_geometry(metadata, row.get("geometry_proof")):
            return model, row["revision"]
        for name, proof in row.get("target_proofs", {}).items():
            if _matches_geometry(metadata, proof):
                return name, row["target_revisions"][name]
    return None


def draft_plan(pool):
    flags = pool["flags"]
    opts = speculative_options(flags)
    if opts is None:
        return None
    method = opts.get("method")
    if not isinstance(method, str) or method not in _METHODS:
        raise DraftEvidenceError(method, "draft_options")
    external = opts.get("model")
    target = pool.get("model_path")
    bundled = not external or external == target
    model = target if bundled else external
    meta = pool.get("metadata") or {}
    profile = _profiles().get(model) if isinstance(model, str) else None
    raw = (meta if bundled else pool.get("draft_metadata")) or {}
    if profile is None and raw.get("config_digest"):
        for name, ref in _profiles().items():
            if ref["integrated"] == bundled and _matches_geometry(raw, ref["geometry_proof"]):
                model, profile = name, ref
                break
    if not profile or profile["integrated"] != bundled or profile["method"] != method:
        raise DraftEvidenceError(method, "draft_checkpoint")
    public = pool.get("public_hub") and raw.get("source") != "mount" and raw.get("hub_origin") != "mirror"
    pin = opts.get("revision")
    if bundled:
        published = (
            public and meta.get("source") in {"pinned_reference", "hub"} and meta.get("revision") == profile["revision"]
        )
        if not published and not _matches_geometry(raw, profile["geometry_proof"]):
            raise DraftEvidenceError(method, "draft_checkpoint")
        if pin not in (None, raw.get("revision"), profile["revision"]):
            raise DraftEvidenceError(method, "draft_checkpoint")
    else:
        published = public and public_draft_known(external, pin)
        if not published and not _matches_geometry(raw, profile["geometry_proof"]):
            raise DraftEvidenceError(method, "draft_checkpoint")
        if not published and pin not in (None, raw.get("revision"), profile["revision"]):
            raise DraftEvidenceError(method, "draft_checkpoint")
        target_pin = profile["target_revisions"].get(target)
        target_published = (
            pool.get("public_hub")
            and meta.get("source") in {"hub", "pinned_reference"}
            and meta.get("hub_origin") != "mirror"
            and target_pin
            and meta.get("revision") == target_pin
        )
        if not target_published and not any(
            _matches_geometry(meta, proof) for proof in profile["target_proofs"].values()
        ):
            raise DraftEvidenceError(method, "draft_geometry")
    supported = {
        "method",
        "model",
        "revision",
        "num_speculative_tokens",
        "kv_cache_dtype",
        "draft_tensor_parallel_size",
        "draft_decode_context_parallel_size",
        "attention_backend",
        "draft_sample_method",
        "rejection_sample_method",
        "enforce_eager",
        "use_local_argmax_reduction",
    }
    width = opts.get("num_speculative_tokens")
    if (
        opts.keys() - supported
        or type(width) is not int
        or not 0 < width <= (profile["max_width"] or 128)
        or opts.get("draft_sample_method") not in (None, "probabilistic", "greedy")
        or opts.get("rejection_sample_method") not in (None, "block", "standard")
        or (
            "use_local_argmax_reduction" in opts
            and (method != "eagle3" or type(opts["use_local_argmax_reduction"]) is not bool)
        )
        or opts.get("attention_backend")
        not in (
            None,
            "FLASH_ATTN",
            "TRITON_ATTN",
            "FLASHINFER_TS",
            "FLASHINFER",
            "ROCM_AITER_UNIFIED_ATTN",
            "ROCM_AITER_MLA",
        )
    ):
        raise DraftEvidenceError(method, "draft_options")
    tp = pool.get("tp") or 1
    dcp = pool.get("dcp") or 1
    layout = kimi_dcp_throughput.layout(pool, model, opts) if dcp > 1 else None
    if (
        pool.get("engine") != "vllm"
        or (pool.get("pp") or 1) != 1
        or (dcp > 1 and not layout)
        or opts.get("draft_tensor_parallel_size", tp) != tp
        or opts.get("draft_decode_context_parallel_size", 1) != 1
        or profile["heads"] % tp
        or (method == "dfly" and pool.get("role") in {"prefill", "decode"})
    ):
        raise DraftEvidenceError(method, "draft_placement")
    measured_layout = pool.get("speculation") or {}
    dtype = (
        measured_layout.get("draft_cache_dtype") or opts.get("kv_cache_dtype") or flags.get("kv-cache-dtype", "auto")
    )
    if dtype not in {"auto", "float16", "bfloat16", "fp8", "fp8_e4m3", "fp8_ds_mla"}:
        raise DraftEvidenceError(method, "draft_geometry")
    if method == "dfly" and (
        dtype not in ("auto", "bfloat16", "fp8_ds_mla")
        or dtype != flags.get("kv-cache-dtype", "auto")
        and {dtype, flags.get("kv-cache-dtype", "auto")} != {"auto", "bfloat16"}
    ):
        raise DraftEvidenceError(method, "draft_options")
    value_bytes = 1 if dtype.startswith("fp8") else 2
    if "latent" in profile:
        kv_row = 656 if dtype == "fp8_ds_mla" and profile["latent"] == 576 else profile["latent"] * value_bytes
    else:
        heads = profile["kv_heads"]
        if max(heads, tp) % min(heads, tp) or dtype == "fp8_ds_mla":
            raise DraftEvidenceError(method, "draft_placement")
        kv_row = 2 * max(heads // tp, 1) * profile["kv_head_dim"] * value_bytes
    if profile.get("qsa"):
        # Both attention-config forms follow the same dotted-override rule as
        # speculative-config. The index precision is independent of the main KV.
        index_options = speculative_options(
            {
                key.replace("attention-config", "speculative-config", 1): val
                for key, val in flags.items()
                if key == "attention-config" or key.startswith("attention-config.")
            }
        )
        index_dtype = (index_options or {}).get("indexer_kv_dtype", profile["qsa"]["default_index_dtype"])
        if index_dtype not in ("fp8", "bf16", "bfloat16"):
            raise DraftEvidenceError(method, "draft_options")
        profile = {**profile, "index_row_bytes": profile["qsa"]["head_dim"] * (1 if index_dtype == "fp8" else 2)}
    source_revision = _public_revision(profile, pin) if not bundled and published else profile["revision"]
    return {
        **profile,
        "revision": source_revision,
        "method": method,
        "draft_tokens": width,
        "dcp_layout": layout,
        "row_bytes_per_rank": measured_layout.get("draft_bytes_per_token_per_gpu", 0) // len(profile["windows"])
        or kv_row,
        "assumed_revision": not bundled and published and opts.get("revision") in (None, "main"),
        "checkpoint_url": f"https://huggingface.co/{model}/tree/{source_revision}",
        "engine_url": _ENGINE + profile["implementation"],
    }


def draft_timing(plan, context, batch, width, prefill_tokens, compute, bandwidth, tp, dispatch):
    """Seconds per prefill and proposal round. All rates are aggregate across TP ranks."""
    layout = plan.get("dcp_layout")
    serial = plan["method"] in {"mtp", "eagle3"}
    invocations = width if serial else 1
    queries = width if serial or layout else width + 1
    if plan["experts"]:
        routed = plan["topk"] / plan["experts"]
        touched = 1 - (1 - routed) ** batch
    else:
        routed = touched = 0
    active = plan["body_params"] + routed * plan["routed_params"]
    head = plan["head_bytes"]
    body_bytes = (
        plan["draft_bytes"] - plan["lookup_bytes"] - plan["routed_bytes"] - (0 if plan["shared_head"] else head)
    )
    if layout:
        # The fused query/KV-A is executed on each rank. The context projection
        # and its separate KV writer execute during prompt ingestion.
        body_bytes += (tp - 1) * 2 * layout["decode_replicated_params"] - 2 * layout["context_params"]
        active += (tp - 1) * layout["decode_replicated_params"] - 7168 * 5 * 7168
    span = sum(min(window or context, context) for window in plan["windows"])
    qsa = plan.get("qsa")
    extra_ops = extra_reads = 0
    if qsa:
        # MTP reuses the first draft step's selected keys. Its index scans all
        # compressed positions once; the value attention reads just the selection.
        span = min(context, qsa["token_topk"] + qsa["compress_ratio"] - 1)
        groups = math.ceil(context / qsa["compress_ratio"])
        extra_ops = 2 * batch * groups * qsa["head_dim"] * qsa["query_heads"] * tp
        extra_reads = batch * groups * plan["index_row_bytes"] * tp
    attn_ops = 4 * plan["heads"] * plan["query_dim"] * span
    local_span = math.ceil(context / layout["ranks"]) * len(plan["windows"]) if layout else span
    kv_reads = batch * local_span * plan["row_bytes_per_rank"] * tp
    ops = extra_ops + batch * (queries * (2 * active + attn_ops) + width * 2 * plan["head_params"])
    # Serial proposals run one backbone and head per token. DFly corrects and
    # projects each position in sequence even though its backbone is parallel.
    head_passes = width if serial or plan["method"] == "dfly" else 1
    traffic = extra_reads + invocations * (body_bytes + touched * plan["routed_bytes"] + kv_reads) + head_passes * head
    decode = max(ops / compute, traffic / bandwidth) + invocations * len(plan["windows"]) * dispatch
    context_params = plan["context_params"] + (routed * plan["routed_params"] if serial else 0)
    if layout:
        context_params = layout["context_params"] * tp
    prefill_ops = 2 * context_params * batch * prefill_tokens
    return prefill_ops / compute, decode


def draft_adjusted_pool(pool, plan, context, batch):
    """Reserve additional weights only when outside the target payload, and draft KV at the requested batch."""
    adjusted = {**pool, "draft_plan": plan}
    # This step is a roofline estimate, not an engine block allocator. Keep KV
    # marked unverified when target component accounting is unavailable.
    budget = pool.get("budget_bytes_per_gpu")
    tp = pool.get("tp") or 1
    budget_based = pool.get("budget_source") == "profile_upper_bound"
    separate = not plan["included_in_target_index"]
    if budget and budget_based and separate:
        replicated = (plan.get("dcp_layout") or {}).get("replicated_draft_bytes", 0)
        extra = replicated + math.ceil((plan["draft_bytes"] - replicated) / tp)
        budget = [max(0, value - extra) for value in budget]
        adjusted["budget_bytes_per_gpu"] = budget
    limit = min(batch, pool.get("max_num_seqs") or batch)
    chunk = int(pool["flags"].get("max-num-batched-tokens", 8192))
    limit = min(limit, chunk // (plan["draft_tokens"] + 1))
    if not limit:
        raise DraftEvidenceError(plan["method"], "draft_options")
    if budget:
        span = sum(min(window or context, context) + plan["draft_tokens"] + 1 for window in plan["windows"])
        if plan["method"] == "dfly":  # Reads historical KV by reference from the target.
            span = (plan["draft_tokens"] + 1) * len(plan["windows"])
        layout = plan.get("dcp_layout")
        if layout:
            # These full-attention MLA draft layers own interleaved slots. Each
            # of the eight ranks owns ceil((T + lookahead) / 8) rows at most.
            span = math.ceil((context + plan["draft_tokens"]) / layout["ranks"]) * len(plan["windows"])
        draft_kv = span * plan["row_bytes_per_rank"]
        if qsa := plan.get("qsa"):
            # Full main K/V is resident. The compressed keys and M-RoPE/raw-key
            # circular buffer are additional, replicated bytes on each rank.
            ratio = qsa["compress_ratio"]
            positions = context + plan["draft_tokens"] + 1
            groups = math.ceil(positions / ratio)
            ring = math.ceil((ratio + plan["draft_tokens"]) / ratio) * ratio
            draft_kv += groups * plan["index_row_bytes"] + ring * qsa["raw_key_bytes"]
        requests = pool.get("requests")
        target_kv = pool.get("required_bytes_per_gpu")
        per_req = math.ceil(target_kv / requests) if target_kv and requests else 0
        if layout:
            per_req = kimi_dcp_throughput.kv_bytes(layout, context + plan["draft_tokens"])
        elif not per_req and pool.get("components"):
            # A partially described target gives a lower bound on resident bytes;
            # use it to rule out impossible batches, never to verify headroom.
            length = context + plan["draft_tokens"]
            per_req = sum(
                row["bytes_per_gpu"] * (1 if row["unit"] == "request" else min(row.get("window") or length, length))
                for row in pool["components"]
            )
        if per_req or budget[1] < draft_kv:
            fit = int(budget[1] // (per_req + draft_kv))
            old = pool.get("complete_requests")
            adjusted["complete_requests"] = min(fit, old) if old is not None else fit
            adjusted.pop("complete_requests_range", None)
    adjusted["max_num_seqs"] = limit
    return adjusted
