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

CAVEAT — RPM saturation: the Router's `_pre_call_checks` (RPM/TPM and
context-window filtering) runs AFTER this hook. Once a prefix is pinned to a
single deployment, a pin over its RPM limit yields "no deployments available"
for the rest of the minute window instead of spilling to another deployment.
Scope hot prefixes via PREFIX_AFFINITY_MODELS / PREFIX_AFFINITY_PROVIDERS and
watch the stamped `metadata.prefix_affinity` decisions if this bites.

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
from typing import List, Optional, cast

from litellm import verbose_logger
from litellm.caching.dual_cache import DualCache
from litellm.integrations.custom_logger import CustomLogger, Span
from litellm.router_utils.prompt_caching_cache import PromptCachingCache
from litellm.types.llms.openai import AllMessageValues
from litellm.types.utils import CallTypes, StandardLoggingPayload
from litellm.utils import is_prompt_caching_valid_prompt, token_counter

DEFAULT_TTL_SECONDS = 300
DEFAULT_PREFIX_STRATEGY = "cache_control"
DEFAULT_LEADING_SLICE_MESSAGES = 2
DEFAULT_MIN_PREFIX_TOKENS = 1024


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
        if not prefix:
            return None
        if not is_prompt_caching_valid_prompt(model=model, messages=messages):
            return None
    elif strategy == "leading_slice":
        n = config.get("leading_slice_messages", DEFAULT_LEADING_SLICE_MESSAGES)
        prefix = messages[:n]
        if not prefix:
            return None
        min_tokens = config.get("min_prefix_tokens", DEFAULT_MIN_PREFIX_TOKENS)
        if token_counter(model=model, messages=prefix) < min_tokens:
            return None
    else:
        return None

    serialized = PromptCachingCache.serialize_object(prefix)
    data_to_hash = json.dumps(
        {"messages": serialized}, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(data_to_hash.encode()).hexdigest()


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


def _in_scope(model: str, healthy_deployments: List[dict], config: dict) -> bool:
    """Respect optional model-group / provider allowlists. Empty list = no restriction."""
    models = config.get("models") or []
    if models and model not in models:
        return False
    providers = config.get("providers") or []
    if providers:
        present = {p for p in (_provider_of(d) for d in healthy_deployments) if p}
        if present.isdisjoint(providers):
            return False
    return True


class PrefixAffinityDeploymentCheck(CustomLogger):
    """Native CustomLogger plugin: route a request to the deployment that already
    holds the provider prompt cache for its prefix; otherwise place it
    deterministically via HRW. Works on the Router's healthy-deployment list, so
    unhealthy / cooled-down deployments are already excluded — but RPM/TPM limits
    are enforced downstream (`_pre_call_checks`), so an over-limit pin fails the
    request rather than spilling to another deployment (see module docstring)."""

    def __init__(self, cache: Optional[DualCache] = None, config: Optional[dict] = None):
        self.cache = cache or DualCache()
        self.config: dict = config or {}

    def _cache_key(self, model_group: str, prefix_key: str) -> str:
        # Scoped by model group: the prefix hash covers messages only, and two
        # groups sharing a prompt must not overwrite each other's sticky entry.
        return f"deployment:{model_group}:{prefix_key}:prefix_affinity"

    def _note(self, request_kwargs, decision, model, model_id, candidates):
        """Observability: debug-log the decision and best-effort stamp it into
        request metadata so it can surface in spend logs / Langfuse."""
        verbose_logger.debug(
            "prefix_affinity: %s, model=%s, chosen=%s, candidates=%d",
            decision,
            model,
            model_id,
            candidates,
        )
        if isinstance(request_kwargs, dict):
            md = request_kwargs.setdefault("metadata", {})
            if isinstance(md, dict):
                md["prefix_affinity"] = {"decision": decision, "model_id": model_id}

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

            if not _in_scope(model, healthy_deployments, self.config):
                verbose_logger.debug("prefix_affinity: skip (out of scope), model=%s", model)
                return healthy_deployments

            prefix_key = compute_prefix_key(messages, model, self.config)
            if prefix_key is None:
                verbose_logger.debug("prefix_affinity: skip (no cacheable prefix), model=%s", model)
                return healthy_deployments

            cached = await self.cache.async_get_cache(key=self._cache_key(model, prefix_key))
            if isinstance(cached, dict):
                model_id = cached.get("model_id")
                if model_id is not None:
                    for deployment in healthy_deployments:
                        if (deployment.get("model_info") or {}).get("id") == model_id:
                            self._note(request_kwargs, "sticky", model, model_id, len(healthy_deployments))
                            return [deployment]

            chosen = select_deployment_hrw(prefix_key, healthy_deployments)
            if chosen is not None:
                self._note(
                    request_kwargs, "hrw", model, chosen["model_info"]["id"], len(healthy_deployments)
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
            messages = standard_logging_object["messages"]
            model_id = standard_logging_object["model_id"]
            if not isinstance(messages, list) or model_id is None:
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
