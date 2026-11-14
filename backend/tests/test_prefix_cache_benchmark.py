import random
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.benchmarks import CreateBenchmarkRequest, _cache_template
from app.services import portal_bench_runner as runner

WORKLOAD = dict(
    experiment="prefix_cache",
    random_input_len=128,
    prefix_len=96,
    random_output_len=8,
    max_concurrency=2,
    num_prompts=6,
    seed=0,
)


def test_rates_are_token_weighted_and_distinguish_missing_and_zero():
    before = {"hits": {"a": 10, "b": 20}, "queries": {"a": 10, "b": 80}}
    after = {"hits": {"a": 20, "b": 20}, "queries": {"a": 20, "b": 170}}
    assert runner.counter_delta(before, after) == {"hits": 10, "queries": 100, "hit_rate": 0.1}
    after["hits"] = before["hits"]
    assert runner.counter_delta(before, after)["hit_rate"] == 0
    assert runner.counter_delta(before, before)["hit_rate"] is None
    assert runner.counter_delta(None, after)["hit_rate"] is None
    assert runner.counter_delta(after, before)["hit_rate"] is None


class TokenizerResponse:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def raise_for_status(self):
        pass

    async def json(self):
        return {"tokens": list(range(1000))}


async def test_measurement_excludes_primes_and_preserves_input_length(monkeypatch):
    sent = []
    boundaries = []

    async def complete(session, model, tokens, output_len):
        sent.append(tokens)
        return 20.0, output_len

    async def counters(session):
        boundaries.append(len(sent))
        return {"queries": {"a": sum(map(len, sent))}, "hits": {"a": 0}}

    monkeypatch.setattr(runner, "complete", complete)
    monkeypatch.setattr(runner, "counters", counters)
    result = {"phases": {}}
    await runner.run(SimpleNamespace(post=lambda *a, **kw: TokenizerResponse()), "m", WORKLOAD, result)
    assert boundaries == [1, 7, 8, 14]
    assert all(len(tokens) == 128 for tokens in sent)
    assert len(set(map(tuple, sent))) == len(sent)
    assert all(tokens[:96] == sent[7][:96] for tokens in sent[8:])
    assert len({tuple(tokens[:96]) for tokens in sent[:7]}) == 7
    for phase in result["phases"].values():
        assert phase["cache"]["queries"] == 6 * 128
        assert phase["completed"] == 6


async def test_partial_failure_preserves_measured_requests(monkeypatch):
    complete = AsyncMock(side_effect=[(20.0, 8), ValueError("broken stream")])
    monkeypatch.setattr(runner, "complete", complete)
    monkeypatch.setattr(runner, "counters", AsyncMock(return_value=None))
    phase = {}
    with pytest.raises(ValueError, match="broken stream"):
        await runner.measure(None, "m", WORKLOAD, runner.prompts(random.Random(0), [1, 2], 128, [], 6), phase)
    assert phase["completed"] == 1 and phase["median_ttft_ms"] == 20


async def test_cache_template_uses_original_placement():
    base = SimpleNamespace(
        cluster_id=uuid4(),
        namespace="inference",
        engine="vllm",
        serving_mode="aggregated",
        vllm_extra_args=[],
        engine_args={},
        runtime={},
    )
    body = CreateBenchmarkRequest(tool="vllm_serving", ephemeral=True, deployment_id=str(uuid4()), params=WORKLOAD)
    db = SimpleNamespace(get=AsyncMock(return_value=base))
    await _cache_template(body, db)
    assert (body.cluster_id, body.namespace) == (str(base.cluster_id), "inference")
    for key, value in [("engine", "sglang"), ("serving_mode", "pd")]:
        with patch.object(base, key, value), pytest.raises(HTTPException):
            await _cache_template(body, db)
    with patch.object(base, "vllm_extra_args", ["--nnodes", "2"]), pytest.raises(HTTPException):
        await _cache_template(body, db)
    with pytest.raises(HTTPException):
        await _cache_template(body.model_copy(update={"ephemeral": False}), db)
    with pytest.raises(HTTPException):
        await _cache_template(body.model_copy(update={"params": {**WORKLOAD, "prefix_len": 128}}), db)


class StreamResponse(TokenizerResponse):
    status = 200

    def __init__(self, events):
        async def lines():
            for event in events:
                yield b"data: " + event + b"\n"

        self.content = lines()


async def test_stream_counts_server_tokens_and_skips_role_and_usage_only_chunks():
    import json

    events = [
        json.dumps(event).encode()
        for event in (
            {"choices": [{"delta": {"role": "assistant"}}]},
            {"choices": [{"delta": {"content": "one"}}]},
            {"choices": [{"delta": {"content": "two"}}]},
            {"usage": {"completion_tokens": 2, "prompt_tokens": 40}, "choices": []},
        )
    ]
    session = SimpleNamespace(post=lambda *a, **kw: StreamResponse(events + [b"[DONE]"]))
    sample = await runner.stream(session, "/v1/chat/completions", {"model": "alias"})
    assert (sample.inputs, sample.outputs) == (40, 2)
    assert len(sample.itls) == 1 and 0 <= sample.ttft <= sample.e2el
    dropped = SimpleNamespace(post=lambda *a, **kw: StreamResponse(events))
    with pytest.raises(ValueError, match="Incomplete stream"):
        await runner.stream(dropped, "/v1/chat/completions", {})


async def test_performance_uses_chat_path_auth_independent_of_engine_and_keeps_partial(monkeypatch):
    calls = []

    async def stream(session, url, payload):
        calls.append(payload)
        assert url == "http://proxy/mounted/v1/chat/completions"
        if len(calls) == 2:
            raise ValueError("incomplete")
        return runner.Sample(15, 3, 10, 30, 8, [7, 9])

    monkeypatch.setattr(runner, "stream", stream)
    metrics = {}
    with pytest.raises(ValueError, match="incomplete"):
        await runner.run_performance(
            None,
            runner.chat_url("http://proxy/mounted/v1/"),
            "alias",
            dict(seed=0, random_output_len=8, max_concurrency=1, goodput="ttft:20"),
            ["one", "two", "three"],
            metrics,
        )
    result = metrics["metrics"]
    assert result["completed"] == result["failed"] == 1 and result["requested"] == 3
    assert result["total_input_tokens"] == 15 and result["total_output_tokens"] == 3
    assert result["mean_itl_ms"] == 8 and result["p99_ttft_ms"] == 10
    assert result["request_goodput"] == result["request_throughput"]
    assert calls[0]["messages"] == [{"role": "user", "content": "one"}] and "ignore_eos" not in calls[0]
