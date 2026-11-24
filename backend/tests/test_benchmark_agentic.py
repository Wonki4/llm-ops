"""Measurement boundaries and immutable replay inputs, without a GPU dependency."""

import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import portal_bench_runner as runner
from app.services.benchmark_scenarios import builtin_scenario
from app.services.benchmark_workload import PerformanceWorkload, performance_workload
from tests.test_prefix_cache_benchmark import StreamResponse, TokenizerResponse


def workload(**changes):
    return PerformanceWorkload(
        workload_version=2, random_input_len=100, random_output_len=8, num_prompts=6, max_concurrency=2, **changes
    ).model_dump()


@pytest.mark.parametrize("ratio", [0, 90, 100])
async def test_synthetic_total_length_single_phase_and_preparation_exclusion(monkeypatch, ratio):
    sent, boundaries = [], []
    monkeypatch.setenv("BENCH_TOKEN_INPUT", "1")
    monkeypatch.setenv("BENCH_ISOLATED", "1")

    async def stream(session, url, payload):
        assert url.endswith("/v1/completions")
        sent.append(payload)
        return runner.Sample(len(payload["prompt"]), payload["max_tokens"], 10, 20, None, [])

    async def counters(session, url):
        boundaries.append(len(sent))
        return {"hits": {"engine": 0}, "queries": {"engine": sum(len(p["prompt"]) for p in sent)}}

    monkeypatch.setattr(runner, "stream", stream)
    monkeypatch.setattr(runner, "counters", counters)
    result = {}
    await runner.run_synthetic(
        SimpleNamespace(post=lambda *a, **kw: TokenizerResponse()),
        "http://server",
        "m",
        workload(cache_ratio=ratio),
        result,
    )
    prep = 1 + bool(ratio)
    assert boundaries == [prep, prep + 6]
    assert len(sent) == prep + 6 and all(len(p["prompt"]) == 100 for p in sent)
    measured = sent[prep:]
    assert all(p["prompt"][:ratio] == measured[0]["prompt"][:ratio] for p in measured)
    assert result["metrics"]["completed"] == 6
    assert result["metrics"]["total_output_tokens"] == 6 * 8
    assert result["metrics"]["cache"] == {"hits": 0, "queries": 600, "hit_rate": 0}
    assert "phases" not in result
    if ratio == 0:
        assert len({tuple(p["prompt"]) for p in measured}) == 6


async def test_shared_target_never_reports_engine_counters(monkeypatch):
    monkeypatch.delenv("BENCH_ISOLATED", raising=False)
    scrape = AsyncMock()
    monkeypatch.setattr(runner, "counters", scrape)
    assert await runner.isolated_counters(None, "http://shared") is None
    scrape.assert_not_awaited()


def test_scenario_is_pinned_and_edits_rejected():
    p = performance_workload({"workload_version": 2, "workload_type": "agentic_replay"})
    assert p["scenario"] == builtin_scenario()
    assert p["scenario"]["input_hash"] == "207064b257169d36ffa9683f84e38a4482dfb7514c3e3faeb672a3916a7007a5"
    assert len(p["scenario"]["turns"]) == 3
    assert len({len(json.dumps(t)) for t in p["scenario"]["turns"]}) == 3
    assert not p["ignore_eos"]
    assert performance_workload(p) == p
    changed = copy.deepcopy(p)
    changed["scenario"]["turns"][0]["messages"][0]["content"] = "changed"
    with pytest.raises(ValueError, match="modified scenario"):
        performance_workload(changed)
    assert performance_workload({"cache_ratio": 90})["workload_version"] == 2
    assert performance_workload({"num_prompts": 3})["cache_ratio"] == 0  # old runs are not overwritten


@pytest.mark.parametrize("fail", [False, True])
async def test_agentic_order_concurrency_partial_failure_and_weighted_throughput(monkeypatch, fail):
    p = workload(workload_type="agentic_replay", num_sessions=3)
    steps = {len(t["messages"]): i for i, t in enumerate(p["scenario"]["turns"])}
    calls, order = [], {}
    active = peak = 0
    broken = False

    async def stream(session, url, payload):
        nonlocal active, peak, broken
        calls.append(copy.deepcopy(payload))
        if len(calls) <= 2:  # warmup and shared-prefix preparation
            return runner.Sample(99, 1, 1, 1, None, [])
        assert payload["messages"] == p["scenario"]["turns"][steps[len(payload["messages"])]]["messages"]
        assert "ignore_eos" not in payload
        step = steps[len(payload["messages"])]
        task = asyncio.current_task()
        order.setdefault(task, []).append(step)
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.001)
        active -= 1
        if fail and step == 1 and not broken:
            broken = True
            raise ValueError("unsupported response")
        return runner.Sample((step + 1) * 100, step + 2, 10, 25, None, [])

    monkeypatch.setattr(runner, "stream", stream)
    monkeypatch.setattr(runner, "isolated_counters", AsyncMock(return_value=None))
    result = {}
    if fail:
        with pytest.raises(ValueError, match="unsupported response"):
            await runner.run_agentic(None, "http://server", "m", p, result)
    else:
        await runner.run_agentic(None, "http://server", "m", p, result)
    assert peak == 2
    for values in order.values():
        for a, b in zip(values, values[1:]):
            assert b == 0 or b == a + 1
    m = result["metrics"]
    assert m["requested"] == 9 and m["completed"] == (7 if fail else 9)
    assert m["completed_sessions"] == (2 if fail else 3)
    assert m["failed"] == int(fail) and m["skipped"] == int(fail)
    assert m["total_output_tokens"] == (20 if fail else 27)
    assert m["output_throughput"] == m["total_output_tokens"] / m["duration"]
    assert m["cache"]["hit_rate"] is None
    assert len(calls) == 2 + m["completed"] + m["failed"]
    assert result["turns"][2]["skipped"] == int(fail)
    assert result["scenario"]["input_hash"] == p["scenario"]["input_hash"]


async def test_tool_only_stream_counts_arguments_but_not_tool_metadata():
    events = [
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "id": "call_1", "function": {"name": "search_docs", "arguments": ""}}
                        ]
                    }
                }
            ]
        },
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"query":'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"rollout"}'}}]}}]},
        {"usage": {"prompt_tokens": 100, "completion_tokens": 2}, "choices": []},
    ]
    session = SimpleNamespace(
        post=lambda *a, **kw: StreamResponse([json.dumps(x).encode() for x in events] + [b"[DONE]"])
    )
    sample = await runner.stream(session, "/v1/chat/completions", {})
    assert sample.inputs == 100 and sample.outputs == 2 and len(sample.itls) == 1


@pytest.mark.parametrize(
    "bad",
    [{"cache_ratio": -1}, {"cache_ratio": 101}, {"num_sessions": 0}, {"max_concurrency": 0}, {"num_prompts": 1.5}],
)
def test_invalid_workload_is_rejected(bad):
    with pytest.raises(ValueError):
        performance_workload({"workload_version": 2, **bad})


async def test_full_runner_against_streaming_engine_and_cache_counters(monkeypatch, capsys):
    """Exercise the actual HTTP/SSE path; this fixture is not a GPU benchmark."""
    from aiohttp import web

    totals = {"hits": 0, "queries": 0}
    prefixes = set()
    calls = []

    async def tokenize(request):
        return web.json_response({"tokens": list(range(1000))})

    async def metrics(request):
        return web.Response(text="\n".join(f"vllm:prefix_cache_{key}_total {value}" for key, value in totals.items()))

    async def completion(request):
        assert request.headers["Authorization"] == "Bearer local-runner-test-key"
        payload = await request.json()
        calls.append(payload)
        prompt = payload.get("prompt")
        if prompt is not None:
            prefix = tuple(prompt[:90])
            totals["queries"] += len(prompt)
            totals["hits"] += 90 if prefix in prefixes else 0
            prefixes.add(prefix)
            inputs = len(prompt)
            delta = {"content": "ok"}
        else:
            inputs = len(json.dumps(payload["messages"]))
            delta = {"tool_calls": [{"index": 0, "function": {"arguments": '{"query":"rollout"}'}}]}
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        for event in (
            {"choices": [{"delta": delta}]},
            {"choices": [], "usage": {"prompt_tokens": inputs, "completion_tokens": 1}},
        ):
            await response.write(("data: " + json.dumps(event) + "\n\n").encode())
        await response.write(b"data: [DONE]\n\n")
        await response.write_eof()
        return response

    app = web.Application()
    app.router.add_post("/tokenize", tokenize)
    app.router.add_get("/metrics", metrics)
    app.router.add_post("/v1/completions", completion)
    app.router.add_post("/v1/chat/completions", completion)
    server = web.AppRunner(app)
    await server.setup()
    site = web.TCPSite(server, "127.0.0.1", 0)
    try:
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        monkeypatch.setenv("BENCH_URL", f"http://127.0.0.1:{port}")
        monkeypatch.setenv("BENCH_MODEL", "fixture-model")
        monkeypatch.setenv("BENCH_RUNNER_API_KEY", "local-runner-test-key")
        monkeypatch.setenv("BENCH_ISOLATED", "1")
        monkeypatch.setenv("BENCH_TOKEN_INPUT", "1")
        monkeypatch.setenv("BENCH_WORKLOAD", json.dumps(workload(cache_ratio=90)))
        await runner.main()
        output = capsys.readouterr().out
        result = json.loads(output.split("<<<RESULT>>>")[-1])
        assert result["metrics"]["cache"] == {"hits": 540, "queries": 600, "hit_rate": 0.9}
        assert result["metrics"]["completed"] == 6 and len(calls) == 8
        assert "local-runner-test-key" not in output
        calls.clear()
        monkeypatch.setenv("BENCH_WORKLOAD", json.dumps(workload(workload_type="agentic_replay", num_sessions=2)))
        await runner.main()
        result = json.loads(capsys.readouterr().out.split("<<<RESULT>>>")[-1])
        assert result["metrics"]["completed_sessions"] == 2
        assert result["metrics"]["completed"] == 6 and len(calls) == 8
        assert len(result["turns"]) == 3
        assert result["metrics"]["cache"]["hit_rate"] is None  # no queries in this mock's chat path
        calls.clear()
        monkeypatch.setenv("BENCH_TOKEN_INPUT", "0")
        monkeypatch.setenv("BENCH_ISOLATED", "0")
        monkeypatch.setenv("BENCH_MODEL", "alias-not-a-huggingface-repository")
        def no_download(*args):
            raise AssertionError("URL-only benchmark must not download a tokenizer")
        monkeypatch.setattr(runner, "load_tokenizer", no_download)
        monkeypatch.setenv("BENCH_WORKLOAD", json.dumps(workload(cache_ratio=90, input_length_mode="text_estimate")))
        await runner.main()
        result = json.loads(capsys.readouterr().out.split("<<<RESULT>>>")[-1])
        assert result["metrics"]["completed"] == 6 and len(calls) == 8
        measured = [p["messages"][0]["content"].split() for p in calls[2:]]
        assert all(len(words) == 100 and words[:90] == measured[0][:90] for words in measured)
        assert len({tuple(words) for words in measured}) > 1
        assert result["input"]["length_mode"] == "text_estimate"
        assert result["input"]["configured_words"] == 100
        assert "configured_tokens" not in result["input"]
        assert result["metrics"]["total_input_tokens"] == sum(len(json.dumps(p["messages"])) for p in calls[2:])
        assert result["metrics"]["cache"]["hit_rate"] is None
    finally:
        await server.cleanup()


async def test_endpoint_api_pins_scenario_and_placement_in_job_and_history(
    client_for_user, super_user, mock_db, monkeypatch
):
    from uuid import uuid4

    from app.api import benchmarks

    cluster = str(uuid4())
    k8s = SimpleNamespace(create_job=AsyncMock())
    select_cluster = AsyncMock(return_value=k8s)
    monkeypatch.setattr(benchmarks, "k8s_for_cluster", select_cluster)
    monkeypatch.setattr(benchmarks, "bench_volumes", AsyncMock(return_value=([], [])))
    body = {
        "tool": "vllm_serving",
        "base_url": "http://fixture/v1",
        "model_name": "fixture",
        "cluster_id": cluster,
        "namespace": "serving-b",
        "api_key": "fixture-private-key",
        "params": {"workload_type": "agentic_replay", "num_sessions": 2, "random_output_len": 16},
    }
    async with client_for_user(super_user) as client:
        response = await client.post("/api/benchmarks", json=body)
    assert response.status_code == 201, response.text
    run = response.json()
    assert "fixture-private-key" not in response.text
    assert run["params"]["scenario"] == builtin_scenario()
    assert run["params"]["workload_version"] == 2
    assert run["cluster_id"] == cluster and run["k8s_namespace"] == "serving-b"
    assert str(select_cluster.call_args.args[1]) == cluster
    manifest = k8s.create_job.call_args.args[1]
    env = {e["name"]: e for e in manifest["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert json.loads(env["BENCH_WORKLOAD"]["value"])["scenario"] == run["params"]["scenario"]
    assert env["BENCH_ISOLATED"]["value"] == "0"
    assert "fixture-private-key" not in json.dumps(manifest)
