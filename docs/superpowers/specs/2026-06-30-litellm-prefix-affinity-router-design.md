# LiteLLM Prefix-Affinity Router — Design

**Date:** 2026-06-30
**Status:** Approved (brainstorming) — ready for implementation plan
**Repo:** LiteLLM fork (`litellm/` submodule, v1.87.0). Design/spec tracked in the parent `litellm-ops` repo.

## Goal

Add a custom routing behavior to the LiteLLM Router so that, within a `model_group` whose
deployments are **distinct provider-prompt-cache domains** (different Anthropic orgs / OpenAI
projects / API keys), requests sharing a cacheable prefix are routed to the **same deployment** —
maximizing provider prompt-cache hit rate (cache-read discount instead of full input price) —
while still balancing load and respecting rate limits.

## Background — what already exists, and the gap

LiteLLM v1.87.0 already ships three cache-relevant routing mechanisms, all implemented as
optional pre-call **deployment filters** (`CustomLogger.async_filter_deployments`), enabled via
`router_settings.optional_pre_call_checks`:

- **`session_affinity`** (`DeploymentAffinityCheck`) — sticky by `session_id`; good for a single
  multi-turn conversation, but blind to a static prefix shared across different sessions/users.
- **`prompt_caching`** (`PromptCachingDeploymentCheck`,
  `litellm/router_utils/pre_call_checks/prompt_caching_deployment_check.py`) — hashes the
  `cache_control: ephemeral`-marked prefix (`PromptCachingCache.extract_cacheable_prefix`) and, if a
  prior request with the **exact** same marked prefix was served by a healthy deployment, forces the
  request to that deployment. Records `prefix → model_id` on success.
- **`deployment_affinity`** — sticky by user/key.

**Confirmed gaps** in `prompt_caching` for this use case (all four must be solved):

1. **No load / rate-limit spreading.** It forces *all* matching traffic onto the single deployment
   that cached the prefix → that account hits its rate limit while peers sit idle.
2. **Marker-only.** `extract_cacheable_prefix` returns `[]` when there is no `cache_control` marker,
   so OpenAI/Bedrock **automatic** prefix caching gets no affinity at all.
3. **No deterministic first-touch placement.** Before any cache exists, requests for the same prefix
   scatter across deployments via the default strategy, so the cache never lands in a predictable place.
4. **No TTL alignment.** The affinity entry's lifetime is not tied to the provider cache TTL
   (Anthropic 5 min default / 1 h extended), so it can outlive or under-live the real cache.

### Cache-domain constraint (why this matters)

Provider prompt caches are scoped to the **account/organization**. If every deployment in the group
shares one API key, any deployment hits the same cache and affinity routing is pointless. This
design assumes the confirmed topology: **deployments differ by API key / account / org**, so each is
a separate cache domain and prefix→deployment affinity is what produces cache hits.

## Approach (chosen: A — pre-call filter)

Implement a new pre-call deployment filter — a `CustomLogger` with `async_filter_deployments` +
`async_log_success_event` — mirroring the built-in `PromptCachingDeploymentCheck` exactly. This
**reuses LiteLLM's existing health / rate-limit / cooldown filtering** (the filter only ever sees the
already-healthy candidate list), so load fallback (#1) comes almost for free.

Rejected alternatives:
- **B. Full `CustomRoutingStrategyBase`** (`router.set_custom_routing_strategy`) — replaces
  `async_get_available_deployment` wholesale, forcing us to re-implement health/rate-limit/cooldown.
  Higher risk and duplication; rejected.
- **C. Block-level prefix radix tree (SGLang/llm-d style)** — per-deployment token-block tree with
  longest-match routing. Best hit rate for partially-overlapping prefixes, but stateful and
  memory-heavy. Out of scope for v1.

## Architecture

### New component

`litellm/router_utils/pre_call_checks/prefix_affinity_check.py`

```python
class PrefixAffinityDeploymentCheck(CustomLogger):
    def __init__(self, cache: DualCache, config: "PrefixAffinityConfig"): ...
    async def async_filter_deployments(self, model, healthy_deployments, messages,
                                       request_kwargs=None, parent_otel_span=None) -> List[dict]: ...
    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time): ...
```

A small wrapper `PrefixAffinityCache` (or reuse `PromptCachingCache`) owns the
`prefix_key → {"model_id": ...}` cache read/write with TTL.

### Config type

`PrefixAffinityConfig` (TypedDict/pydantic) in `litellm/types/router.py`:

```python
class PrefixAffinityConfig(TypedDict, total=False):
    ttl_seconds: int            # default 300 (Anthropic 5-min cache). Aligns affinity to provider TTL.
    prefix_strategy: str        # "cache_control" (default) | "leading_slice"
    leading_slice_messages: int # default 2 (system + first user); used by "leading_slice"
    min_prefix_tokens: int      # default 1024; below this, no affinity (let default strategy balance)
    hash: str                   # default "sha256"
```

### Integration points (exact, fork v1.87.0)

1. **`litellm/types/router.py:763`** — add `"prefix_affinity"` to the `OptionalPreCallChecks`
   `List[Literal[...]]`.
2. **`litellm/types/router.py`** — add the `PrefixAffinityConfig` type.
3. **`litellm/router.py:320`** (`Router.__init__`) — add
   `prefix_affinity_config: Optional[PrefixAffinityConfig] = None`; store as
   `self.prefix_affinity_config` (mirror `deployment_affinity_ttl_seconds` / `model_group_affinity_config`).
4. **`litellm/router.py:1744`** (`add_optional_pre_call_checks` loop) — add a branch:
   `elif pre_call_check == "prefix_affinity": _callback = PrefixAffinityDeploymentCheck(cache=self.cache, config=self.prefix_affinity_config or {})`.
5. **Proxy wiring** — none needed beyond the above: the proxy constructs `Router(**router_settings)`,
   so `optional_pre_call_checks` and `prefix_affinity_config` flow straight from `config.yaml`.
   *(Verify the `Router(**router_settings)` pass-through during planning.)*

The filter is invoked by `Router.async_callback_filter_deployments`
(`litellm/router.py:7646`), which chains each callback's `async_filter_deployments` over the
healthy-deployment list. **Exceptions there are re-raised**, so the filter must never raise (see Error
Handling).

## The routing algorithm (`async_filter_deployments`)

Input `healthy_deployments` is already health/rate-limit/cooldown filtered by the Router.

1. If `len(healthy_deployments) <= 1` → return it unchanged.
2. If `messages` is `None` (e.g. embeddings) → return unchanged.
3. **Compute `prefix_key`:**
   - `prefix_strategy == "cache_control"` (default): `prefix = PromptCachingCache.extract_cacheable_prefix(messages)`.
     If empty → no marker → return unchanged.
   - `prefix_strategy == "leading_slice"`: `prefix = messages[:leading_slice_messages]`.
   - Below `min_prefix_tokens` → return unchanged (tiny prompts gain nothing from caching; let the
     default strategy balance). Measure with the existing helper: for `cache_control`, reuse
     `is_prompt_caching_valid_prompt(messages, model)` (it already gates provider support + >1024
     tokens); for `leading_slice`, count with `litellm.token_counter(model, messages=prefix)`.
   - `prefix_key = hash(serialize(prefix))`.
4. **Affinity lookup (sticky):** `model_id = cache.get(prefix_key)`. If present **and** a deployment
   with that `model_info["id"]` is in `healthy_deployments` → return `[that deployment]`.
   *(Requirement #2 stickiness; TTL on the entry gives #4. If that account is saturated it is absent
   from `healthy_deployments`, so we fall through to HRW — that is requirement #1's spill.)*
5. **HRW (rendezvous) selection:** among `healthy_deployments`, pick
   `argmax_d  hash(f"{prefix_key}:{d['model_info']['id']}")`; return `[d]`.
   *(Deterministic first-touch placement — same prefix → same account even before any cache exists —
   and distinct prefixes spread evenly. Requirement #2 + natural load distribution. Do **not** write
   the cache here; HRW is deterministic so repeated pre-call lookups pick the same `d` anyway, and we
   only confirm placement on success.)*
6. Any internal exception → return `healthy_deployments` unchanged.

**`async_log_success_event`:** for `completion` / `acompletion` / `anthropic_messages` call types
only, recompute `prefix_key` from `standard_logging_object["messages"]` and write
`prefix_key → {"model_id": standard_logging_object["model_id"]}` with **TTL = `config.ttl_seconds`**
(requirement #4). Skip when messages/model_id missing or prefix below threshold. Mirrors
`PromptCachingDeploymentCheck.async_log_success_event`.

### Why HRW gives stable multi-turn affinity

With `prefix_strategy == "cache_control"`, the cache breakpoint is normally placed on the **static**
portion (system + tools + RAG), so `prefix_key` is stable across turns of a conversation → HRW picks
the same deployment every turn. With `leading_slice`, the leading slice (system + first user) is
likewise stable across turns. So affinity holds across turns **without** needing `session_id`.

## Configuration (proxy `config.yaml`)

```yaml
router_settings:
  optional_pre_call_checks: ["prefix_affinity"]   # use INSTEAD OF "prompt_caching" — not both
  prefix_affinity_config:
    ttl_seconds: 300                 # Anthropic 5-min cache (use 3600 for 1-h extended caching)
    prefix_strategy: "cache_control" # or "leading_slice" for OpenAI/Bedrock automatic caching
    leading_slice_messages: 2
    min_prefix_tokens: 1024
```

`prefix_affinity` and the built-in `prompt_caching` are **mutually exclusive** — enabling both chains
two filters that both narrow to a single (possibly different) deployment. Document this; optionally
log a warning if both are present.

## Error handling

- The filter **never raises** (the call site re-raises — `router.py:7679`). Wrap the body in
  try/except and return the input list on any error.
- `messages is None`, empty prefix, missing `model_info["id"]`, cache backend error → return input
  unchanged. Routing degrades gracefully to the default strategy; no request is ever dropped.

## Observability

- `verbose_logger.debug` on each decision: `prefix_key` (truncated), decision
  (`sticky` | `hrw` | `skip`), chosen `model_id`, candidate count.
- Stamp `request_kwargs["metadata"]["prefix_affinity"] = {"key": ..., "decision": ..., "model_id": ...}`
  so it lands in the standard logging payload for hit-rate analysis.

## Testing (`tests/test_litellm/router_utils/` in the fork)

- **prefix key:** `cache_control` extracts the marked prefix; no marker → skip (returns input);
  `leading_slice` takes the first N messages; below `min_prefix_tokens` → skip.
- **HRW:** deterministic (same key + same deployment set → same pick); distribution across many keys
  is roughly even; removing the chosen deployment remaps only that key (≈1/N churn).
- **stickiness:** after a success writes `key → model_id`, the next request returns `[that deployment]`;
  if that `model_id` is **absent** from `healthy_deployments` (saturated), it falls back to HRW among
  the rest (requirement #1 spill).
- **TTL:** the success write uses `config.ttl_seconds`.
- **defensive:** an internal exception returns `healthy_deployments` unchanged (no raise);
  `messages=None` returns unchanged.
- **registration/integration:** `Router(optional_pre_call_checks=["prefix_affinity"], prefix_affinity_config={...})`
  installs the callback; a `Router(**router_settings)` round-trip from a config-like dict wires it.
- Run `make test-unit` and `uv run black .` before committing (fork CI requirement).

## Out of scope (v1)

- Block-level prefix radix tree (Approach C).
- Per-`model_group` overrides of `prefix_affinity_config` (extend later, mirroring
  `model_group_affinity_config`).
- Portal UI to toggle/configure the check per model group.
- Auto-injecting `cache_control` breakpoints (the caller still marks them, as today).

## Open items to confirm during planning

- Confirm the proxy builds the Router via `Router(**router_settings)` so `prefix_affinity_config`
  passes through from `config.yaml` with no extra proxy code.
- Confirm the cache backend (`self.cache`, `DualCache`) `get`/`set` async API and TTL parameter name
  used by `PromptCachingCache`, and reuse it.
