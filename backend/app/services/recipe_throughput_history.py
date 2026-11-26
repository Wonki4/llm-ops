"""Use repeated, local deployment benchmarks only when the recipe and workload still match."""

import math
import re
from statistics import median

from sqlalchemy import select

from app.db.models.custom_benchmark_run import CustomBenchmarkRun as Run
from app.db.models.custom_model_deployment import CustomModelDeployment as Deployment
from app.services.kv_capacity import final_flags
from app.services.kv_metadata import uses_public_hub
from app.services.speculative_throughput import DraftEvidenceError, draft_plan, speculative_options


def _number(value):
    return type(value) in (float, int) and math.isfinite(value)


def _confirmed_points(params, result, workload):
    recorded_input = (result or {}).get("input") or {}
    if (
        params.get("workload_type", "synthetic") != "synthetic"
        or params.get("input_length_mode", "tokenizer") != "tokenizer"
        # Averages from retokenized chat text can mask per-request length drift.
        or recorded_input.get("token_input") is False
        or params.get("random_input_len") != workload["input_tokens"]
        or params.get("random_output_len") != workload["output_tokens"]
        or params.get("max_concurrency") != workload["concurrency"]
        or params.get("cache_ratio", 0) != workload["shared_prefix_percent"]
    ):
        return None
    search = (result or {}).get("capacity_search") or {}
    best = search.get("best") or {}
    target_rate = best.get("target_rate")
    if (
        search.get("confirmed") is not True
        or search.get("scope") != "deployment"
        or not _number(target_rate)
        or target_rate <= 0
    ):
        return None
    confirmed = []
    for point in search.get("probes") or []:
        if point.get("step") != "confirmation" or point.get("target_rate") != best["target_rate"]:
            continue
        if (
            point.get("passed") is not True
            or not _number(point.get("completed"))
            or point["completed"] < workload["concurrency"]
        ):
            return None
        values = point.get("all_rates") or {}
        rpm = values.get("rpm")
        if not _number(rpm) or rpm <= 0:
            return None
        # Compare actual served lengths, not just the requested generation cap.
        for key, length in (("input_tpm", workload["input_tokens"]), ("output_tpm", workload["output_tokens"])):
            total = values.get(key)
            if not _number(total) or not math.isclose(total / rpm, length, rel_tol=1e-12, abs_tol=1e-8):
                return None
        confirmed.append(point)
    if len(confirmed) >= 2 and len(confirmed) == best.get("repetitions"):
        return confirmed
    return None


def _range(values):
    return [min(values), max(values)]


def confirmed_rpm_range(params, result, workload):
    points = _confirmed_points(params, result, workload)
    return _range([p["all_rates"]["rpm"] for p in points]) if points else None


def _matching_cache_case(forecast, point, observation, inputs):
    # A configured shared fraction does not prove the prefix was resident. The
    # request histogram counts actually computed prefill tokens; older engines
    # only expose prefix-cache counters. Both cover the same measured requests.
    computed = observation.get("computed_input_tokens")
    if _number(computed) and 0 <= computed <= inputs:
        reused = inputs - computed
    else:
        cache = point.get("cache") or {}
        if cache.get("queries") != inputs * point["completed"] or not _number(cache.get("hits")):
            return None
        reused = cache["hits"] / point["completed"]
    for name, case in (forecast.get("cache_scenarios") or {}).items():
        if _number(case.get("reused_input_tokens")) and math.isclose(reused, case["reused_input_tokens"], abs_tol=1e-6):
            return name
    return None


def _phase_samples(points):
    samples = []
    for point in points:
        sample = point.get("phase_observation") or {}
        if (
            sample.get("source") != "vllm_request_histograms"
            or sample.get("completed") != point["completed"]
            or not all(
                _number(sample.get(f"{p}_seconds")) and sample[f"{p}_seconds"] >= 0 for p in ("prefill", "decode")
            )
        ):
            return []
        samples.append(sample)
    return samples


def _phase_comparison(forecast, points, samples, workload):
    if not forecast or not forecast.get("cache_scenarios"):
        return None, None, "analytical_timing_unavailable"
    if workload["concurrency"] != 1:
        # The predictor models the full simultaneous batch. A per-request
        # duration in a continuously batched run cannot identify that makespan.
        return None, None, "concurrent_requests"
    if not samples:
        return None, None, "phase_not_recorded"
    states = {
        _matching_cache_case(forecast, p, s, workload["input_tokens"]) for p, s in zip(points, samples, strict=True)
    }
    if len(states) != 1 or None in states:
        return None, None, "cache_state_unconfirmed"
    state = states.pop()
    case = forecast["cache_scenarios"][state]
    phases = {}
    for phase in ("prefill", "decode"):
        bounds = (case.get("phase_seconds") or {}).get(phase) or []
        if len(bounds) != 2 or not all(_number(n) for n in bounds) or not 0 <= bounds[0] <= bounds[1]:
            return None, state, "analytical_timing_unavailable"
        values = [s[f"{phase}_seconds"] for s in samples]
        typical = median(values)
        nearest = max(bounds[0], min(bounds[1], typical))
        phases[phase] = {
            "predicted_seconds": bounds,
            "measured_seconds": _range(values),
            "deviation_percent": (100 * (typical / nearest - 1) if nearest else None) if nearest != typical else 0,
        }
    return phases, state, None


def calibration_evidence(params, result, workload, points, forecast):
    search = result["capacity_search"]
    rates = {}
    for metric in ("rpm", "input_tpm", "output_tpm"):
        values = [(p.get("slo_rates") or {}).get(metric) for p in points]
        if any(not _number(v) or not 0 <= v <= p["all_rates"][metric] for v, p in zip(values, points, strict=True)):
            rates = None
            break
        rates[metric] = _range(values)
    slo = {
        k: v
        for k, v in (params.get("slo") or {}).items()
        if k in {"ttft_ms", "tpot_ms", "e2el_ms", "max_error_percent", "min_slo_percent"} and _number(v)
    }
    samples = _phase_samples(points)
    phases, state, reason = _phase_comparison(forecast, points, samples, workload)
    measured_seconds = (
        {p: _range([s[f"{p}_seconds"] for s in samples]) for p in ("prefill", "decode")} if samples else None
    )
    return {
        "repetitions": len(points),
        "forecast_rpm": (forecast or {}).get("rpm"),
        "forecast_version": (forecast or {}).get("version"),
        "measured_rpm": _range([p["all_rates"]["rpm"] for p in points]),
        "slo_rates": rates,
        "slo": slo or None,
        "phases": phases,
        "phase_reason": reason,
        "cache_scenario": state,
        "observed_request_seconds": measured_seconds,
        "target_requests_per_second": search["best"]["target_rate"],
        "search_stop_reason": search.get("stop_reason"),
    }


def _has_pinned_weights(manifests, pool):
    """A resolved commit alone does not pin a deployment launched against a moving branch."""
    for doc in manifests:
        if doc.get("kind") != "Deployment":
            continue
        pod = doc["spec"]["template"]["spec"]
        for container in pod["containers"]:
            if container["name"] != pool["engine"] or not uses_public_hub(pod, container):
                continue
            if any("valueFrom" in ref for ref in container.get("env", [])):
                return False
            mounted = {m["name"] for m in container.get("volumeMounts", [])}
            if any(
                v["name"] in mounted and any(k in v for k in ("secret", "configMap", "projected", "csi"))
                for v in pod.get("volumes", [])
            ):
                return False
            flags = final_flags(container["args"])
            if flags.get("revision") != pool["revision"]:
                return False
            # A separately configured tokenizer/code revision can move while
            # the base weights and image stay pinned.
            for key in ("tokenizer-revision", "code-revision"):
                if key in flags and not re.fullmatch(r"[a-f0-9]{40}", flags[key]):
                    return False
            if "tokenizer" in flags and ("tokenizer-revision" not in flags or flags["tokenizer"].startswith("/")):
                return False
            draft = pool.get("speculation") or {}
            has_speculation = draft or any("speculative" in key or key.startswith("draft-model") for key in flags)
            if not has_speculation or pool.get("integrated_speculation"):
                return True
            # Bundled MTP/DFly/DSpark heads can be pinned by the target commit. A
            # separate EAGLE/DSpark checkpoint must also be pinned by the launch.
            opts = speculative_options(flags) or {}
            external = opts.get("model")
            if external and external != pool.get("model_path") and (
                not isinstance(opts.get("revision"), str) or not re.fullmatch(r"[a-f0-9]{40}", opts["revision"])
            ):
                return False
            if draft:
                return bool(
                    not draft.get("assumed_revision", True)
                    and re.fullmatch(r"[a-f0-9]{40}", draft.get("revision") or "")
                    and not (draft.get("draft_model") or "").startswith("/")
                )
            try:
                plan = draft_plan({
                    **pool, "flags": flags, "public_hub": True,
                    "metadata": {"source": pool.get("metadata_source"), "revision": pool["revision"]},
                })
            except DraftEvidenceError:
                return False
            return bool(plan and not plan["assumed_revision"])

    return False


async def matching_recipe_benchmark(db, recipe_id, target, pools, workload, manifests, forecast=None):
    if len(pools) != 1:
        return None
    pool = pools[0]
    if (
        pool["role"] != "aggregated"
        or pool["replicas"] != 1
        or not target.gpu_type
        or pool.get("gpu_profile_resolved") is False
        or pool.get("metadata_source") not in {"hub", "pinned_reference"}
        or not re.search(r"@sha256:[a-f0-9]{64}$", pool.get("image") or "")
        or not re.fullmatch(r"[a-f0-9]{40}", pool.get("revision") or "")
        or not _has_pinned_weights(manifests, pool)
    ):
        return None
    try:
        from app.services.capacity_identity import deployment_identity
    except ModuleNotFoundError as exc:
        if exc.name != "app.services.capacity_identity":
            raise
        return None
    from app.api.benchmarks import _serving_snapshot

    rows = await db.execute(
        select(Run, Deployment)
        .join(Deployment, Run.deployment_id == Deployment.id)
        .where(
            Deployment.recipe_id == recipe_id,
            Deployment.cluster_id == target.cluster_id,
            Deployment.namespace == target.namespace,
            Run.cluster_id == target.cluster_id,
            Run.k8s_namespace == target.namespace,
            Run.status == "succeeded",
            Run.kind == "performance",
            Run.validation_attempt_id == Deployment.validation_attempt_id,
            Run.validation_id == Deployment.validation_id,
            Run.params.has_key("_capacity_identity"),
            Run.params.contains(
                {
                    "random_input_len": workload["input_tokens"],
                    "random_output_len": workload["output_tokens"],
                    "max_concurrency": workload["concurrency"],
                }
            ),
        )
        .order_by(Run.finished_at.desc())
        .limit(32)
    )
    snapshot = _serving_snapshot(target)
    for run, dep in rows:
        if (
            run.params["_capacity_identity"] != deployment_identity(dep)
            or dep.gpu_type != target.gpu_type
            or dep.runtime != target.runtime
            or any(
                getattr(dep, key, None) != getattr(target, key, None)
                for key in ("aggregated_router", "router_stack_id", "common_labels", "common_annotations")
            )
            or dep.tolerations != target.tolerations
            or _serving_snapshot(dep) != snapshot
        ):
            continue
        points = _confirmed_points(run.params, run.result, workload)
        if points:
            calibration = calibration_evidence(run.params, run.result, workload, points, forecast)
            rates = calibration["measured_rpm"]
            return {
                "status": "estimated",
                "reason": None,
                "method": "historical",
                "scope": "replica",
                "rpm": rates,
                "input_tpm": _range([p["all_rates"]["input_tpm"] for p in points]),
                "output_tpm": _range([p["all_rates"]["output_tpm"] for p in points]),
                "assumptions": ["matching_benchmark"],
                "sources": [],
                "benchmark_id": str(run.id),
                "calibration": calibration,
            }
    return None
