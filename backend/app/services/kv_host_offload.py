"""Read configured CPU KV storage separately from the engine's active GPU pool."""

import json
import math
from decimal import Decimal, InvalidOperation


def _budget(source, scope, amount=None):
    return {"source": source, "scope": scope, "configured_bytes": amount}


def _bytes(value, unit=1):
    # A missing/invalid amount must not turn into a real zero in the UI.
    if isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or not 0 <= amount < 2**63:
            return None
        size = amount * unit
        if size >= 2**63 or size != int(size):
            return None
        return int(size)
    except (TypeError, ValueError, InvalidOperation):
        return None


def _transfer_config(flags):
    # vLLM's CLI builds a final JSON object from dotted options when present.
    dotted = [(key.split(".")[1:], value) for key, value in flags.items() if key.startswith("kv-transfer-config.")]
    if not dotted:
        return json.loads(flags.get("kv-transfer-config", "{}"))
    config = {}
    for parts, value in dotted:
        node = config
        for part in parts[:-1]:
            node = node.setdefault(part.replace("-", "_"), {})
        try:
            value = json.loads(value)
        except ValueError:
            pass  # Unquoted string values are normal for dotted CLI options.
        node[parts[-1].replace("-", "_")] = value
    return config


def _connector_budget(config):
    name = config.get("kv_connector")
    extra = config.get("kv_connector_extra_config") or {}
    if config.get("kv_connector_module_path") and name in ("SimpleCPUOffloadConnector", "OffloadingConnector"):
        return _budget(name, "unknown")
    if name == "MultiConnector":
        # A P/D transfer connector plus one CPU connector is unambiguous.
        # Multiple independent stores may contain copies of the same cache.
        budgets = [_connector_budget(child) for child in extra.get("connectors", [])]
        hosts = [budget for budget in budgets if budget is not None]
        if len(hosts) == 1:
            return hosts[0]
        return _budget("MultiConnector", "unknown") if hosts else None
    if name in ("LMCacheConnectorV1", "LMCacheMPConnector"):
        return _budget("LMCache", "external" if name == "LMCacheMPConnector" else "unknown")
    if name == "SimpleCPUOffloadConnector":
        if extra.get("kv_offload_backend", "cpu") != "cpu":
            return None
        if "cpu_bytes_to_use_per_rank" in extra:
            return _budget(name, "per_worker", _bytes(extra["cpu_bytes_to_use_per_rank"]))
    elif name == "OffloadingConnector":
        if extra.get("spec_name", "CPUOffloadingSpec") not in ("CPUOffloadingSpec", "TieringOffloadingSpec"):
            return _budget(name, "unknown")
    else:
        return None
    return _budget(name, "all_workers", _bytes(extra.get("cpu_bytes_to_use")))


def host_kv_offload(engine, flags, *, env=None):
    """Budget per engine worker group or per rank, never active admission capacity.

    The amount is the configured buffer, before allocator alignment and runtime
    restrictions. Host copies can overlap GPU-resident blocks and each other.
    """
    if engine == "sglang":
        if str(flags.get("enable-lmcache", "")).lower() in ("1", "true"):
            return _budget("LMCache", "unknown")
        if str(flags.get("enable-hierarchical-cache", "")).lower() not in ("1", "true"):
            return None
        size_gb = _bytes(flags.get("hicache-size", 0))
        size = _bytes(size_gb, 10**9)
        result = _budget("HiCache", "per_worker", size or None)
        if size == 0 and "hicache-ratio" in flags:
            try:
                ratio = float(flags["hicache-ratio"])
                if math.isfinite(ratio) and ratio > 0:
                    result["device_pool_ratio"] = ratio
            except (ValueError, TypeError):
                pass
        return result
    if engine != "vllm":
        return None
    try:
        config = _transfer_config(flags)
        if "kv-offloading-size" in flags:
            backend = flags.get("kv-offloading-backend", "native")
            if backend == "lmcache":
                # vLLM releases select either the in-process V1 connector
                # or the independently configured LMCache MP server.
                return _budget("LMCache", "unknown")
            if backend != "native":
                return _budget("kv-offloading-size", "unknown")
            # Native shorthand overrides the connector and total byte setting
            # while retaining extra options (including Simple's per-rank limit).
            extra = {
                **(config.get("kv_connector_extra_config") or {}),
                "cpu_bytes_to_use": _bytes(flags["kv-offloading-size"], 1 << 30),
            }
            selector = (env or {}).get("VLLM_USE_SIMPLE_KV_OFFLOAD", "0")
            try:
                simple = bool(int(selector))
            except (TypeError, ValueError):
                # envFrom/valueFrom may select either connector at startup.
                # Only report a number if both interpretations give the same budget.
                choices = [
                    _connector_budget({**config, "kv_connector": name, "kv_connector_extra_config": extra})
                    for name in ("SimpleCPUOffloadConnector", "OffloadingConnector")
                ]
                if all(choices) and all(choices[0][key] == choices[1][key] for key in ("configured_bytes", "scope")):
                    return {**choices[0], "source": "kv-offloading-size"}
                return _budget("kv-offloading-size", "unknown")
            config = {
                **config,
                "kv_connector": "SimpleCPUOffloadConnector" if simple else "OffloadingConnector",
                "kv_connector_extra_config": extra,
            }
        return _connector_budget(config)
    except (ValueError, TypeError, AttributeError, RecursionError):
        return _budget("kv-transfer-config", "unknown")
