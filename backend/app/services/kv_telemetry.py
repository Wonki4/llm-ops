"""Extract engine-reported capacity without persisting log lines or metric labels."""

import math
import re
from collections import defaultdict

from prometheus_client.parser import text_string_to_metric_families

_GIB = 1 << 30
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_TOKEN_LINE = re.compile(r"GPU KV cache size:\s*([\d,]+)\s+tokens\b", re.I)
_BUDGET_LINE = re.compile(r"Available KV cache memory:\s*([\d.]+)\s+GiB\b", re.I)
_SGLANG_TOKENS = re.compile(r"\bmax_total_num_tokens=(\d+),\s*chunked_prefill_size=")
_SGLANG_BUDGET = re.compile(
    r"KV Cache is allocated\.[^\n]*?(?:K size: ([\d.]+) GB, V size: ([\d.]+) GB|KV size: ([\d.]+) GB)", re.I
)


def _number(value):
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return result if math.isfinite(result) and 0 <= result < 2**63 else None


def _integers(values, multiplier=1):
    numbers = [_number(v) for v in values]
    return [int(v * multiplier) for v in numbers if v is not None and v * multiplier < 2**63]


def _reported(tokens, budgets, source):
    result = {}
    for key, values in (("capacity_tokens", tokens), ("budget_bytes", budgets)):
        if values:
            # A rank may repeat the entire logical capacity. Never sum ranks.
            result.update({key: min(values), key + "_max": max(values), key + "_source": source})
    return result


def log_sample(engine, text):
    lines = _ANSI.sub("", text or "")
    if engine == "sglang":
        budgets = []
        for k, v, kv in _SGLANG_BUDGET.findall(lines):
            parsed = [_number(n) for n in ([kv] if kv else [k, v])]
            if all(n is not None for n in parsed):
                budgets.append(sum(parsed))
        return _reported(_integers(_SGLANG_TOKENS.findall(lines)), _integers(budgets, _GIB), "startup_log")
    tokens = [s.replace(",", "") for s in _TOKEN_LINE.findall(lines)]
    return _reported(_integers(tokens), _integers(_BUDGET_LINE.findall(lines), _GIB), "startup_log")


def metric_sample(engine, text):
    prefix = f"{engine}:"
    # Filter before parsing; other histogram/label families can be very large.
    names = {
        "vllm:cache_config_info",
        "vllm:kv_cache_size_tokens",
        "vllm:kv_cache_usage_perc",
        "vllm:gpu_cache_usage_perc",
        "vllm:prefix_cache_queries_total",
        "vllm:prefix_cache_hits_total",
        "sglang:max_total_num_tokens",
        "sglang:kv_cache_memory_usage_gb",
        "sglang:token_usage",
        "sglang:cache_hit_rate",
    }
    lines = [
        line
        for line in text.splitlines()
        if line.startswith(prefix) and line.split("{", 1)[0].split(" ", 1)[0] in names
    ]
    values = defaultdict(dict)
    tokens, budgets = [], []
    for family in text_string_to_metric_families("\n".join(lines)):
        for sample in family.samples:
            value = _number(sample.value)
            if value is None:
                continue
            values[sample.name][tuple(sorted(sample.labels.items()))] = value
            if sample.name in {"vllm:kv_cache_size_tokens", "sglang:max_total_num_tokens"}:
                tokens.extend(_integers([value]))
            elif sample.name == "vllm:cache_config_info":
                tokens.extend(_integers([sample.labels.get("kv_cache_size_tokens")]))
                budgets.extend(_integers([sample.labels.get("kv_cache_memory_bytes")]))
            elif sample.name == "sglang:kv_cache_memory_usage_gb":
                budgets.extend(_integers([value], _GIB))
    result = _reported(tokens, budgets, "engine_metrics")
    usage = values["sglang:token_usage"] or values["vllm:kv_cache_usage_perc"] or values["vllm:gpu_cache_usage_perc"]
    if usage and max(usage.values()) <= 1:
        result["utilization"] = max(usage.values())  # busiest engine
    queries, hits = values["vllm:prefix_cache_queries_total"], values["vllm:prefix_cache_hits_total"]
    if queries and hits.keys() == queries.keys() and all(hits[k] <= v for k, v in queries.items()):
        total = sum(queries.values())
        if total:
            result["prefix_hit_rate"] = sum(hits.values()) / total
            result["prefix_rate_scope"] = "since_startup"
    ratios = set(values["sglang:cache_hit_rate"].values())
    if len(ratios) == 1 and max(ratios) <= 1:
        result["prefix_hit_rate"] = ratios.pop()
        result["prefix_rate_scope"] = "engine_window"
    return result


def recent_log_rates(engine, text):
    if engine != "vllm":
        return {}
    result = {}
    for line in reversed(_ANSI.sub("", text or "").splitlines()):
        match = re.search(r"GPU KV cache usage:\s*([\d.]+)%.*?Prefix cache hit rate:\s*([\d.]+)%", line, re.I)
        if match:
            for key, raw in zip(("utilization", "prefix_hit_rate"), match.groups()):
                value = _number(raw)
                if value is not None and value <= 100:
                    result[key] = value / 100
            if "prefix_hit_rate" in result:
                result["prefix_rate_scope"] = "engine_window"
            break
    return result
