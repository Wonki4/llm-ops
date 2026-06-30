import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

import litellm
import prefix_affinity_check as mod
from litellm.caching.dual_cache import DualCache
from prefix_affinity_check import (
    PrefixAffinityDeploymentCheck,
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

_CFG = {
    "prefix_strategy": "leading_slice",
    "leading_slice_messages": 2,
    "min_prefix_tokens": 0,
}
_MSGS = [
    {"role": "system", "content": "S"},
    {"role": "user", "content": "U"},
    {"role": "user", "content": "tail"},
]


def _deployment(model_id: str) -> dict:
    return {
        "model_name": "gpt",
        "litellm_params": {"model": "openai/gpt-4o"},
        "model_info": {"id": model_id, "db_model": True},
    }


def _check() -> PrefixAffinityDeploymentCheck:
    return PrefixAffinityDeploymentCheck(cache=DualCache(), config=_CFG)


# ── prefix key ──────────────────────────────────────────────────────────────
def test_compute_prefix_key_cache_control_stable_across_tail(monkeypatch):
    monkeypatch.setattr(mod, "is_prompt_caching_valid_prompt", lambda **kw: True)
    cfg = {"prefix_strategy": "cache_control"}
    msgs_b = CACHE_CONTROL_MESSAGES[:1] + [{"role": "user", "content": "Different tail"}]
    key_a = compute_prefix_key(CACHE_CONTROL_MESSAGES, "openai/gpt-4o", cfg)
    key_b = compute_prefix_key(msgs_b, "openai/gpt-4o", cfg)
    assert key_a is not None and key_a == key_b


def test_compute_prefix_key_no_marker_returns_none(monkeypatch):
    monkeypatch.setattr(mod, "is_prompt_caching_valid_prompt", lambda **kw: True)
    msgs = [{"role": "user", "content": "no cache_control here"}]
    assert compute_prefix_key(msgs, "openai/gpt-4o", {"prefix_strategy": "cache_control"}) is None


def test_compute_prefix_key_leading_slice(monkeypatch):
    monkeypatch.setattr(mod, "token_counter", lambda **kw: 2000)
    cfg = {"prefix_strategy": "leading_slice", "leading_slice_messages": 2, "min_prefix_tokens": 1024}
    base = [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]
    key_a = compute_prefix_key(base + [{"role": "user", "content": "t1"}], "openai/gpt-4o", cfg)
    key_b = compute_prefix_key(base + [{"role": "user", "content": "t2"}], "openai/gpt-4o", cfg)
    assert key_a is not None and key_a == key_b


def test_compute_prefix_key_below_threshold_returns_none(monkeypatch):
    monkeypatch.setattr(mod, "token_counter", lambda **kw: 100)
    cfg = {"prefix_strategy": "leading_slice", "min_prefix_tokens": 1024}
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]
    assert compute_prefix_key(msgs, "openai/gpt-4o", cfg) is None


# ── HRW ─────────────────────────────────────────────────────────────────────
def test_select_deployment_hrw_deterministic():
    deployments = [_deployment("a"), _deployment("b"), _deployment("c")]
    first = select_deployment_hrw("key-1", deployments)
    assert first is not None
    assert first["model_info"]["id"] == select_deployment_hrw("key-1", deployments)["model_info"]["id"]


def test_select_deployment_hrw_spreads_distinct_keys():
    deployments = [_deployment(x) for x in ("a", "b", "c", "d")]
    chosen = {select_deployment_hrw(f"key-{i}", deployments)["model_info"]["id"] for i in range(50)}
    assert len(chosen) >= 2


def test_select_deployment_hrw_stable_when_other_removed():
    deployments = [_deployment(x) for x in ("a", "b", "c", "d")]
    picked = select_deployment_hrw("key-1", deployments)["model_info"]["id"]
    remaining = [d for d in deployments if d["model_info"]["id"] != picked]
    drop_one = [d for d in deployments if d["model_info"]["id"] != remaining[0]["model_info"]["id"]]
    assert select_deployment_hrw("key-1", drop_one)["model_info"]["id"] == picked
    assert select_deployment_hrw("key-1", remaining)["model_info"]["id"] != picked


# ── filter / success-event ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_filter_hrw_when_no_affinity_yet():
    out = await _check().async_filter_deployments("gpt", [_deployment("a"), _deployment("b"), _deployment("c")], _MSGS)
    assert len(out) == 1 and out[0]["model_info"]["id"] in {"a", "b", "c"}


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
    check = PrefixAffinityDeploymentCheck(cache=DualCache(), config={**_CFG, "ttl_seconds": 42})
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
    def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(mod, "compute_prefix_key", boom)
    deployments = [_deployment("a"), _deployment("b")]
    assert await _check().async_filter_deployments("gpt", deployments, _MSGS) == deployments


# ── scoping (model-group / provider allowlists; empty = all) ──────────────────
@pytest.mark.asyncio
async def test_scope_model_allowlist_skips_other_groups():
    check = PrefixAffinityDeploymentCheck(cache=DualCache(), config={**_CFG, "models": ["gpt-4o"]})
    deployments = [_deployment("a"), _deployment("b"), _deployment("c")]
    assert await check.async_filter_deployments("other", deployments, _MSGS) == deployments  # out of scope
    assert len(await check.async_filter_deployments("gpt-4o", deployments, _MSGS)) == 1       # in scope


@pytest.mark.asyncio
async def test_scope_provider_allowlist():
    openai_deps = [_deployment("a"), _deployment("b")]  # litellm_params.model = openai/gpt-4o
    ok = PrefixAffinityDeploymentCheck(cache=DualCache(), config={**_CFG, "providers": ["openai"]})
    assert len(await ok.async_filter_deployments("gpt", openai_deps, _MSGS)) == 1
    no = PrefixAffinityDeploymentCheck(cache=DualCache(), config={**_CFG, "providers": ["anthropic"]})
    assert await no.async_filter_deployments("gpt", openai_deps, _MSGS) == openai_deps


@pytest.mark.asyncio
async def test_scope_empty_applies_to_all():
    out = await _check().async_filter_deployments("anything", [_deployment("a"), _deployment("b")], _MSGS)
    assert len(out) == 1


# ── NATIVE registration: callback in litellm.callbacks gets its filter invoked ──
@pytest.mark.asyncio
async def test_native_callback_registration_is_invoked():
    """Proves the no-fork path: a CustomLogger added to litellm.callbacks has its
    async_filter_deployments called by the Router's deployment-selection filter."""
    handler = PrefixAffinityDeploymentCheck(config=_CFG)
    litellm.callbacks.append(handler)
    try:
        router = litellm.Router(
            model_list=[
                {"model_name": "gpt", "litellm_params": {"model": "openai/gpt-4o", "api_key": "sk-x"}, "model_info": {"id": "a"}},
                {"model_name": "gpt", "litellm_params": {"model": "openai/gpt-4o", "api_key": "sk-y"}, "model_info": {"id": "b"}},
            ],
            enable_pre_call_checks=True,
        )
        out = await router.async_callback_filter_deployments(
            model="gpt",
            healthy_deployments=router.model_list,
            messages=_MSGS,
            parent_otel_span=None,
        )
        assert len(out) == 1
        assert out[0]["model_info"]["id"] in {"a", "b"}
    finally:
        litellm.callbacks.remove(handler)
