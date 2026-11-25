"""Regression fixtures for per-turn token accounting and verified capacity boundaries."""

import asyncio
import time
from types import SimpleNamespace

import pytest

from app.api.benchmarks import result_with_rates
from app.jobs.reconcile_recipe_validations import measurement_plan
from app.services import portal_bench_runner as runner
from app.services.validation_protocol import ValidationConditions, ValidationCreate, assess

SLO = dict(ttft_ms=100, tpot_ms=None, e2el_ms=None, min_slo_percent=95, max_error_percent=2)
OPTIONS = dict(max_rate=8, max_lag_ms=100, refinements=2, repeats=3)


def sample(ttft=50, inputs=100_000, outputs=1000):
    return runner.Sample(inputs, outputs, ttft, 400, 20, [])


def point(rate, n=60):
    return {
        **runner.summarize([sample()] * n, 600, None, SLO),
        "requested": n,
        "failed": 0,
        "timeouts": 0,
        "target_arrivals_per_sec": rate,
        "actual_arrivals_per_sec": rate,
        "p99_runner_delay_ms": 0,
    }


def test_identical_goodput_set_and_legacy_history():
    metrics = runner.summarize([sample()] * 60 + [sample(300, 5, 12)], 600, None, SLO)
    assert metrics["slo_rates"] == {"rpm": 6, "input_tpm": 600_000, "output_tpm": 6000}
    assert metrics["all_rates"]["rpm"] == 6.1
    assert metrics["all_rates"]["output_tpm"] == 6001.2
    old = {
        "metrics": {
            "completed": 60,
            "total_input_tokens": 6_000_000,
            "total_output_tokens": 60_000,
            "duration": 600,
            "request_goodput": 0.1,
        }
    }
    restored = result_with_rates(old)["metrics"]
    assert restored["all_rates"] == metrics["slo_rates"]
    assert "slo_rates" not in restored and "all_rates" not in old["metrics"]
    assert "all_rates" not in result_with_rates({"metrics": {"duration": 0}})["metrics"]


def test_turns_skips_errors_runner_queue_and_missing_latency_reject_load():
    m = point(2, 50)
    m.update(
        requested=55,
        failed=2,
        skipped=3,
        requested_sessions=20,
        completed_sessions=15,
        actual_arrivals_per_sec=1.5,
        p99_runner_delay_ms=500,
        timeouts=1,
    )
    assert set(runner.check_operating_point(m, SLO, 100)) == {"errors", "latency", "arrival_rate", "runner_delay"}
    assert m["error_percent"] == pytest.approx(100 * 5 / 55)
    incomplete = runner.Sample(2, 1, 1, 2, None, [])
    assert runner.summarize([incomplete], 60, None, {"tpot_ms": 10})["slo_completed"] == 0


async def test_bracket_search_refines_and_confirms_one_point_across_identical_intervals():
    result = {}

    async def measure(rate, index):
        m = point(rate)
        if rate > 5:
            m["p99_runner_delay_ms"] = 300
        return m

    await runner.search_rates({"slo": SLO}, OPTIONS, result, measure, time.monotonic() + 60, "deployment")
    search = result["capacity_search"]
    assert [p["target_rate"] for p in search["probes"]] == [1, 2, 4, 8, 6, 5, 5, 5, 5]
    assert search["stop_reason"] == "bracket_refined" and search["confirmed"]
    assert search["best"]["target_rate"] == 5 and search["best"]["repetitions"] == 3
    assert search["best"]["slo_rates"] == {"rpm": 6, "input_tpm": 600_000, "output_tpm": 6000}
    assert result["metrics"]["completed"] == 180 and result["metrics"]["duration"] == 1800
    assert search["scope"] == "deployment"


async def test_timeout_preserves_single_pass_without_promoting_it():
    result = {}

    async def measure(rate, index):
        if index > 0:
            await asyncio.sleep(1)
        return point(rate)

    await runner.search_rates({"slo": SLO}, OPTIONS, result, measure, time.monotonic() + 0.05, "endpoint")
    search = result["capacity_search"]
    assert search["stop_reason"] == "time_budget" and search["last_passing"]["target_rate"] == 1
    assert search["scope"] == "endpoint" and search["best"] is None and not search["confirmed"]


def test_validation_pins_one_search_per_candidate_requires_confirmations():
    body = ValidationCreate.model_validate(
        {
            "name": "x",
            "request_key": "x",
            "model_name": "alias",
            "conditions": {"ingress_host": "a.test", "performance": {"num_prompts": 60}},
            "candidates": [{"recipe": {}}],
        }
    )
    c = body.conditions.model_dump(mode="json")
    plan = measurement_plan(c)
    assert len(plan) == 1 and plan[0][2]["capacity_search"]["repeats"] == 3
    m = runner.confirmed_metrics([point(1)] * 3)
    best = {"repetitions": 3, "target_rate": 1, "slo_rates": m["slo_rates"]}
    result = {"metrics": m, "capacity_search": {"confirmed": True, "best": best}}
    run = SimpleNamespace(id="test", kind="performance", params=plan[0][2], result=result, status="succeeded")
    assert assess([run], c, 2)["metrics"]["slo_input_tpm"] == 600_000
    result["capacity_search"]["confirmed"] = False
    assert not assess([run], c, 2)["passed"]
    previous = ValidationConditions(version=2, ingress_host="a.test").model_dump(mode="json")
    assert len(measurement_plan(previous)) == 3


def test_retained_capacity_numbers_are_not_rewritten_by_numeric_env_secrets():
    from app.services.validation_runtime import scrub

    original = {"best": {"repetitions": 3, "rpm": 136.0}, "error": "credential is 3"}
    cleaned = scrub(original, {"env": {"SOME_SETTING": "3"}})
    assert cleaned["best"] == original["best"] and cleaned["error"] == "credential is ***"


def test_deployment_measurement_identity_tracks_effective_topology_but_survives_publication():
    from app.services.capacity_identity import deployment_identity

    dep = SimpleNamespace(
        id="dep-A",
        namespace="serving",
        node_selector={"gpu": "h200"},
        replicas=2,
        env={"TOKEN": "sensitive"},
        pd_config={"decode": {"replicas": 2}},
    )
    baseline = deployment_identity(dep)
    dep.validation_state = "published"
    dep.litellm_model_id = "new-id"
    assert deployment_identity(dep) == baseline and "sensitive" not in baseline
    for field, new_value in (
        ("id", "another-candidate-deployment"),
        ("env", {"TOKEN": "another"}),
        ("node_selector", {"gpu": "h100"}),
        ("image", "engine@sha256:" + "a" * 64),
        ("model_path", "provider/other-weights"),
        ("gpu_type", "H100"),
        ("gpu_count", 4),
        ("runtime", "different-runtime"),
        ("router_stack_id", "other-router"),
        ("replicas", 1),
        ("pd_config", {}),
        ("cluster_id", "other"),
        ("engine_args", {"tp": 8}),
    ):
        previous = getattr(dep, field, None)
        setattr(dep, field, new_value)
        assert deployment_identity(dep) != baseline
        setattr(dep, field, previous)


async def test_short_measurements_cover_the_full_offer_window():
    # Two immediate completions at t=0 and t=0.05 should measure 20/s, not 40/s.
    schedule = runner.ArrivalSchedule({"seed": 0, "request_rate": 20, "_search": True})
    await schedule.admit()
    await schedule.admit()
    duration = await schedule.duration()
    assert duration >= 2 / 20
    assert runner.rates(2, 4, 2, duration)["rpm"] <= 1200


async def test_agentic_window_uses_session_arrivals_and_counts_successful_turns(monkeypatch):
    from unittest.mock import AsyncMock

    from app.services.benchmark_workload import PerformanceWorkload

    workload = PerformanceWorkload(
        workload_version=2, workload_type="agentic_replay", num_sessions=2, max_concurrency=2, request_rate=20
    ).model_dump()
    workload.update(_search=True, slo=SLO)
    monkeypatch.setattr(runner, "stream", AsyncMock(return_value=sample()))
    monkeypatch.setattr(runner, "isolated_counters", AsyncMock(return_value=None))
    result = {}
    await runner.run_agentic(None, "http://fixture", "fixture", workload, result)
    metrics = result["metrics"]
    turns = len(workload["scenario"]["turns"])
    assert metrics["requested_sessions"] == metrics["completed_sessions"] == 2
    assert metrics["completed"] == metrics["slo_completed"] == 2 * turns
    assert metrics["duration"] >= 2 / 20  # two session offers, not six turn offers
    assert metrics["slo_rates"]["rpm"] == pytest.approx(2 * turns * 60 / metrics["duration"])
    assert metrics["slo_rates"]["rpm"] <= 20 * 60 * turns
