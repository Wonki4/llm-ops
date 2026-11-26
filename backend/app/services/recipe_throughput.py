"""GPU-free, steady-state text generation scenarios using a compute/bandwidth roofline.

Each cache condition fixes the reusable prefix and evaluates both hardware
efficiency endpoints. The combined envelope is retained for earlier clients.
"""
import math
from collections import Counter
from dataclasses import dataclass

from app.services import deepseek_throughput, kimi_dcp_throughput, speculative_throughput
from app.services.kv_capacity import memory_size
from app.services.kv_layout import text_config
from app.services.recipe_throughput_evidence import gpu_evidence, model_evidence, workload_kind

VERSION = "roofline-v4"
COMPUTE_EFFICIENCY = (0.15, 0.55)
MEMORY_EFFICIENCY = (0.30, 0.75)
# Planning overheads in microseconds, per layer and forward invocation. These
# cover dependent kernels/fusions, small-message TP collectives, and routing.
# They are exposed with the result alongside the assumed utilization factors.
LAYER_DISPATCH_US = (40, 8)
COLLECTIVE_US_PER_TP_HOP = (24, 3)
MOE_ROUTING_US = (40, 8)
ROOFLINE = "https://developer.nvidia.com/blog/co-designing-ai-model-attention-for-fast-interactive-long-context-inference"


class EstimateUnavailableError(ValueError):
    def __init__(self, reason, status="unavailable"):
        self.reason = reason
        self.status = status


def _positive(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _on(value):
    return str(value).lower() in {"true", "1", "yes"}


def _evidence_link(label, data):
    url = data.get("source_url") or data.get("source")
    if isinstance(url, str) and url.startswith(("https://", "http://")):
        return {"label": label, "url": url}
    return None


def _prefix_enabled(flags):
    return not (any(_on(flags.get(key)) for key in
                    ("no-enable-prefix-caching", "disable-prefix-caching", "disable-radix-cache"))
                or str(flags.get("enable-prefix-caching", "")).lower() in {"false", "0"})


def _check_memory(pool):
    budget = pool.get("budget_bytes_per_gpu")
    if (pool.get("budget_exceeds_vram") or pool.get("weights_exceed_vram")
            or (budget is not None and budget[1] <= 0)):
        raise EstimateUnavailableError("gpu_memory", "infeasible")


def _batch_limits(pool, count, prefix, notes):
    """Cap active slots; queued requests do not increase aggregate generation speed."""
    flags = pool["flags"]
    limit = pool.get("max_num_seqs") or count
    draft = pool.get("speculation") or pool.get("integrated_speculation") or {}
    if draft.get("active_requests"):
        limit = min(limit, draft["active_requests"])
    for name in ("max-num-seqs", "max-num-active-seqs", "max-num-batched-tokens"):
        if name in flags:
            limit = min(limit, int(flags[name]))
    if limit < 1:
        raise EstimateUnavailableError("invalid_recipe")
    cold = warm = min(limit, count)
    full = pool.get("complete_requests_range")
    cap = pool.get("complete_requests") if full is None else full[1]
    budget = pool.get("budget_bytes_per_gpu")
    if cap is None or pool.get("weight_fit_unverified") or (full is not None and full[0] < min(cold, cap)):
        # Calculate a conditional rate if the estimated active slots actually fit.
        notes.add("kv_unverified")
    if cap is not None and cap <= 0:
        # Even the first request must build the complete shared prefix once.
        raise EstimateUnavailableError("kv_insufficient", "infeasible")
    if cap is not None:
        cold = min(cold, cap)
        warm = cold
        # The KV estimator evaluates the full requested batch with a page-aligned
        # shared prefix. Recover fixed shared bytes + private bytes per request.
        private = pool.get("required_bytes_per_gpu")
        shared = pool.get("shared_required_bytes_per_gpu")
        n = pool.get("requests")
        if prefix and budget and n and n > 1 and private and shared is not None and 0 < shared < private:
            once = (private - shared) / (n - 1)
            marginal = private / n - once
            if marginal >= 0 and once <= budget[1]:
                shared_limit = count if marginal == 0 else math.floor((budget[1] - once) / marginal)
                warm = max(cold, min(limit, count, shared_limit))
                if warm > cold and once + marginal * warm > budget[0]:
                    notes.add("kv_unverified")
    if cold < count or warm < count:
        notes.add("scheduler_capped")
    return cold, warm


def _attention_layers(cfg, heads, n):
    """Number of query layers by attention span. Zero-span recurrent layers use the weight term."""
    window = cfg.get("sliding_window") or 0
    topk = (cfg.get("dsa_config") or {}).get("index_topk") or cfg.get("index_topk") or 0
    layers = cfg.get("layer_types") or cfg.get("layers_block_type")
    if layers and len(layers) == n:
        spans = Counter()
        for kind in layers:
            if kind in {"linear_attention", "mamba", "mamba2", "ssm", "conv", "moe", "mlp"}:
                continue
            span = window if kind in {"sliding_attention", "sliding_window"} else topk if "sparse" in kind else 0
            spans[span] += 1
    elif (cfg.get("dsa_config") or {}).get("dsa_layers"):
        spans = {topk: len(cfg["dsa_config"]["dsa_layers"])}
    elif cfg.get("compress_ratios"):
        # DeepSeek-V4 attends the sliding window plus selected compressed entries.
        ratios = cfg["compress_ratios"][:n]
        spans = Counter({window: n})
        spans[topk or 2048] += sum(1 for ratio in ratios if ratio)
    else:
        sliding = min(n, cfg.get("max_window_layers", n)) if window and cfg.get("use_sliding_window", True) else 0
        spans = Counter({0: n - sliding})
        spans[window] += sliding
        if topk and not sliding:
            spans = {topk: n}
    dimension = cfg.get("head_dim") or max(1, cfg["hidden_size"] // heads)
    return {span: 4 * heads * dimension * layers for span, layers in spans.items() if layers > 0}


def _attention_flops(spans, before, after):
    def pairs(length, window):
        width = min(length, window) if window else length
        return width * (width + 1) / 2 + (length - width) * width

    return sum(scale * (pairs(after, span) - pairs(before, span)) for span, scale in spans.items())


def _kv_step_bytes(pool, cfg, ctx, tp):
    # State rows are read and updated. Sparse latents gather top-k positions;
    # a sparse index still scans stored keys for the retained context.
    layout = (pool.get("draft_plan") or {}).get("dcp_layout")
    if layout:
        # KDA reads and writes its rank-local state once during a modeling step.
        return (kimi_dcp_throughput.kv_bytes(layout, ctx) + layout["target_state"]) * tp
    topk = cfg.get("index_topk") or (cfg.get("dsa_config") or {}).get("index_topk")
    rows = pool.get("components") or []
    compressed = deepseek_throughput.kv_read_bytes(cfg, rows, ctx, tp)
    if compressed is not None:
        return compressed
    if rows:
        total = 0
        for row in rows:
            kind = row["kind"].lower()
            span = min(ctx, row.get("window") or ctx)
            if topk and "mla" in kind and not row.get("window"):
                span = min(span, topk)
            total += row["bytes_per_gpu"] * (2 if row["unit"] == "request" else span)
        return total * tp
    layers = cfg["num_hidden_layers"]
    if cfg.get("kv_lora_rank"):
        return 2 * (cfg["kv_lora_rank"] + (cfg.get("qk_rope_head_dim") or 0)) * layers * ctx * tp
    heads = cfg.get("num_key_value_heads") or cfg["num_attention_heads"]
    dim = cfg.get("head_dim") or max(1, cfg["hidden_size"] // cfg["num_attention_heads"])
    # Use 16-bit K/V as the baseline for unclassified layouts.
    return 4 * max(heads, tp) * dim * layers * ctx


def _touched_weights(model, total_bytes, batch):
    total = model.get("total_params") or model["active_params"]
    fraction = min(1.0, model["active_params"] / total)
    if model.get("kind") != "moe" or fraction >= 1.0:
        return total_bytes
    # Approximate overlapping expert selections in a batch; the entire MoE
    # checkpoint is neither streamed per request nor divided by the batch size.
    return total_bytes * -math.expm1(batch * math.log1p(-fraction))


def _weight_interval(model, pool):
    exact = model.get("weight_bytes") or pool["metadata"].get("weight_bytes")
    if _positive(exact):
        return exact, exact
    interval = model.get("weight_bytes_range") or []
    if len(interval) == 2 and all(_positive(v) for v in interval) and interval[0] <= interval[1]:
        return interval
    raise EstimateUnavailableError("model_unknown")


def _check_weight_fit(model, pool, flags):
    gb = pool.get("gpu_vram_gb")
    fitted = pool.get("budget_bytes_per_gpu")
    explicit = pool.get("budget_source") == "explicit" and fitted is not None
    plan = pool.get("draft_plan")
    if not gb or (fitted is not None and not explicit):
        return
    if explicit and pool["metadata"].get("weight_bytes"):
        # KV sizing owns the target's loader, CPU placement, and any draft it
        # already sized. Account only for a separate throughput-resolved drafter.
        floor = pool.get("resident_weight_floor_bytes_per_gpu")
        if (floor is not None and plan and not plan["included_in_target_index"]
                and not pool.get("speculation") and not pool.get("integrated_speculation")
                and not pool.get("weight_offload") and not int(flags.get("offload-group-size", 0))):
            replicated = (plan.get("dcp_layout") or {}).get("replicated_draft_bytes", 0)
            extra = replicated + math.ceil((plan["draft_bytes"] - replicated) / (pool.get("tp") or 1))
            if floor + extra + fitted[0] > gb * 1e9:
                raise EstimateUnavailableError("gpu_memory", "infeasible")
        return
    # Only reject when even the smaller parameter payload cannot fit and
    # there is no unresolved CPU placement, dynamic precision, or skipped tower.
    if (float(flags.get("cpu-offload-gb", 0)) or int(flags.get("offload-group-size", 0)) or flags.get("quantization")
            or _on(flags.get("language-model-only")) or any(k.startswith("limit-mm-per-prompt") for k in flags)
            or flags.get("load-format", "auto") not in {"auto", "safetensors", "fastsafetensors", "pt"}):
        return
    meta = pool["metadata"]
    cfg = text_config(meta["config"])
    if cfg.get("engram_layer_ids"):
        return
    stored = (cfg.get("dtype") or cfg.get("torch_dtype")
              or meta["config"].get("dtype") or meta["config"].get("torch_dtype"))
    loaded = flags.get("dtype", "auto")
    half = {"float16", "bfloat16", "fp16", "bf16", "half"}
    single = {"float32", "float", "fp32"}
    if loaded == "auto" and stored in single:
        return  # The default loader narrows FP32 checkpoints.
    if loaded != "auto" and not ((stored in half and loaded in half) or (stored in half | single and loaded in single)):
        # The file payload need not be the minimum GPU payload after conversion.
        return
    lower, _ = _weight_interval(model, pool)
    parts = meta.get("weight_components") or {}
    if plan and not plan["included_in_target_index"]:
        lower += plan["draft_bytes"]
    if not plan and not pool.get("speculation") and not pool.get("integrated_speculation"):
        # Legacy metadata can omit unused prediction heads; do not reject a
        # deployment based on unverified draft residency.
        if cfg.get("num_nextn_predict_layers") and "draft" not in parts:
            return
        lower -= parts.get("draft", 0)
    # vLLM's explicit KV size takes precedence over gpu-memory-utilization.
    fraction_flag = "mem-fraction-static" if pool.get("engine") == "sglang" else "gpu-memory-utilization"
    fraction = 1.0 if explicit else float(flags.get(fraction_flag, 1))
    if not 0 < fraction <= 1:
        raise EstimateUnavailableError("invalid_recipe")
    workers = (pool.get("tp") or 1) * (pool.get("pp") or 1)
    budget = gb * 1e9 * workers * fraction
    if lower >= budget:
        raise EstimateUnavailableError("weight_budget", "infeasible")
    if explicit and lower + fitted[0] * workers > budget:
        raise EstimateUnavailableError("gpu_memory", "infeasible")


@dataclass
class StageTime:
    batch: int
    prefill: float
    decode: float

    @property
    def rpm(self):
        return 60 * self.batch / (self.prefill + self.decode)


@dataclass
class CacheStages:
    reused_tokens: int
    times: tuple[StageTime, StageTime]


def _rates(rpm, reused_tokens, batch, inputs, outputs):
    low, high = sorted(rpm)
    if not all(_positive(rate) for rate in (low, high)):
        raise EstimateUnavailableError("model_unknown")
    return {"rpm": [low, high], "input_tpm": [low * inputs, high * inputs],
            "output_tpm": [low * outputs, high * outputs], "reused_input_tokens": reused_tokens,
            "effective_concurrency": batch}


def _service_time(pool, gpu, model, cfg, lengths, batch, cached, fast, notes):
    inputs, outputs = lengths
    flags = pool["flags"]
    tp, pp = pool.get("tp") or 1, pool.get("pp") or 1
    # A pipeline cannot execute consecutive single-stream layers in parallel.
    pipeline_fill = batch / (batch + pp - 1)
    devices = tp * pp * pipeline_fill
    communication = 1 + math.log2(tp) * (0.06 if fast else 0.16)
    calc = gpu["bf16_tflops"]
    fmt = (pool["metadata"].get("weight_format") or "").lower()
    runtime_fp8 = not fmt and flags.get("quantization") in {"fp8", "fp8_per_channel"}
    if (fmt in {"fp8", "mxfp8"} or runtime_fp8) and gpu.get("fp8_tflops"):
        calc = gpu["fp8_tflops"]
    compute = calc * 1e12 * devices * COMPUTE_EFFICIENCY[fast] / communication
    bandwidth = gpu["bandwidth_gbps"] * 1e9 * devices * MEMORY_EFFICIENCY[fast] / communication
    spans = _attention_layers(cfg, cfg["num_attention_heads"], cfg["num_hidden_layers"])

    layout = (pool.get("draft_plan") or {}).get("dcp_layout")
    if layout:
        spans = {0: 2 * layout["target_heads"] * (layout["qk_dim"] + layout["value_dim"]) * layout["target_layers"]}

    def attention_cost(before, after):
        if layout:
            # KDA's recurrent matrix uses fixed work per token. The range spans
            # fused versus unfused state gating, rank-one updates and readout.
            updates = (after - before) * layout["kda_layers"] * layout["kda_heads"] * layout["kda_dim"] ** 2
            return _attention_flops(spans, before, after) + updates * (4 if fast else 8)
        compressed = deepseek_throughput.attention_flops(cfg, before, after)
        if compressed is not None:
            notes.add("deepseek_compression")
            return compressed
        return _attention_flops(spans, before, after)

    weights = _weight_interval(model, pool)[0 if fast else 1]
    if model.get("weight_basis") in {"shard_files", "precision_scenario"}:
        notes.add("weight_approx")
    # Exclude inactive modules. CPU-offloaded active experts still need to be fetched.
    residency = pool.get("weight_residency") or {}
    omitted = residency.get("excluded_bytes") or {}
    weights -= sum(omitted.get(k, 0) for k in ("vision", "audio", "draft", "engram_embeddings"))
    plan = pool.get("draft_plan")
    if plan and plan["included_in_target_index"] and not model.get("weight_basis"):
        weights -= max(0, plan["draft_bytes"] - omitted.get("draft", 0))
    if weights <= 0:
        raise EstimateUnavailableError("model_unknown")
    if runtime_fp8:
        # Dynamic FP8 leaves embeddings, scale tensors, and unsupported layers
        # at their load precision. Vary the traffic between checkpoint-sized
        # and fully converted rather than assuming all weights halve exactly.
        notes.add("runtime_fp8")
        stored = cfg.get("dtype") or cfg.get("torch_dtype") or pool["metadata"]["config"].get("dtype")
        if fast and stored in {"bfloat16", "float16", "half", "bf16", "fp16"}:
            weights /= 2
    active = model["active_params"]
    prefill_params = model.get("active_prefill_params") or active
    new_input = max(1, inputs - cached)
    pf_ops = batch * (2 * prefill_params * new_input + attention_cost(inputs - new_input, inputs))
    # Estimate weight passes across scheduler chunks at steady load.
    chunk = int(flags.get("max-num-batched-tokens", 8192))
    if chunk <= 0:
        raise EstimateUnavailableError("invalid_recipe")
    slices = max(1, math.ceil(batch * new_input / chunk))
    dispatch_us = LAYER_DISPATCH_US[fast] + math.log2(tp) * COLLECTIVE_US_PER_TP_HOP[fast]
    if model.get("kind") == "moe":
        dispatch_us += MOE_ROUTING_US[fast]
    dispatch = cfg["num_hidden_layers"] * dispatch_us / 1e6
    pf_bytes = slices * _touched_weights(model, weights, max(1, batch * new_input / slices))
    if cached:
        pf_bytes += batch * _kv_step_bytes(pool, cfg, cached, tp)
    prefill = max(pf_ops / compute, pf_bytes / bandwidth) + slices * dispatch
    # First output token comes from prefill. Later tokens attend to the growing context.
    steps = outputs - 1
    spec = plan or pool.get("speculation") or pool.get("integrated_speculation") or {}
    candidates = min(spec.get("draft_tokens", 0), max(0, steps - 1))
    verify_width = candidates + 1
    # Without a verified acceptance curve use zero/all accepted as two scenarios.
    passes = math.ceil(steps / verify_width) if fast else steps
    verified = min(steps, verify_width) if steps else 0
    decode_batches = min(batch * verified, chunk) if verified else batch
    kv_reads = batch * _kv_step_bytes(pool, cfg, inputs + steps / 2, tp)
    decode_ops = 2 * active * batch * verified
    if steps:
        decode_ops += batch * verified * attention_cost(inputs, inputs + steps) / steps
    weight_traffic = _touched_weights(model, weights, decode_batches)
    decode = passes * (max(decode_ops / compute, (weight_traffic + kv_reads) / bandwidth) + dispatch)
    if candidates:
        notes.add("speculation")
        if plan:
            # The drafter may use BF16 even if the target uses FP8 GEMMs.
            draft_compute = gpu["bf16_tflops"] * 1e12 * devices * COMPUTE_EFFICIENCY[fast] / communication
            preparation, proposal = speculative_throughput.draft_timing(
                plan, inputs + steps // 2, batch, candidates, new_input, draft_compute, bandwidth, tp,
                dispatch_us / 1e6,
            )
            prefill += preparation
            decode += passes * proposal
        else:
            notes.add("draft_traffic_only")
            draft_bytes = (spec.get("draft_weight_bytes_per_gpu") or 0) * tp
            if not draft_bytes:
                draft_bytes = (pool["metadata"].get("weight_components") or {}).get("draft") or 0
            decode += passes * draft_bytes / bandwidth
    if layout:
        prefill += kimi_dcp_throughput.prefill_transfer(layout, batch, new_input, cached, chunk, fast)
        decode += kimi_dcp_throughput.exchange_time(layout, plan, batch, passes, verified, candidates, fast)
    offloaded = omitted.get("routed_experts", 0)
    if ("routed_experts" not in omitted
            and (float(flags.get("cpu-offload-gb", 0)) > 0 or int(flags.get("offload-group-size", 0)) > 0)):
        # Use the eligible tensor payload, not the configured quota as a measured transfer.
        placement = pool.get("weight_offload") or {}
        minimum = placement.get("min_bytes_per_gpu") or 0
        maximum = placement.get("max_bytes_per_gpu")
        if minimum != maximum:
            notes.add("weight_offload_unmeasured")
        per_rank = minimum if fast else maximum
        offloaded = min(weights, per_rank * tp * pp) if per_rank is not None else weights
    if offloaded or omitted.get("engram_embeddings"):
        notes.add("offload")
    if offloaded:
        # Host link per rank is a scenario, not measured effective GPU bandwidth.
        board = str(gpu.get("variant") or pool.get("gpu_label_value") or pool["gpu_type"]).upper()
        coherent = any(name in board for name in ("GB200", "GB300"))
        link_gbps = (100, 450)[fast] if coherent else (12, 48)[fast]
        host_bandwidth = link_gbps * 1e9 * devices
        share = min(1.0, offloaded / weights)
        prefill += share * pf_bytes / host_bandwidth
        decode += share * passes * weight_traffic / host_bandwidth
    return StageTime(batch, prefill, decode)


def _stages(pool, scenario, notes, sources):
    i, o, count, percent = scenario
    flags = pool.get("flags") or {}
    gpu_count = pool.get("gpu_count")
    if gpu_count == 0:
        raise EstimateUnavailableError("no_gpu")
    gpu = gpu_evidence(pool.get("gpu_label_value")) or gpu_evidence(pool.get("gpu_type"))
    if not gpu or not gpu_count:
        raise EstimateUnavailableError("gpu_unknown")
    if (pool.get("engine") not in {"vllm", "sglang"} or flags.get("runner") == "pooling"
            or flags.get("task") in {"embed", "embedding", "classify", "score", "reward", "pooling"}
            or flags.get("convert") in {"embed", "classify", "reward"}):
        raise EstimateUnavailableError("unsupported_workload")
    if any(int(flags.get(k, 1)) > 1 for k in ("data-parallel-size", "dp-size", "dp")):
        raise EstimateUnavailableError("unsupported_topology")
    tp, pp = pool.get("tp") or 1, pool.get("pp") or 1
    if tp * pp > gpu_count:
        raise EstimateUnavailableError("invalid_recipe", "infeasible")
    if tp * pp > 1:
        notes.add("parallelism")
    meta = pool.get("metadata") or {}
    cfg = text_config(meta.get("config") or {})
    block_types = cfg.get("layers_block_type") or cfg.get("layer_types")
    if not cfg.get("num_hidden_layers") and isinstance(block_types, list) and 0 < len(block_types) <= 4096:
        cfg = {**cfg, "num_hidden_layers": len(block_types)}
    if workload_kind(meta, pool.get("model_path") or "") not in {"text", "unknown"}:
        raise EstimateUnavailableError("unsupported_workload")
    model = model_evidence(meta, pool.get("model_path") or "")
    if not model and speculative_throughput.speculative_options(flags) is not None:
        # Private checkpoints can share the verified public geometry independently
        # of where weights are served. This never matches measured throughput.
        identity = speculative_throughput.target_by_geometry(meta)
        if identity:
            reference_name, revision = identity
            model = model_evidence({**meta, "source": "pinned_reference", "revision": revision}, reference_name)
    if not model or not _positive(model.get("active_params")) or not all(
        _positive(cfg.get(k)) for k in ("num_attention_heads", "num_hidden_layers", "hidden_size")
    ):
        raise EstimateUnavailableError("model_unknown")
    _check_weight_fit(model, pool, flags)
    if flags.get("dtype") in {"float", "float32", "fp32"}:
        # BF16 accelerator throughput cannot price an FP32 compute path.
        raise EstimateUnavailableError("unsupported_precision")
    host_kv = pool.get("host_kv_offload")
    if host_kv is not None and host_kv.get("configured_bytes") != 0:
        # The warm scenario assumes a prefix already resident on the GPU.
        notes.add("host_kv_transfer_unmeasured")
    for label, descriptor in ((gpu.get("variant") or pool["gpu_type"], gpu), (pool["model_path"], model)):
        link = _evidence_link(label, descriptor)
        if link and link not in sources:
            sources.append(link)
    if model.get("architecture_source") == "public_model_spec":
        notes.add("public_model_spec")
        link = _evidence_link(f"{pool['model_path']} · config", {"source_url": model.get("config_url")})
        if link and link["url"] not in {source["url"] for source in sources}:
            sources.append(link)
    weight_source = model.get("weight_source_url")
    if weight_source and weight_source not in {link["url"] for link in sources}:
        link = _evidence_link(f"{pool['model_path']} · safetensors", {"source_url": weight_source})
        if link:
            sources.append(link)
    if model.get("kind") == "moe":
        notes.add("moe_routing")
    if not pool.get("components") or cfg.get("index_topk") or cfg.get("dsa_config") or cfg.get("compress_ratios"):
        notes.add("attention_approx")
    maximum = pool.get("max_model_len") or flags.get("max-model-len")
    if maximum in (None, "auto", "-1", -1):
        maximum = cfg.get("max_position_embeddings")
    if maximum and i + o > memory_size(maximum):
        raise EstimateUnavailableError("context_limit", "infeasible")
    if flags.get("hf-overrides") or flags.get("hf-config-path"):
        raise EstimateUnavailableError("invalid_recipe")
    capacity_plan = pool.get("speculation") or pool.get("integrated_speculation")
    if capacity_plan and not capacity_plan.get("draft_model"):
        plan = None
    else:
        try:
            plan = speculative_throughput.draft_plan(pool)
        except speculative_throughput.DraftEvidenceError:
            if not capacity_plan:
                raise
            # The dedicated KV rule can still provide weight traffic for this
            # checkpoint when its compute geometry has no verified profile.
            plan = None
    if plan:
        if capacity_plan:
            # KV sizing has already reserved this exact draft's weights and pages.
            pool = {**pool, "draft_plan": plan}
        else:
            pool = speculative_throughput.draft_adjusted_pool(pool, plan, i + o, count)
        if pool.get("missing"):
            notes.add("kv_unverified")
        if plan.get("dcp_layout"):
            notes.update(("kimi_mla_dcp", "kv_unverified"))
            for label, url in (("DSpark DCP · vLLM", kimi_dcp_throughput.SOURCE),
                               ("AMD Infinity Fabric", kimi_dcp_throughput.HARDWARE)):
                if url not in {source["url"] for source in sources}:
                    sources.append({"label": label, "url": url})
            plan["engine_url"] = kimi_dcp_throughput.DISPATCH
        if plan["assumed_revision"]:
            notes.add("draft_revision_assumed")
        for label, url in ((f"{plan['method']} · checkpoint", plan["checkpoint_url"]),
                           (f"{plan['method']} · vLLM", plan["engine_url"])):
            if url not in {link["url"] for link in sources}:
                sources.append({"label": label, "url": url})
        _check_memory(pool)
        _check_weight_fit(model, pool, flags)
    enabled = _prefix_enabled(flags)
    # The first decode logit still requires the last prompt token even if the
    # whole prompt repeats. The lookup uses the preceding complete blocks.
    shared = min(i * percent // 100, i - 1) if enabled else 0
    cold_batch, warm_batch = _batch_limits(pool, count, shared, notes)
    warm_tokens = 0
    if shared:
        # The physical group sizes used to validate prefix-match-unit are
        # resolved at startup and may exceed the KV estimate's assumed page.
        unit = int(flags.get("prefix-match-unit") or pool.get("block_tokens")
                   or flags.get("block-size") or (1 if pool["engine"] == "sglang" else 16))
        if unit <= 0:
            raise EstimateUnavailableError("invalid_recipe")
        warm_tokens = shared // unit * unit

    def at_cache_state(batch, cached):
        slow = _service_time(pool, gpu, model, cfg, (i, o), batch, cached, False, notes)
        fast = _service_time(pool, gpu, model, cfg, (i, o), batch, cached, True, notes)
        return CacheStages(cached, (slow, fast))

    cases = {"cold": at_cache_state(cold_batch, 0)}
    if warm_tokens:
        notes.add("prefix_reuse")
        cases["warm"] = at_cache_state(warm_batch, warm_tokens)
    elif percent and not enabled:
        notes.add("prefix_disabled")
    return cases


def _pd_rates(pools, workload, notes, sources):
    roles = {pool.get("role"): pool for pool in pools}
    if len(pools) != 2 or set(roles) != {"prefill", "decode"}:
        raise EstimateUnavailableError("unsupported_topology")
    pre, dec = roles["prefill"], roles["decode"]
    if any(pre.get(key) != dec.get(key) for key in ("model_path", "engine", "tp", "pp", "dcp", "kv_dtype")):
        raise EstimateUnavailableError("unsupported_topology")
    if ((pre.get("metadata") or {}).get("config_digest") != (dec.get("metadata") or {}).get("config_digest")
            or not pre.get("components")):
        raise EstimateUnavailableError("unsupported_topology")
    inputs, outputs, concurrency, percent = workload
    # Model a pair (one replica per role) with a simple half-batch schedule.
    # For a single request, handoff, prompt and decode execute in series.
    pre_batch = max(1, concurrency // 2)
    dec_batch = max(1, concurrency - pre_batch)
    pre_times = _stages(pre, (inputs, outputs, pre_batch, percent), notes, sources)
    dec_times = _stages(dec, (inputs, outputs, dec_batch, percent), notes, sources)
    # Full cache handoff for one prompt. Include rank replicas and per-request state.
    handoff = sum(
        row["bytes_per_gpu"] * (1 if row["unit"] == "request" else min(inputs, row.get("window") or inputs))
        for row in pre["components"]
    ) * (pre.get("tp") or 1)
    notes.add("pd_transfer")
    cases = {}
    for state, pre_case in pre_times.items():
        # A warm prefill does not imply prefix residency on the decode worker.
        # Charge the same full handoff for both cache states.
        dec_case = dec_times["cold"]
        rates = []
        for fast, (pf, generation) in enumerate(zip(pre_case.times, dec_case.times, strict=True)):
            # Planning scenarios for the pair's effective 100/400 Gbit/s fabric.
            link = (12.5, 50)[fast] * 1e9
            costs = (pf.prefill / pf.batch, handoff / link, generation.decode / generation.batch)
            seconds = max(costs) if fast and concurrency > 1 else sum(costs)
            rates.append(60 / seconds)
        batch = min(concurrency, pre_case.times[0].batch + dec_case.times[0].batch)
        cases[state] = _rates(rates, pre_case.reused_tokens, batch, inputs, outputs)
    return cases


def estimate_throughput(pools, scenario):
    """One aggregated replica, or one paired prefill/decode replica per stage."""
    result = {"version": VERSION, "method": "roofline", "scope": "replica", "status": "unavailable",
              "reason": None, "rpm": None, "input_tpm": None, "output_tpm": None,
              "assumptions": [], "sources": [], "effective_concurrency": None}
    try:
        workload = tuple(scenario[k] for k in ("input_tokens", "output_tokens", "concurrency", "shared_prefix_percent"))
        inputs, outputs, concurrency, percent = workload
        if any(type(x) is not int or x < 1 for x in (inputs, outputs, concurrency)) or not 0 <= percent <= 100:
            raise EstimateUnavailableError("invalid_scenario")
        # Physical memory errors apply to every workload and both disaggregated
        # roles, even if the other pool has an earlier context or layout error.
        for pool in pools:
            _check_memory(pool)
        notes = {"analytical"}
        if len(pools) == 1 and pools[0].get("role") in ("aggregated", None):
            stages = _stages(pools[0], workload, notes, result["sources"])
            cases = {}
            for state, case in stages.items():
                rates = [s.rpm for s in case.times]
                estimates = _rates(rates, case.reused_tokens, case.times[0].batch, inputs, outputs)
                estimates["phase_seconds"] = {
                    "prefill": sorted(s.prefill for s in case.times),
                    "decode": sorted(s.decode for s in case.times),
                }
                cases[state] = estimates
        else:
            result["scope"] = "pd_pair"
            cases = _pd_rates(pools, workload, notes, result["sources"])
        rpm = [min(case["rpm"][0] for case in cases.values()), max(case["rpm"][1] for case in cases.values())]
        batches = [cases["cold"]["effective_concurrency"],
                   cases.get("warm", cases["cold"])["effective_concurrency"]]
        result.update(status="estimated", rpm=rpm, input_tpm=[r * inputs for r in rpm],
                      output_tpm=[r * outputs for r in rpm], effective_concurrency=batches,
                      cache_scenarios=cases,
                      assumptions=sorted(notes),
                      efficiency={"compute": list(COMPUTE_EFFICIENCY), "memory": list(MEMORY_EFFICIENCY)},
                      timing_us={"layer_dispatch": list(LAYER_DISPATCH_US),
                                 "collective_per_tp_hop": list(COLLECTIVE_US_PER_TP_HOP),
                                 "moe_routing": list(MOE_ROUTING_US)})
        result["sources"].append({"label": "Roofline", "url": ROOFLINE})
    except speculative_throughput.DraftEvidenceError as exc:
        result.update(reason="draft_unknown", draft_method=exc.method, draft_missing=exc.missing)
    except EstimateUnavailableError as exc:
        result.update(status=exc.status, reason=exc.reason)
    except (ArithmeticError, ValueError, TypeError, KeyError, AttributeError):
        # Optional preview: malformed live metadata cannot interrupt the recipe form.
        result["reason"] = "invalid_recipe"
    return result
