# LiteLLM Prefix-Affinity Router Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a LiteLLM Router pre-call filter that routes requests sharing a cacheable prefix to the same provider-prompt-cache domain (deployment), maximizing provider prompt-cache hit rate while reusing the Router's existing health/rate-limit filtering for load fallback.

**Architecture:** A new `PrefixAffinityDeploymentCheck(CustomLogger)` implements `async_filter_deployments` (narrows the already-healthy candidate list to one deployment) + `async_log_success_event` (records the placement). Selection = sticky affinity-cache lookup, falling back to deterministic Rendezvous (HRW) hashing of the prefix over the healthy deployments. Enabled via `router_settings.optional_pre_call_checks: ["prefix_affinity"]`, configured via a new `prefix_affinity_config` Router param. Mirrors the existing built-in `PromptCachingDeploymentCheck` exactly.

**Tech Stack:** Python 3.11, LiteLLM fork v1.87.0 (`litellm/` submodule), pytest, Black, MyPy/Ruff.

**Spec:** `docs/superpowers/specs/2026-06-30-litellm-prefix-affinity-router-design.md`

## Global Constraints

- All code changes happen **inside the `litellm/` submodule** (`/Users/wongibaek/Documents/litellm-ops/litellm`), a fork at v1.87.0. Run all commands from that directory. Commit on a branch in the submodule (`git checkout -b feat/prefix-affinity-router` there).
- **Imports at module top only** — no inline imports inside functions/methods (fork CLAUDE.md rule).
- **The filter must NEVER raise.** Its call site `Router.async_callback_filter_deployments` (`litellm/router.py:7646`) re-raises any exception from `async_filter_deployments`. Wrap the body in try/except and return the input list on any error.
- **DRY:** reuse `PromptCachingCache.extract_cacheable_prefix` and `PromptCachingCache.serialize_object` (`litellm/router_utils/prompt_caching_cache.py`) — do not reimplement prefix extraction or serialization.
- **Defaults (verbatim):** `ttl_seconds=300`, `prefix_strategy="cache_control"`, `leading_slice_messages=2`, `min_prefix_tokens=1024`.
- `prefix_affinity` and the built-in `prompt_caching` are **mutually exclusive** (documented; both narrow to a single deployment and would conflict if chained).
- **Before each commit:** run `uv run black .` and the task's tests with `uv run pytest`.
- Cache API to use: `await cache.async_set_cache(key, value, ttl=<int>)` and `await cache.async_get_cache(key=<str>)` (returns the stored value or `None`).
- Deployment dict shape: a deployment's id is `deployment["model_info"]["id"]`.

---

### Task 1: Pure routing logic — prefix key + HRW selection

**Files:**
- Modify: `litellm/types/router.py` (add `PrefixAffinityConfig` TypedDict after the `OptionalPreCallChecks` literal block at L763-775)
- Create: `litellm/router_utils/pre_call_checks/prefix_affinity_check.py` (module-level helpers only this task)
- Test: `tests/test_litellm/router_utils/pre_call_checks/test_prefix_affinity_check.py`

**Interfaces:**
- Produces:
  - `PrefixAffinityConfig` (TypedDict, total=False): keys `ttl_seconds:int`, `prefix_strategy:str`, `leading_slice_messages:int`, `min_prefix_tokens:int`
  - `compute_prefix_key(messages: Optional[List[AllMessageValues]], model: str, config: PrefixAffinityConfig) -> Optional[str]`
  - `select_deployment_hrw(prefix_key: str, healthy_deployments: List[dict]) -> Optional[dict]`
  - module constants `DEFAULT_TTL_SECONDS=300`, `DEFAULT_PREFIX_STRATEGY="cache_control"`, `DEFAULT_LEADING_SLICE_MESSAGES=2`, `DEFAULT_MIN_PREFIX_TOKENS=1024`

- [ ] **Step 1: Create the submodule branch**

```bash
cd /Users/wongibaek/Documents/litellm-ops/litellm
git checkout -b feat/prefix-affinity-router
```

- [ ] **Step 2: Add the `PrefixAffinityConfig` type**

In `litellm/types/router.py`, immediately after the `OptionalPreCallChecks = List[Literal[...]]` block (ends at L775), add (`TypedDict` is already imported in this file):

```python
class PrefixAffinityConfig(TypedDict, total=False):
    """Config for the prefix-affinity deployment filter (see prefix_affinity_check.py)."""

    ttl_seconds: int  # affinity entry TTL; align to provider prompt-cache TTL (default 300 = Anthropic 5 min)
    prefix_strategy: str  # "cache_control" (default) | "leading_slice"
    leading_slice_messages: int  # messages counted as the stable prefix for "leading_slice" (default 2)
    min_prefix_tokens: int  # below this, no affinity is applied (default 1024)
```

- [ ] **Step 3: Write the failing test**

Create `tests/test_litellm/router_utils/pre_call_checks/test_prefix_affinity_check.py`:

```python
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath("../.."))

from litellm.router_utils.pre_call_checks.prefix_affinity_check import (
    compute_prefix_key,
    select_deployment_hrw,
)

CACHE_CONTROL_MESSAGES = [
    {
        "role": "system",
        "content": [
            {
                "type": "text",
                "text": "You are a helpful assistant with a large static system prompt.",
                "cache_control": {"type": "ephemeral"},
            }
        ],
    },
    {"role": "user", "content": "What is the capital of France?"},
]


def _deployment(model_id: str) -> dict:
    return {
        "model_name": "gpt",
        "litellm_params": {"model": "openai/gpt-4o"},
        "model_info": {"id": model_id, "db_model": True},
    }


def test_compute_prefix_key_cache_control_stable_across_tail(monkeypatch):
    """Same cache_control prefix + different trailing user message -> same key."""
    import litellm.router_utils.pre_call_checks.prefix_affinity_check as mod

    monkeypatch.setattr(mod, "is_prompt_caching_valid_prompt", lambda **kw: True)
    cfg = {"prefix_strategy": "cache_control"}
    msgs_a = CACHE_CONTROL_MESSAGES
    msgs_b = CACHE_CONTROL_MESSAGES[:1] + [{"role": "user", "content": "Different tail"}]
    key_a = compute_prefix_key(msgs_a, "openai/gpt-4o", cfg)
    key_b = compute_prefix_key(msgs_b, "openai/gpt-4o", cfg)
    assert key_a is not None
    assert key_a == key_b


def test_compute_prefix_key_no_marker_returns_none(monkeypatch):
    import litellm.router_utils.pre_call_checks.prefix_affinity_check as mod

    monkeypatch.setattr(mod, "is_prompt_caching_valid_prompt", lambda **kw: True)
    msgs = [{"role": "user", "content": "no cache_control here"}]
    assert compute_prefix_key(msgs, "openai/gpt-4o", {"prefix_strategy": "cache_control"}) is None


def test_compute_prefix_key_leading_slice(monkeypatch):
    """leading_slice keys on the first N messages; different tail -> same key."""
    import litellm.router_utils.pre_call_checks.prefix_affinity_check as mod

    monkeypatch.setattr(mod, "token_counter", lambda **kw: 2000)
    cfg = {"prefix_strategy": "leading_slice", "leading_slice_messages": 2, "min_prefix_tokens": 1024}
    base = [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]
    key_a = compute_prefix_key(base + [{"role": "user", "content": "t1"}], "openai/gpt-4o", cfg)
    key_b = compute_prefix_key(base + [{"role": "user", "content": "t2"}], "openai/gpt-4o", cfg)
    assert key_a is not None and key_a == key_b


def test_compute_prefix_key_below_threshold_returns_none(monkeypatch):
    import litellm.router_utils.pre_call_checks.prefix_affinity_check as mod

    monkeypatch.setattr(mod, "token_counter", lambda **kw: 100)
    cfg = {"prefix_strategy": "leading_slice", "min_prefix_tokens": 1024}
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]
    assert compute_prefix_key(msgs, "openai/gpt-4o", cfg) is None


def test_select_deployment_hrw_deterministic():
    deployments = [_deployment("a"), _deployment("b"), _deployment("c")]
    first = select_deployment_hrw("key-1", deployments)
    second = select_deployment_hrw("key-1", deployments)
    assert first is not None
    assert first["model_info"]["id"] == second["model_info"]["id"]


def test_select_deployment_hrw_spreads_distinct_keys():
    deployments = [_deployment(x) for x in ("a", "b", "c", "d")]
    chosen = {select_deployment_hrw(f"key-{i}", deployments)["model_info"]["id"] for i in range(50)}
    assert len(chosen) >= 2  # distinct prefixes do not all pile on one deployment


def test_select_deployment_hrw_stable_when_other_removed():
    deployments = [_deployment(x) for x in ("a", "b", "c", "d")]
    picked = select_deployment_hrw("key-1", deployments)["model_info"]["id"]
    remaining = [d for d in deployments if d["model_info"]["id"] != picked]
    # remove a NON-picked deployment -> the key still maps to the same one
    drop_one = [d for d in deployments if d["model_info"]["id"] != remaining[0]["model_info"]["id"]]
    assert select_deployment_hrw("key-1", drop_one)["model_info"]["id"] == picked
    # remove the PICKED deployment -> it remaps to a different one
    assert select_deployment_hrw("key-1", remaining)["model_info"]["id"] != picked
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `cd /Users/wongibaek/Documents/litellm-ops/litellm && uv run pytest tests/test_litellm/router_utils/pre_call_checks/test_prefix_affinity_check.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'litellm.router_utils.pre_call_checks.prefix_affinity_check'`.

- [ ] **Step 5: Write the helpers**

Create `litellm/router_utils/pre_call_checks/prefix_affinity_check.py`:

```python
"""
Prefix-affinity deployment filter.

Routes requests that share a cacheable prefix to the same deployment (provider
prompt-cache domain), maximizing provider prompt-cache hit rate, while reusing
the Router's existing health/rate-limit filtering for load fallback.

Enable via router_settings.optional_pre_call_checks: ["prefix_affinity"].
Mutually exclusive with the built-in "prompt_caching" check.
"""

import hashlib
import json
from typing import List, Optional

from litellm.router_utils.prompt_caching_cache import PromptCachingCache
from litellm.types.llms.openai import AllMessageValues
from litellm.types.router import PrefixAffinityConfig
from litellm.utils import is_prompt_caching_valid_prompt, token_counter

DEFAULT_TTL_SECONDS = 300
DEFAULT_PREFIX_STRATEGY = "cache_control"
DEFAULT_LEADING_SLICE_MESSAGES = 2
DEFAULT_MIN_PREFIX_TOKENS = 1024


def compute_prefix_key(
    messages: Optional[List[AllMessageValues]],
    model: str,
    config: PrefixAffinityConfig,
) -> Optional[str]:
    """Return a stable sha256 hash of the cacheable prefix, or None if no affinity applies."""
    if not messages:
        return None

    strategy = config.get("prefix_strategy", DEFAULT_PREFIX_STRATEGY)

    if strategy == "cache_control":
        prefix = PromptCachingCache.extract_cacheable_prefix(messages)
        if not prefix:
            return None
        # gate: provider supports caching AND prompt >= 1024 tokens (same as built-in)
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
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd /Users/wongibaek/Documents/litellm-ops/litellm && uv run pytest tests/test_litellm/router_utils/pre_call_checks/test_prefix_affinity_check.py -v`
Expected: PASS (7 tests).

- [ ] **Step 7: Format and commit**

```bash
cd /Users/wongibaek/Documents/litellm-ops/litellm
uv run black litellm/types/router.py litellm/router_utils/pre_call_checks/prefix_affinity_check.py tests/test_litellm/router_utils/pre_call_checks/test_prefix_affinity_check.py
git add litellm/types/router.py litellm/router_utils/pre_call_checks/prefix_affinity_check.py tests/test_litellm/router_utils/pre_call_checks/test_prefix_affinity_check.py
git commit -m "feat(router): prefix-affinity prefix-key + HRW selection helpers"
```

---

### Task 2: The `PrefixAffinityDeploymentCheck` callback

**Files:**
- Modify: `litellm/router_utils/pre_call_checks/prefix_affinity_check.py` (add the class)
- Test: `tests/test_litellm/router_utils/pre_call_checks/test_prefix_affinity_check.py` (add cases)

**Interfaces:**
- Consumes: `compute_prefix_key`, `select_deployment_hrw`, `DEFAULT_TTL_SECONDS`, `PrefixAffinityConfig` (Task 1); `CustomLogger`, `Span` (`litellm/integrations/custom_logger.py`); `DualCache` (`litellm/caching/dual_cache.py`); `CallTypes`, `StandardLoggingPayload` (`litellm/types/utils.py`); `verbose_logger` (`from litellm import verbose_logger`).
- Produces:
  - `class PrefixAffinityDeploymentCheck(CustomLogger)` with `__init__(self, cache: DualCache, config: Optional[PrefixAffinityConfig] = None)`
  - `async def async_filter_deployments(self, model, healthy_deployments, messages, request_kwargs=None, parent_otel_span=None) -> List[dict]`
  - `async def async_log_success_event(self, kwargs, response_obj, start_time, end_time)`
  - `def _cache_key(self, prefix_key: str) -> str` → `f"deployment:{prefix_key}:prefix_affinity"`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_litellm/router_utils/pre_call_checks/test_prefix_affinity_check.py`:

```python
import pytest

from litellm.caching.dual_cache import DualCache
from litellm.router_utils.pre_call_checks.prefix_affinity_check import (
    PrefixAffinityDeploymentCheck,
    compute_prefix_key,
)

# leading_slice + min_prefix_tokens=0 avoids needing 1024-token fixtures
_CFG = {"prefix_strategy": "leading_slice", "leading_slice_messages": 2, "min_prefix_tokens": 0}
_MSGS = [
    {"role": "system", "content": "S"},
    {"role": "user", "content": "U"},
    {"role": "user", "content": "tail"},
]


def _check() -> PrefixAffinityDeploymentCheck:
    return PrefixAffinityDeploymentCheck(cache=DualCache(), config=_CFG)


@pytest.mark.asyncio
async def test_filter_hrw_when_no_affinity_yet():
    check = _check()
    deployments = [_deployment("a"), _deployment("b"), _deployment("c")]
    out = await check.async_filter_deployments("gpt", deployments, _MSGS)
    assert len(out) == 1
    assert out[0]["model_info"]["id"] in {"a", "b", "c"}


@pytest.mark.asyncio
async def test_filter_sticky_overrides_hrw():
    check = _check()
    deployments = [_deployment("a"), _deployment("b"), _deployment("c")]
    key = compute_prefix_key(_MSGS, "gpt", _CFG)
    await check.cache.async_set_cache(check._cache_key(key), {"model_id": "b"}, ttl=300)
    out = await check.async_filter_deployments("gpt", deployments, _MSGS)
    assert out[0]["model_info"]["id"] == "b"


@pytest.mark.asyncio
async def test_filter_falls_back_to_hrw_when_sticky_saturated():
    check = _check()
    deployments = [_deployment("a"), _deployment("b"), _deployment("c")]
    hrw_pick = (await check.async_filter_deployments("gpt", deployments, _MSGS))[0]["model_info"]["id"]
    key = compute_prefix_key(_MSGS, "gpt", _CFG)
    # sticky points to "z" which is NOT in the healthy set -> fall back to HRW
    await check.cache.async_set_cache(check._cache_key(key), {"model_id": "z"}, ttl=300)
    out = await check.async_filter_deployments("gpt", deployments, _MSGS)
    assert out[0]["model_info"]["id"] == hrw_pick


@pytest.mark.asyncio
async def test_success_event_writes_affinity_entry():
    check = _check()
    slo = {"call_type": "acompletion", "model": "gpt", "messages": _MSGS, "model_id": "c"}
    await check.async_log_success_event({"standard_logging_object": slo}, None, 0, 0)
    key = compute_prefix_key(_MSGS, "gpt", _CFG)
    assert await check.cache.async_get_cache(key=check._cache_key(key)) == {"model_id": "c"}


@pytest.mark.asyncio
async def test_success_event_uses_configured_ttl(monkeypatch):
    check = PrefixAffinityDeploymentCheck(
        cache=DualCache(),
        config={
            "prefix_strategy": "leading_slice",
            "leading_slice_messages": 2,
            "min_prefix_tokens": 0,
            "ttl_seconds": 42,
        },
    )
    captured = {}

    async def fake_set(key, value, ttl=None, **kw):
        captured["ttl"] = ttl

    monkeypatch.setattr(check.cache, "async_set_cache", fake_set)
    slo = {"call_type": "acompletion", "model": "gpt", "messages": _MSGS, "model_id": "c"}
    await check.async_log_success_event({"standard_logging_object": slo}, None, 0, 0)
    assert captured["ttl"] == 42


@pytest.mark.asyncio
async def test_filter_passthrough_on_trivial_inputs():
    check = _check()
    deployments = [_deployment("a"), _deployment("b")]
    assert await check.async_filter_deployments("gpt", deployments, None) == deployments
    assert await check.async_filter_deployments("gpt", [deployments[0]], _MSGS) == [deployments[0]]


@pytest.mark.asyncio
async def test_filter_never_raises(monkeypatch):
    import litellm.router_utils.pre_call_checks.prefix_affinity_check as mod

    def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(mod, "compute_prefix_key", boom)
    check = _check()
    deployments = [_deployment("a"), _deployment("b")]
    out = await check.async_filter_deployments("gpt", deployments, _MSGS)
    assert out == deployments  # returns input, no raise
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/wongibaek/Documents/litellm-ops/litellm && uv run pytest tests/test_litellm/router_utils/pre_call_checks/test_prefix_affinity_check.py -k "filter or success_event" -v`
Expected: FAIL — `ImportError: cannot import name 'PrefixAffinityDeploymentCheck'`.

- [ ] **Step 3: Add the class**

Update the existing `from typing import List, Optional` line at the top of
`litellm/router_utils/pre_call_checks/prefix_affinity_check.py` to add `cast`:

```python
from typing import List, Optional, cast
```

Then add these imports to the top of the file (keep the Task 1 imports):

```python
from litellm import verbose_logger
from litellm.caching.dual_cache import DualCache
from litellm.integrations.custom_logger import CustomLogger, Span
from litellm.types.utils import CallTypes, StandardLoggingPayload
```

Append the class to the same file:

```python
class PrefixAffinityDeploymentCheck(CustomLogger):
    """Pre-call deployment filter: route a request to the deployment that already
    holds the provider prompt cache for its prefix; otherwise place it
    deterministically via HRW. Reuses the Router's healthy-deployment list, so a
    saturated deployment (already filtered out upstream) is skipped automatically."""

    def __init__(self, cache: DualCache, config: Optional[PrefixAffinityConfig] = None):
        self.cache = cache
        self.config: PrefixAffinityConfig = config or {}

    def _cache_key(self, prefix_key: str) -> str:
        return f"deployment:{prefix_key}:prefix_affinity"

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

            prefix_key = compute_prefix_key(messages, model, self.config)
            if prefix_key is None:
                return healthy_deployments

            # sticky: route to the previously-cached deployment if still healthy
            cached = await self.cache.async_get_cache(key=self._cache_key(prefix_key))
            if isinstance(cached, dict):
                model_id = cached.get("model_id")
                if model_id is not None:
                    for deployment in healthy_deployments:
                        if deployment["model_info"]["id"] == model_id:
                            return [deployment]

            # first-touch / fallback: deterministic HRW among healthy deployments
            chosen = select_deployment_hrw(prefix_key, healthy_deployments)
            if chosen is not None:
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
                self._cache_key(prefix_key), {"model_id": model_id}, ttl=ttl
            )
        except Exception as e:
            verbose_logger.debug(f"PrefixAffinityDeploymentCheck.log error: {e}")
        return
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd /Users/wongibaek/Documents/litellm-ops/litellm && uv run pytest tests/test_litellm/router_utils/pre_call_checks/test_prefix_affinity_check.py -v`
Expected: PASS (all 14 tests).

- [ ] **Step 5: Format and commit**

```bash
cd /Users/wongibaek/Documents/litellm-ops/litellm
uv run black litellm/router_utils/pre_call_checks/prefix_affinity_check.py tests/test_litellm/router_utils/pre_call_checks/test_prefix_affinity_check.py
git add litellm/router_utils/pre_call_checks/prefix_affinity_check.py tests/test_litellm/router_utils/pre_call_checks/test_prefix_affinity_check.py
git commit -m "feat(router): PrefixAffinityDeploymentCheck callback (sticky + HRW + TTL write)"
```

---

### Task 3: Router wiring + config

**Files:**
- Modify: `litellm/types/router.py:763-775` (add `"prefix_affinity"` to the `OptionalPreCallChecks` literal)
- Modify: `litellm/router.py` (import at L128-130 block; `__init__` param at L329; `litellm.types.router` import block at L142-156; storage near L703; registration branch at L1753-1754)
- Test: `tests/test_litellm/router_utils/pre_call_checks/test_prefix_affinity_check.py` (add cases)

**Interfaces:**
- Consumes: `PrefixAffinityDeploymentCheck`, `PrefixAffinityConfig` (Tasks 1-2)
- Produces: Router accepts `prefix_affinity_config: Optional[PrefixAffinityConfig] = None`; enabling `optional_pre_call_checks=["prefix_affinity"]` installs the callback; `Router.get_valid_args()` includes `"prefix_affinity_config"`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_litellm/router_utils/pre_call_checks/test_prefix_affinity_check.py`:

```python
import litellm


def _router(**kwargs):
    return litellm.Router(
        model_list=[
            {"model_name": "gpt", "litellm_params": {"model": "openai/gpt-4o", "api_key": "sk-x"}, "model_info": {"id": "a"}},
            {"model_name": "gpt", "litellm_params": {"model": "openai/gpt-4o", "api_key": "sk-y"}, "model_info": {"id": "b"}},
        ],
        **kwargs,
    )


def test_get_valid_args_includes_prefix_affinity_config():
    # guarantees config.yaml router_settings.prefix_affinity_config is accepted by the proxy
    assert "prefix_affinity_config" in litellm.Router.get_valid_args()


def test_router_registers_prefix_affinity_callback():
    router = _router(
        optional_pre_call_checks=["prefix_affinity"],
        prefix_affinity_config={"prefix_strategy": "leading_slice", "min_prefix_tokens": 0},
    )
    assert any(
        isinstance(cb, PrefixAffinityDeploymentCheck) for cb in (router.optional_callbacks or [])
    )


@pytest.mark.asyncio
async def test_registered_callback_filters_by_prefix():
    router = _router(
        optional_pre_call_checks=["prefix_affinity"],
        prefix_affinity_config={"prefix_strategy": "leading_slice", "leading_slice_messages": 2, "min_prefix_tokens": 0},
    )
    cb = next(c for c in router.optional_callbacks if isinstance(c, PrefixAffinityDeploymentCheck))
    out = await cb.async_filter_deployments("gpt", router.model_list, _MSGS)
    assert len(out) == 1
    assert out[0]["model_info"]["id"] in {"a", "b"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/wongibaek/Documents/litellm-ops/litellm && uv run pytest tests/test_litellm/router_utils/pre_call_checks/test_prefix_affinity_check.py -k "valid_args or registers or registered_callback" -v`
Expected: FAIL — `test_get_valid_args_includes_prefix_affinity_config` asserts False; the Router has no `prefix_affinity_config` param so `"prefix_affinity"` is not registered.

- [ ] **Step 3: Add the literal**

In `litellm/types/router.py`, add `"prefix_affinity",` to the `OptionalPreCallChecks` literal list (after `"encrypted_content_affinity",`):

```python
OptionalPreCallChecks = List[
    Literal[
        "prompt_caching",
        "router_budget_limiting",
        "responses_api_deployment_check",
        "deployment_affinity",
        "session_affinity",
        "forward_client_headers_by_model_group",
        "enforce_model_rate_limits",
        "encrypted_content_affinity",
        "prefix_affinity",
    ]
]
```

- [ ] **Step 4: Wire the Router**

In `litellm/router.py`:

(a) Add the import after the `PromptCachingDeploymentCheck` import block (L128-130):

```python
from litellm.router_utils.pre_call_checks.prefix_affinity_check import (
    PrefixAffinityDeploymentCheck,
)
```

(b) Add `PrefixAffinityConfig` to the `from litellm.types.router import (` block (L142-156), next to `OptionalPreCallChecks,`:

```python
    PrefixAffinityConfig,
```

(c) Add the `__init__` parameter immediately after `model_group_affinity_config: Optional[Dict[str, List[str]]] = None,` (L329):

```python
        prefix_affinity_config: Optional[PrefixAffinityConfig] = None,
```

(d) Add storage immediately after the `self.model_group_affinity_config = (...)` assignment (ends ~L704):

```python
        self.prefix_affinity_config: Optional[PrefixAffinityConfig] = prefix_affinity_config
```

(e) Add the registration branch in `add_optional_pre_call_checks`, after the `if pre_call_check == "prompt_caching":` branch (L1753-1754):

```python
            elif pre_call_check == "prefix_affinity":
                _callback = PrefixAffinityDeploymentCheck(
                    cache=self.cache, config=self.prefix_affinity_config or {}
                )
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd /Users/wongibaek/Documents/litellm-ops/litellm && uv run pytest tests/test_litellm/router_utils/pre_call_checks/test_prefix_affinity_check.py -v`
Expected: PASS (all 17 tests).

- [ ] **Step 6: Run the broader router pre-call-check suite for regressions**

Run: `cd /Users/wongibaek/Documents/litellm-ops/litellm && uv run pytest tests/test_litellm/router_utils/pre_call_checks/ -v`
Expected: PASS (existing `test_deployment_affinity_check.py` still green).

- [ ] **Step 7: Format, lint, commit**

```bash
cd /Users/wongibaek/Documents/litellm-ops/litellm
uv run black litellm/types/router.py litellm/router.py tests/test_litellm/router_utils/pre_call_checks/test_prefix_affinity_check.py
uv run ruff check litellm/router_utils/pre_call_checks/prefix_affinity_check.py litellm/router.py
git add litellm/types/router.py litellm/router.py tests/test_litellm/router_utils/pre_call_checks/test_prefix_affinity_check.py
git commit -m "feat(router): register prefix_affinity pre-call check + config wiring"
```

---

## Verification (whole feature)

- `cd /Users/wongibaek/Documents/litellm-ops/litellm && uv run pytest tests/test_litellm/router_utils/pre_call_checks/test_prefix_affinity_check.py -v` → all green (17 tests).
- `uv run pytest tests/test_litellm/router_utils/ -v` → no regressions.
- `uv run black --check litellm/router_utils/pre_call_checks/prefix_affinity_check.py litellm/router.py litellm/types/router.py` → clean.
- Manual config sanity (documented, not automated): a proxy `config.yaml` with
  `router_settings: { optional_pre_call_checks: ["prefix_affinity"], prefix_affinity_config: { ttl_seconds: 300, prefix_strategy: "cache_control" } }`
  starts without the "not a valid router_settings parameter" warning (guaranteed by `test_get_valid_args_includes_prefix_affinity_config`).

## Out of scope (v1)

- Block-level prefix radix tree; per-`model_group` config overrides; portal UI toggle; auto-injecting `cache_control`; observability (per-decision debug logging + `request_kwargs` metadata stamping) — spec lists these; deferred to a follow-up if requested.
</content>
</invoke>
