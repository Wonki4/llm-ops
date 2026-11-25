"""Compare stored observations with frozen throughput forecasts."""

from copy import deepcopy

import pytest

from app.services.recipe_throughput_history import _has_pinned_weights, calibration_evidence, confirmed_rpm_range


PARAMS = {
    "random_input_len": 100,
    "random_output_len": 12,
    "max_concurrency": 1,
    "cache_ratio": 80,
    "slo": {"ttft_ms": 1000, "min_slo_percent": 90},
}
SCENARIO = {"input_tokens": 100, "output_tokens": 12, "concurrency": 1, "shared_prefix_percent": 80}
FORECAST = {
    "rpm": [30, 80],
    "cache_scenarios": {
        "cold": {"reused_input_tokens": 0, "phase_seconds": {"prefill": [0.5, 1], "decode": [0.8, 1.1]}},
        "warm": {"reused_input_tokens": 80, "phase_seconds": {"prefill": [0.2, 0.4], "decode": [0.8, 1.1]}},
    },
}


def result():
    points = []
    for rate, prefill in ((55, 0.48), (57, 0.52)):
        points.append(
            {
                "step": "confirmation",
                "target_rate": 1,
                "passed": True,
                "completed": 30,
                "all_rates": {"rpm": rate, "input_tpm": rate * 100, "output_tpm": rate * 12},
                "slo_rates": {"rpm": rate - 1, "input_tpm": (rate - 1) * 100, "output_tpm": (rate - 1) * 12},
                "phase_observation": {
                    "source": "vllm_request_histograms",
                    "completed": 30,
                    "computed_input_tokens": 20,
                    "prefill_seconds": prefill,
                    "decode_seconds": 1.0,
                },
            }
        )
    return {
        "capacity_search": {
            "scope": "deployment",
            "confirmed": True,
            "best": {"target_rate": 1, "repetitions": 2},
            "stop_reason": "rate_ceiling",
            "probes": points,
        }
    }


def evidence(run, load=SCENARIO, forecast=FORECAST):
    return calibration_evidence(PARAMS, run, load, run["capacity_search"]["probes"], forecast)


def test_comparison_uses_untuned_frozen_prediction_and_separates_slo_goodput():
    run = result()
    before = deepcopy(FORECAST)
    evidence_row = evidence(run)
    assert FORECAST == before
    assert evidence_row["measured_rpm"] == [55, 57] and evidence_row["forecast_rpm"] == [30, 80]
    assert evidence_row["repetitions"] == 2 and evidence_row["search_stop_reason"] == "rate_ceiling"
    assert evidence_row["slo_rates"] == {"rpm": [54, 56], "input_tpm": [5400, 5600], "output_tpm": [648, 672]}
    assert evidence_row["slo"] == PARAMS["slo"] and evidence_row["cache_scenario"] == "warm"
    assert evidence_row["phases"]["prefill"] == {
        "predicted_seconds": [0.2, 0.4],
        "measured_seconds": [0.48, 0.52],
        "deviation_percent": pytest.approx(25),
    }
    assert evidence_row["phases"]["decode"]["deviation_percent"] == 0
    assert confirmed_rpm_range(PARAMS, run, SCENARIO) == [55, 57]
    assert confirmed_rpm_range(PARAMS, run, {**SCENARIO, "input_tokens": 101}) is None
    assert confirmed_rpm_range(PARAMS, {**run, "input": {"token_input": False}}, SCENARIO) is None
    run["capacity_search"]["probes"][0]["all_rates"]["input_tpm"] += 0.01
    assert confirmed_rpm_range(PARAMS, run, SCENARIO) is None


def test_unmatched_engine_phases_preserve_observations_without_false_error():
    run = result()
    row = evidence(run, {**SCENARIO, "concurrency": 8})
    assert row["phase_reason"] == "concurrent_requests" and row["phases"] is None
    assert row["observed_request_seconds"]["prefill"] == [0.48, 0.52]
    points = run["capacity_search"]["probes"]
    points[0]["phase_observation"]["computed_input_tokens"] = 32
    assert evidence(run)["phase_reason"] == "cache_state_unconfirmed"
    del points[0]["phase_observation"]
    points[0]["p99_ttft_ms"] = 123
    points[0]["p99_tpot_ms"] = 75
    del points[0]["slo_rates"]
    row = evidence(run)
    assert row["phase_reason"] == "phase_not_recorded" and row["observed_request_seconds"] is None
    assert row["slo_rates"] is None and row["measured_rpm"] == [55, 57]


def test_prefix_delta_must_cover_exactly_the_completed_requests_and_zero_is_valid():
    run = result()
    for point in run["capacity_search"]["probes"]:
        del point["phase_observation"]["computed_input_tokens"]
        point["cache"] = {"hits": 0, "queries": 3000}
        point["slo_rates"] = {key: 0.0 for key in point["all_rates"]}
    assert evidence(run)["cache_scenario"] == "cold"
    assert evidence(run)["slo_rates"]["rpm"] == [0, 0]
    run["capacity_search"]["probes"][0]["cache"]["queries"] = 0
    assert evidence(run)["phase_reason"] == "cache_state_unconfirmed"


def test_external_mutable_configuration_invalidates_matching_history():
    digest = "a" * 40
    engine = {"name": "vllm", "args": ["--revision", digest]}
    pod = {"containers": [engine]}
    manifests = [{"kind": "Deployment", "spec": {"template": {"spec": pod}}}]
    pool = {"revision": digest, "engine": "vllm"}
    assert _has_pinned_weights(manifests, pool)
    engine["env"] = [{"name": "FLAGS", "valueFrom": {"configMapKeyRef": {"name": "runtime", "key": "flags"}}}]
    assert not _has_pinned_weights(manifests, pool)
    engine.pop("env")
    engine["args"].append("--code-revision=main")
    assert not _has_pinned_weights(manifests, pool)


def test_calibration_accepts_verified_bundled_heads_and_requires_a_full_external_pin():
    import json

    from app.services.speculative_throughput import draft_plan
    from tests.test_speculative_throughput import EAGLE, MTP, replica

    def match(p, opts):
        pool = {**p, "metadata_source": "pinned_reference", "revision": p["metadata"]["revision"]}
        engine = {"name": "vllm", "args": ["--revision", pool["revision"], "--speculative-config", json.dumps(opts)]}
        manifests = [{"kind": "Deployment", "spec": {"template": {"spec": {"containers": [engine]}}}}]
        return _has_pinned_weights(manifests, pool)
    target = replica(MTP)
    assert match(target, {"method": "mtp", "num_speculative_tokens": 3})
    assert not match(target, {"method": "mtp", "revision": "main", "num_speculative_tokens": 3})
    external = replica(EAGLE)
    opts = json.loads(external["flags"]["speculative-config"])
    assert not match(external, opts)
    assert match(external, {**opts, "revision": draft_plan(external)["revision"]})
