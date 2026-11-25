"""Measure vLLM engine phases over the isolated synthetic workload."""

from types import SimpleNamespace

from app.services import portal_bench_runner as runner
from tests.test_benchmark_agentic import workload
from tests.test_prefix_cache_benchmark import TokenizerResponse


class MetricsResponse:
    def __init__(self, completed):
        self.completed = completed

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    def raise_for_status(self):
        pass

    async def text(self):
        lines = ["process_start_time_seconds 100"]
        for name, mean in (
            ("prefill_time_seconds", 0.5),
            ("decode_time_seconds", 0.75),
            ("prefill_kv_computed_tokens", 20),
        ):
            key = f"vllm:request_{name}"
            lines.extend(
                [
                    f"# TYPE {key} histogram",
                    f'{key}_count{{engine="0"}} {self.completed}',
                    f'{key}_sum{{engine="0"}} {self.completed * mean}',
                ]
            )
        return "\n".join(lines)


async def test_synthetic_job_collects_engine_timings_only_for_measured_requests(monkeypatch):
    monkeypatch.setenv("BENCH_TOKEN_INPUT", "1")
    monkeypatch.setenv("BENCH_ISOLATED", "1")
    requests = []
    scrapes = []

    def scrape(*_, **__):
        scrapes.append(len(requests))
        return MetricsResponse(len(requests))

    async def stream(_, url, payload):
        requests.append(payload)
        return runner.Sample(len(payload["prompt"]), payload["max_tokens"], 20, 500, None, [])

    monkeypatch.setattr(runner, "stream", stream)
    session = SimpleNamespace(post=lambda *a, **kw: TokenizerResponse(), get=scrape)
    result = {}
    await runner.run_synthetic(session, "http://isolated", "m", workload(cache_ratio=80), result)
    assert scrapes == [2, 8]  # one model warmup and one prefix prime before timing
    assert result["metrics"]["phase_observation"] == {
        "source": "vllm_request_histograms",
        "completed": 6,
        "prefill_seconds": 0.5,
        "decode_seconds": 0.75,
        "computed_input_tokens": 20,
    }
    monkeypatch.setenv("BENCH_ISOLATED", "0")
    await runner.run_synthetic(session, "http://shared", "m", workload(cache_ratio=80), result)
    assert scrapes == [2, 8] and "phase_observation" not in result["metrics"]


async def test_changed_process_count_mismatch_and_reset_invalidate_measurement():
    snap = await runner.counters(SimpleNamespace(get=lambda *a, **kw: MetricsResponse(10)))
    after = await runner.counters(SimpleNamespace(get=lambda *a, **kw: MetricsResponse(20)))
    assert runner.engine_phase_observation(snap, after, 9) is None
    assert runner.engine_phase_observation(snap, {**after, "process_start": {(): 300}}, 10) is None
    assert runner.engine_phase_observation(snap, {**after, "decode_time_seconds_sum": {}}, 10) is None
    assert runner.engine_phase_observation(after, snap, 10) is None
