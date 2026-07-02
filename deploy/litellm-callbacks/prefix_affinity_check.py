"""
Prefix-affinity deployment filter for the LiteLLM proxy — NATIVE plugin form.

Registered purely via config; this requires NO changes to LiteLLM source, so it
survives version upgrades untouched:

    litellm_settings:
      callbacks: ["prefix_affinity_check.prefix_affinity_handler"]
    router_settings:
      enable_pre_call_checks: true

Mechanism: `async_filter_deployments` is a native `CustomLogger` override hook
(litellm/integrations/custom_logger.py); the Router calls it for every CustomLogger
registered in `litellm.callbacks` during deployment selection, after its
health-check / cooldown / blocked filtering. We narrow the healthy list to one
deployment so that requests sharing a cacheable prefix land on the same
provider-prompt-cache domain.

RPM interplay: the Router's `_pre_call_checks` (RPM/TPM and context-window
filtering) runs AFTER this hook, so it cannot rescue a pinned deployment that is
over its limit. To keep hot prefixes spilling, the callback mirrors the Router's
RPM counters (injected `router=`, or the proxy's global router when available)
and drops saturated candidates BEFORE pinning — the spill target is the next HRW
pick. Context-window and tag filtering still run downstream; pins that fail
those error rather than spill.

Config via env (all optional):
  PREFIX_AFFINITY_STRATEGY        "cache_control" | "leading_slice"  (default cache_control)
  PREFIX_AFFINITY_LEADING_SLICE   int messages for leading_slice     (default 2)
  PREFIX_AFFINITY_MIN_TOKENS      int                                (default 1024)
  PREFIX_AFFINITY_TTL             int seconds                        (default 300)
  PREFIX_AFFINITY_MODELS          csv of model-group names; empty = all (scope allowlist)
  PREFIX_AFFINITY_PROVIDERS       csv of providers; empty = all         (scope allowlist)

This module imports only PUBLIC LiteLLM APIs, so it loads against the stock
ghcr.io/berriai/litellm image with no fork.
"""

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from typing import List, Optional, cast

from litellm import verbose_logger
from litellm.caching.dual_cache import DualCache
from litellm.integrations.custom_logger import CustomLogger, Span
from litellm.router_utils.prompt_caching_cache import PromptCachingCache
from litellm.types.llms.openai import AllMessageValues
from litellm.types.utils import CallTypes, StandardLoggingPayload
from litellm.utils import token_counter

DEFAULT_TTL_SECONDS = 300
DEFAULT_PREFIX_STRATEGY = "cache_control"
DEFAULT_LEADING_SLICE_MESSAGES = 2
DEFAULT_MIN_PREFIX_TOKENS = 1024


# Token-gate memo: counting tokens is the expensive part of this per-request
# path, and prefixes eligible for affinity repeat by definition.
_GATE_MEMO: dict = {}
_GATE_MEMO_MAX = 4096


def compute_prefix_key(
    messages: Optional[List[AllMessageValues]],
    model: str,
    config: dict,
) -> Optional[str]:
    """Return a stable sha256 hash of the cacheable prefix, or None if no affinity applies."""
    if not messages:
        return None

    strategy = config.get("prefix_strategy", DEFAULT_PREFIX_STRATEGY)

    if strategy == "cache_control":
        prefix = PromptCachingCache.extract_cacheable_prefix(messages)
    elif strategy == "leading_slice":
        n = config.get("leading_slice_messages", DEFAULT_LEADING_SLICE_MESSAGES)
        prefix = messages[:n]
    else:
        return None
    if not prefix:
        return None

    serialized = PromptCachingCache.serialize_object(prefix)
    data_to_hash = json.dumps(
        {"messages": serialized}, sort_keys=True, separators=(",", ":")
    )
    prefix_key = hashlib.sha256(data_to_hash.encode()).hexdigest()

    # Gate on the prefix (what the provider actually caches), not the whole
    # conversation; memoize per (model, prefix) so hot prefixes count once.
    memo_key = (model, prefix_key)
    gate = _GATE_MEMO.get(memo_key)
    if gate is None:
        min_tokens = config.get("min_prefix_tokens", DEFAULT_MIN_PREFIX_TOKENS)
        gate = token_counter(model=model, messages=prefix) >= min_tokens
        if len(_GATE_MEMO) >= _GATE_MEMO_MAX:
            _GATE_MEMO.clear()
        _GATE_MEMO[memo_key] = gate
    return prefix_key if gate else None


def select_deployment_hrw(
    prefix_key: str, healthy_deployments: List[dict]
) -> Optional[dict]:
    """Rendezvous (HRW) hashing: deterministically pick the deployment with the
    highest hash(prefix_key:id). Same key+set -> same pick; distinct keys spread
    evenly; removing a deployment remaps only the keys that mapped to it."""
    best: Optional[dict] = None
    best_score: Optional[int] = None
    for deployment in healthy_deployments:
        model_id = deployment.get("model_info", {}).get("id")
        if model_id is None:
            continue
        digest = hashlib.sha256(f"{prefix_key}:{model_id}".encode()).hexdigest()
        score = int(digest, 16)
        if best_score is None or score > best_score:
            best_score = score
            best = deployment
    return best


def _provider_of(deployment: dict) -> Optional[str]:
    info = deployment.get("model_info") or {}
    provider = info.get("litellm_provider")
    if provider:
        return provider
    model = (deployment.get("litellm_params") or {}).get("model", "")
    return model.split("/", 1)[0] if "/" in model else None


def _affinity_candidates(
    model: str, healthy_deployments: List[dict], config: dict
) -> List[dict]:
    """Deployments affinity may pin to, per the optional allowlists (empty = no
    restriction). `models` gates the whole group; `providers` filters
    per-deployment, so a mixed-provider group pins within the allowlisted ones."""
    models = config.get("models") or []
    if models and model not in models:
        return []
    providers = config.get("providers") or []
    if not providers:
        return list(healthy_deployments)
    return [d for d in healthy_deployments if _provider_of(d) in providers]


class PrefixAffinityDeploymentCheck(CustomLogger):
    """Native CustomLogger plugin: route a request to the deployment that already
    holds the provider prompt cache for its prefix; otherwise place it
    deterministically via HRW. Works on the Router's healthy-deployment list, so
    unhealthy / cooled-down deployments are already excluded; RPM saturation is
    checked here as well (mirroring the downstream `_pre_call_checks`) so a hot
    prefix spills to the next HRW pick instead of erroring (see module docstring)."""

    def __init__(
        self,
        cache: Optional[DualCache] = None,
        config: Optional[dict] = None,
        router=None,
    ):
        self.cache = cache or DualCache()
        self.config: dict = config or {}
        self._router = router

    def _get_router(self):
        """Router instance for RPM lookups: injected, else the proxy's global —
        read only if the proxy module is already loaded, never imported here."""
        if self._router is None:
            proxy_module = sys.modules.get("litellm.proxy.proxy_server")
            router = getattr(proxy_module, "llm_router", None) if proxy_module else None
            if router is not None:
                self._router = router
        return self._router

    def _drop_rpm_saturated(self, model: str, deployments: List[dict]) -> List[dict]:
        """Mirror the RPM check of Router._pre_call_checks, which runs AFTER this
        hook: pinning a deployment already at its rpm limit would become "no
        deployments available" downstream instead of spilling to the next HRW
        pick. Reads the Router's own local counters; fail-open throughout."""
        router = self._get_router()
        if router is None or getattr(router, "routing_strategy", None) == "usage-based-routing-v2":
            return deployments
        try:
            current_minute = datetime.now(timezone.utc).strftime("%H-%M")
            group_usage = router.cache.get_cache(
                key=f"{model}:rpm:{current_minute}", local_only=True
            )
            if not isinstance(group_usage, dict):
                group_usage = {}
            survivors = []
            for deployment in deployments:
                rpm_limit = (deployment.get("litellm_params") or {}).get("rpm")
                if isinstance(rpm_limit, int):
                    model_id = (deployment.get("model_info") or {}).get("id", "")
                    local = router.cache.get_cache(key=model_id, local_only=True) or 0
                    if rpm_limit <= max(local, group_usage.get(model_id, 0)):
                        continue
                survivors.append(deployment)
            return survivors
        except Exception as e:
            verbose_logger.debug(f"prefix_affinity: rpm check skipped ({e})")
            return deployments

    def _cache_key(self, model_group: str, prefix_key: str) -> str:
        # Scoped by model group: the prefix hash covers messages only, and two
        # groups sharing a prompt must not overwrite each other's sticky entry.
        return f"deployment:{model_group}:{prefix_key}:prefix_affinity"

    def _note(self, request_kwargs, decision, model, model_id, candidates, prefix_key):
        """Observability: debug-log the decision and best-effort stamp it into
        request metadata (spend logs / Langfuse). The stamped prefix_key also
        lets the success handler write the sticky entry without re-deriving the
        hash from the logging payload's (mutated) messages."""
        verbose_logger.debug(
            "prefix_affinity: %s, model=%s, chosen=%s, candidates=%d",
            decision,
            model,
            model_id,
            candidates,
        )
        if isinstance(request_kwargs, dict):
            # New-style endpoints (e.g. /v1/messages) carry litellm metadata under
            # "litellm_metadata"; there "metadata" belongs to the provider API.
            md_var = "litellm_metadata" if "litellm_metadata" in request_kwargs else "metadata"
            md = request_kwargs.setdefault(md_var, {})
            if isinstance(md, dict):
                md["prefix_affinity"] = {
                    "decision": decision,
                    "model_id": model_id,
                    "prefix_key": prefix_key,
                }

    @staticmethod
    def _stamped_prefix_key(kwargs: dict) -> Optional[str]:
        litellm_params = kwargs.get("litellm_params") or {}
        for md in (
            litellm_params.get("metadata"),
            litellm_params.get("litellm_metadata"),
            kwargs.get("metadata"),
            kwargs.get("litellm_metadata"),
        ):
            if isinstance(md, dict):
                stamp = md.get("prefix_affinity")
                if isinstance(stamp, dict) and stamp.get("prefix_key"):
                    return stamp["prefix_key"]
        return None

    async def async_filter_deployments(
        self,
        model: str,
        healthy_deployments: List,
        messages: Optional[List[AllMessageValues]],
        request_kwargs: Optional[dict] = None,
        parent_otel_span: Optional[Span] = None,
    ) -> List[dict]:
        try:
            if messages is None or len(healthy_deployments) <= 1:
                return healthy_deployments

            candidates = _affinity_candidates(model, healthy_deployments, self.config)
            if not candidates:
                verbose_logger.debug("prefix_affinity: skip (out of scope), model=%s", model)
                return healthy_deployments

            prefix_key = compute_prefix_key(messages, model, self.config)
            if prefix_key is None:
                verbose_logger.debug("prefix_affinity: skip (no cacheable prefix), model=%s", model)
                return healthy_deployments

            candidates = self._drop_rpm_saturated(model, candidates)
            if not candidates:
                verbose_logger.debug("prefix_affinity: skip (all candidates over rpm), model=%s", model)
                return healthy_deployments

            cached = await self.cache.async_get_cache(key=self._cache_key(model, prefix_key))
            if isinstance(cached, dict):
                model_id = cached.get("model_id")
                if model_id is not None:
                    for deployment in candidates:
                        if (deployment.get("model_info") or {}).get("id") == model_id:
                            self._note(request_kwargs, "sticky", model, model_id, len(candidates), prefix_key)
                            return [deployment]

            chosen = select_deployment_hrw(prefix_key, candidates)
            if chosen is not None:
                self._note(
                    request_kwargs, "hrw", model, chosen["model_info"]["id"], len(candidates), prefix_key
                )
                return [chosen]
            return healthy_deployments
        except Exception as e:
            verbose_logger.debug(f"PrefixAffinityDeploymentCheck.filter error: {e}")
            return healthy_deployments

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        try:
            standard_logging_object: Optional[StandardLoggingPayload] = kwargs.get(
                "standard_logging_object", None
            )
            if standard_logging_object is None:
                return

            call_type = standard_logging_object["call_type"]
            if call_type not in (
                CallTypes.completion.value,
                CallTypes.acompletion.value,
                CallTypes.anthropic_messages.value,
            ):
                return

            model = standard_logging_object["model"]
            model_group = standard_logging_object.get("model_group") or model
            model_id = standard_logging_object["model_id"]
            if model_id is None:
                return

            # Prefer the prefix_key stamped at routing time: the payload's
            # messages are not the request messages (base64 truncation,
            # system-prompt append, redaction), so a recomputed hash can
            # silently never match the filter-time key.
            prefix_key = self._stamped_prefix_key(kwargs)
            if prefix_key is None:
                messages = standard_logging_object["messages"]
                if not isinstance(messages, list):
                    return
                prefix_key = compute_prefix_key(
                    cast(List[AllMessageValues], messages), model, self.config
                )
            if prefix_key is None:
                return

            ttl = self.config.get("ttl_seconds", DEFAULT_TTL_SECONDS)
            await self.cache.async_set_cache(
                self._cache_key(model_group, prefix_key), {"model_id": model_id}, ttl=ttl
            )
        except Exception as e:
            verbose_logger.debug(f"PrefixAffinityDeploymentCheck.log error: {e}")
        return


def _config_from_env() -> dict:
    return {
        "prefix_strategy": os.getenv("PREFIX_AFFINITY_STRATEGY", DEFAULT_PREFIX_STRATEGY),
        "leading_slice_messages": int(
            os.getenv("PREFIX_AFFINITY_LEADING_SLICE", str(DEFAULT_LEADING_SLICE_MESSAGES))
        ),
        "min_prefix_tokens": int(
            os.getenv("PREFIX_AFFINITY_MIN_TOKENS", str(DEFAULT_MIN_PREFIX_TOKENS))
        ),
        "ttl_seconds": int(os.getenv("PREFIX_AFFINITY_TTL", str(DEFAULT_TTL_SECONDS))),
        # Optional scoping (empty = apply to all): model-group names / providers.
        "models": [s.strip() for s in os.getenv("PREFIX_AFFINITY_MODELS", "").split(",") if s.strip()],
        "providers": [s.strip() for s in os.getenv("PREFIX_AFFINITY_PROVIDERS", "").split(",") if s.strip()],
    }


# The instance the proxy loads via `litellm_settings.callbacks`.
# Uses in-memory affinity cache; HRW keeps routing deterministic across replicas
# even without shared state. For shared spill-memory, pass a Redis-backed DualCache.
_cfg = _config_from_env()
prefix_affinity_handler = PrefixAffinityDeploymentCheck(config=_cfg)
verbose_logger.info(
    "prefix_affinity callback loaded: strategy=%s, min_tokens=%s, ttl=%s, models=%s, providers=%s",
    _cfg.get("prefix_strategy"),
    _cfg.get("min_prefix_tokens"),
    _cfg.get("ttl_seconds"),
    _cfg.get("models") or "ALL",
    _cfg.get("providers") or "ALL",
)
