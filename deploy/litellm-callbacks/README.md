# prefix_affinity — native LiteLLM router plugin (no fork)

Routes requests that share a cacheable prefix to the same provider-prompt-cache domain
(deployment) within a `model_group`, to maximize provider prompt-cache hits — while reusing
the Router's own health-check / cooldown filtering for load fallback.

Implemented as a **LiteLLM-native `CustomLogger` callback**, so there are **zero changes to
LiteLLM source** and it survives version upgrades untouched. It runs on the stock
`ghcr.io/berriai/litellm` image.

## Files
- `prefix_affinity_check.py` — the plugin: `compute_prefix_key`, `select_deployment_hrw`,
  `PrefixAffinityDeploymentCheck` (a `CustomLogger` overriding `async_filter_deployments`),
  plus the module-level instance `prefix_affinity_handler` the proxy loads.
- `test_prefix_affinity_check.py` — 32 unit tests (incl. one proving a callback registered in
  `litellm.callbacks` actually gets its `async_filter_deployments` invoked by the Router).

## How it's wired (already committed here)
- `docker-compose.yml` (litellm service): stock image, mounts the plugin at
  `/app/prefix_affinity_check.py`, sets `PREFIX_AFFINITY_*` env.
- `deploy/litellm-proxy/proxy_server_config.yaml`:
  - `litellm_settings.callbacks: ["prefix_affinity_check.prefix_affinity_handler"]`
  - `router_settings.enable_pre_call_checks: true` (already present)

The proxy config was moved here from inside the `litellm/` submodule so litellm version
switches never clobber deployment config.

## Mechanism
`async_filter_deployments` is a native override hook on `CustomLogger`
(`litellm/integrations/custom_logger.py`). During deployment selection the Router calls it for
every `CustomLogger` in `litellm.callbacks`, after its health-check / cooldown / blocked
filtering. The plugin narrows the healthy list to one deployment: provider/RPM candidate
filtering → sticky affinity-cache lookup (scoped per model group) → deterministic Rendezvous
(HRW) hashing of the prefix → records the placement on success with a provider-TTL, keyed by
the `prefix_key` stamped into request metadata at routing time (the logging payload's messages
are mutated — base64 truncation, system-prompt append, redaction — so a recomputed hash could
silently never match; recompute is kept only as a fallback).
It never raises (the call site re-raises; all error paths return the input list unchanged).

**RPM interplay:** the Router's `_pre_call_checks` (RPM/TPM + context-window filtering) runs
*after* this hook, so it cannot rescue an over-limit pin. The plugin therefore mirrors the
Router's RPM counters (via the proxy's global router, or a `router=` passed to the constructor)
and drops saturated candidates *before* pinning — a hot prefix spills to the next HRW pick
instead of erroring. Context-window and tag filtering still run downstream; pins that fail
those still error rather than spill.

## Config (env — all optional; compose sets sensible defaults)
| var | default | meaning |
|---|---|---|
| `PREFIX_AFFINITY_STRATEGY` | `leading_slice` (compose) / `cache_control` (code) | `cache_control` for Anthropic explicit breakpoints; `leading_slice` for OpenAI automatic caching |
| `PREFIX_AFFINITY_LEADING_SLICE` | 2 | messages counted as the stable prefix for `leading_slice` |
| `PREFIX_AFFINITY_MIN_TOKENS` | 1024 | prefix below this → no affinity; counted on the cacheable prefix (both strategies), memoized per prefix |
| `PREFIX_AFFINITY_TTL` | 600 (compose) / 300 (code) | affinity entry TTL; align to provider cache window |
| `PREFIX_AFFINITY_MODELS` | _(empty = all)_ | csv of model-group names to scope to (e.g. `gpt-4o,claude-3-5-sonnet`) |
| `PREFIX_AFFINITY_PROVIDERS` | _(empty = all)_ | csv of providers to scope to (e.g. `openai`) |

**Scoping:** by default the plugin runs for every `model_group` (no-op where it can't help).
`PREFIX_AFFINITY_MODELS` gates whole model groups; `PREFIX_AFFINITY_PROVIDERS` filters
per-deployment, so a mixed-provider group pins within the allowlisted providers only.
Out-of-scope requests pass through untouched. Both empty = apply to all.

## Apply / restart
```bash
docker compose up -d litellm
docker logs litellm_proxy 2>&1 | grep -i "callback\|prefix_affinity"   # confirm the callback loaded
```

## Verify
A `model_group` must span **different OpenAI orgs/accounts** (provider cache is org-scoped;
multiple keys in one org share a cache and gain nothing). Send two requests sharing a
>= 1024-token prefix and confirm they land on the same deployment.

## Observability — how to know it's working
- **Loaded?** On startup the proxy logs (info): `prefix_affinity callback loaded: strategy=..., min_tokens=..., ttl=..., models=..., providers=...`. A bad callbacks path / missing module errors at startup instead.
- **Per-decision** (set `LITELLM_LOG=DEBUG`): each request logs `prefix_affinity: <sticky|hrw|skip (reason)>, model=<group>, chosen=<id>, candidates=N` — so you see exactly when it applies vs passes through.
- **In spend logs / Langfuse:** every applied request is best-effort stamped with `metadata.prefix_affinity = {"decision": "...", "model_id": "..."}`. Confirm it reaches your logging sink on first run (the stamp path isn't guaranteed across all LiteLLM versions).
- **Black-box:** send two requests sharing a >= 1024-token prefix → both hit the same deployment (check spend logs / `litellm_deployment_*` metrics); with the plugin off they'd spread.

## Tests
```bash
cd litellm && uv run pytest ../deploy/litellm-callbacks/test_prefix_affinity_check.py -q   # 32 passed
```

## Note on the cache backend
The plugin uses an in-memory affinity cache by default. HRW is deterministic, so routing stays
consistent across proxy replicas even without shared state; the cache only adds sticky
"remember the spill target" memory. For shared sticky state across replicas, construct
`PrefixAffinityDeploymentCheck` with a Redis-backed `DualCache`.

## Supersedes the fork approach
This native plugin replaces the earlier fork-based implementation (LiteLLM source edits in
`Wonki4/litellm` branches `feat/prefix-affinity-router*`, PRs, and the thin-overlay image).
Those can be retired — no LiteLLM source changes are needed.
