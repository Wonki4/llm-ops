"""Live, traffic-weighted P50 from classic serving-engine Prometheus histograms."""

import asyncio
import json
import math
import re
import time
from collections import defaultdict
from datetime import UTC, datetime
from itertools import groupby

import httpx

METRICS = {
    "ttft": "time_to_first_token_seconds",
    "e2e": "e2e_request_latency_seconds",
    "itl": "inter_token_latency_seconds",
}
_SGLANG_REQUEST_MODE_BUCKETS = {f"sglang:{METRICS[key]}_bucket" for key in ("ttft", "e2e")}
_REQUEST_MODES = frozenset({"true", "false"})
WINDOW_SECONDS = 300
HISTORY_SECONDS = 1800
HISTORY_STEP_SECONDS = 30
_HISTORY_RESPONSE_BYTES = 8 * 1024 * 1024
STALE_SECONDS = 120
_LABEL_NAME = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")
_SERIES_KIND = "portal_latency_kind"
_RESERVED_LABELS = {"le", "namespace", "pod", "model_name", _SERIES_KIND}


def _empty(status: str) -> dict:
    return {"status": status, "p50_ms": None}


def _names(names) -> str:
    # Both ends of Prometheus regex matchers are anchored. Escape every literal name.
    return json.dumps("|".join(re.escape(name).replace(r"\-", "-") for name in sorted(names)))


def _selector(namespace, matcher, cluster_label, model_name=None) -> str:
    selectors = [f'namespace={json.dumps(namespace)}', matcher]
    if cluster_label is not None:
        label, value = cluster_label
        if not _LABEL_NAME.fullmatch(label) or label.startswith("__") or label in _RESERVED_LABELS or not value:
            raise ValueError("Invalid Prometheus cluster label")
        selectors.append(f"{label}={json.dumps(value, ensure_ascii=False)}")
    if model_name is not None:
        if not model_name:
            raise ValueError("Empty Prometheus model name")
        selectors.append(f"model_name={json.dumps(model_name, ensure_ascii=False)}")
    return ",".join(selectors)


def _scope(namespace: str, pods: set[str], cluster_label: tuple[str, str] | None, model_name: str | None) -> str:
    return _selector(namespace, f'pod=~{_names(pods)}', cluster_label, model_name)


def _owner_query(deployments: dict[str, list[str]], cluster_label: tuple[str, str] | None) -> str:
    """Membership of each Deployment at each evaluation time, including replaced ReplicaSets."""
    pairs = []
    scope = ["namespace", *([cluster_label[0]] if cluster_label else [])]
    rs_key = ",".join([*scope, "replicaset"])
    pod_key = ",".join([*scope, "pod"])
    for namespace, names in sorted(deployments.items()):
        pod = _selector(namespace, 'owner_kind="ReplicaSet",owner_is_controller="true"', cluster_label)
        rs = _selector(namespace, f'owner_kind="Deployment",owner_is_controller="true",owner_name=~{_names(names)}',
                       cluster_label)
        # Deduplicate kube-state-metrics scrape replicas before joining. Keep the actual
        # Deployment name for validating the response, including multi-namespace groups.
        owners = (
            f'group by ({rs_key},pod) (label_replace(kube_pod_owner{{{pod}}} == 1,'
            f' "replicaset", "$1", "owner_name", "(.+)"))'
            f' * on ({rs_key}) group_left(deployment) '
            f'group by ({rs_key},deployment) (label_replace(kube_replicaset_owner{{{rs}}} == 1,'
            f' "deployment", "$1", "owner_name", "(.+)"))'
        )
        # Some installations only collect the two owner gauges. Exclude Pods known to be
        # pending, terminated or deleting when those lifecycle metrics are available.
        inactive = _selector(namespace, 'phase=~"Pending|Succeeded|Failed|Unknown"', cluster_label)
        deleting = _selector(namespace, 'pod!=""', cluster_label)
        deployment = _selector(namespace, f'deployment=~{_names(names)}', cluster_label)
        zero_replicas = (
            f'group by ({",".join([*scope, "deployment"])}) (kube_deployment_status_replicas{{{deployment}}} == 0)'
        )
        pairs.append(
            f'(({owners}) unless on ({pod_key}) (kube_pod_status_phase{{{inactive}}} == 1)'
            f' unless on ({pod_key}) (kube_pod_deletion_timestamp{{{deleting}}} > 0))'
            f' or label_replace({zero_replicas}, "{_SERIES_KIND}", "zero_replicas", "", ".*")'
        )
    return " or ".join(pairs)


def _query(bucket: str, scopes: list[str]) -> str:
    # Select each namespace with *its own* Pod names. Crossing the two lists would
    # introduce unrelated Pods with a matching name in another selected namespace.
    rate = " or ".join(f"rate({bucket}{{{scope}}}[{WINDOW_SECONDS // 60}m])" for scope in scopes)
    p50 = f"histogram_quantile(0.5, sum by (le) ({rate}))"
    latest = " or ".join(f"timestamp({bucket}{{{scope}}})" for scope in scopes)
    # Evaluate quantile, histogram coverage, and real scrape times at the same instant.
    parts = [("p50", p50), ("bucket", rate), ("scrape", latest)]
    return " or ".join(f'label_replace({expr}, "{_SERIES_KIND}", "{kind}", "", ".*")' for kind, expr in parts)


def _interpret(
    samples, expected_pods: set[tuple[str, str]], required_labels: dict[str, str],
    now: float, allow_model_aliases_across_pods: bool, bucket: str,
) -> tuple[dict, float | None]:
    buckets = defaultdict(dict)
    scrapes = defaultdict(dict)
    quantile = None
    for labels, sample in samples:
        kind = labels[_SERIES_KIND]
        value = float(sample)
        if kind == "p50":
            quantile = value
            continue
        if (labels["namespace"], labels["pod"]) not in expected_pods or not math.isfinite(value) or value < 0:
            return _empty("error"), None
        if any(labels.get(name) != expected for name, expected in required_labels.items()):
            return _empty("error"), None
        # Both rate() and timestamp() drop __name__. Match all other series labels.
        identity = tuple(sorted((k, v) for k, v in labels.items() if k not in {_SERIES_KIND, "le", "__name__"}))
        if kind == "scrape":
            if value > now:
                return _empty("error"), None
            scrapes[identity][float(labels["le"])] = value
        elif kind == "bucket":
            buckets[identity][float(labels["le"])] = value
        else:
            return _empty("error"), None
    if not scrapes:
        return _empty("missing"), None
    oldest = min(min(stamps.values()) for stamps in scrapes.values())
    newest = max(max(stamps.values()) for stamps in scrapes.values())
    if now - newest > STALE_SECONDS:
        return _empty("stale"), oldest
    if now - oldest > STALE_SECONDS or set(buckets) != set(scrapes):
        return _empty("partial"), oldest
    # One physical exporter can appear under two jobs/instances in a central Prometheus.
    # Count each engine's observations once, even when scraped by multiple jobs.
    seen_modes = defaultdict(set)
    pod_models = defaultdict(set)
    layout = None
    traffic = 0.0
    within_range = 0.0
    for identity, rates in buckets.items():
        labels = dict(identity)
        pod = (labels["namespace"], labels["pod"])
        pod_models[pod].add(labels.get("model_name"))
        logical = (pod, labels.get("engine"), labels.get("engine_index"), labels.get("dp_rank"))
        # Newer SGLang exposes disjoint TTFT/E2E histograms per request mode.
        # A legacy histogram without this label already covers both modes.
        mode = labels.get("is_streaming") if bucket in _SGLANG_REQUEST_MODE_BUCKETS else None
        if mode is not None and mode not in _REQUEST_MODES:
            return _empty("partial"), oldest
        covered = _REQUEST_MODES if mode is None else {mode}
        if seen_modes[logical].intersection(covered):
            return _empty("partial"), oldest
        seen_modes[logical].update(covered)
        # A removed/delayed finite bucket can still contribute a historical rate even
        # when +Inf is fresh. Verify every bound has both a live scrape and a rate.
        if rates.keys() != scrapes[identity].keys():
            return _empty("partial"), oldest
        bounds = sorted(rates)
        if len(bounds) < 2 or bounds[-1] != math.inf or not all(math.isfinite(b) and b >= 0 for b in bounds[:-1]):
            return _empty("partial"), oldest
        if any(rates[a] > rates[b] and not math.isclose(rates[a], rates[b], rel_tol=1e-12)
               for a, b in zip(bounds, bounds[1:])):
            return _empty("partial"), oldest
        if layout is not None and layout != bounds:
            return _empty("partial"), oldest
        layout = bounds
        traffic += rates[math.inf]
        within_range += rates[bounds[-2]]
    if pod_models.keys() != expected_pods or any(len(models) != 1 for models in pod_models.values()):
        return _empty("partial"), oldest
    if not allow_model_aliases_across_pods and len(set.union(*pod_models.values())) > 1:
        return _empty("partial"), oldest
    if traffic == 0:
        return _empty("no_samples"), oldest
    if not math.isfinite(traffic) or quantile is None or not math.isfinite(quantile * 1000) or quantile < 0:
        return _empty("partial"), oldest
    # For a rank in +Inf, histogram_quantile returns the last finite boundary.
    # The cumulative rate at that boundary establishes only a strict lower bound.
    if within_range < 0.5 * traffic:
        lower_bound = layout[-2] * 1000
        if not math.isfinite(lower_bound):
            return _empty("partial"), oldest
        return {"status": "above_range", "p50_ms": None, "lower_bound_ms": lower_bound}, oldest
    return {"status": "ok", "p50_ms": quantile * 1000}, oldest


async def _read_metric(
    client: httpx.AsyncClient, url: str, headers: dict, bucket: str, scopes: list[str],
    pods: set[tuple[str, str]], required_labels: dict[str, str], now: float, allow_model_aliases_across_pods: bool,
) -> tuple[dict, float | None]:
    try:
        result = await client.post(
            url, data={"query": _query(bucket, scopes), "time": str(now), "timeout": "4s"},
            headers=headers, timeout=5.0, follow_redirects=False,
        )
        result.raise_for_status()
        payload = result.json()
        if payload["status"] != "success" or payload["data"]["resultType"] != "vector":
            return _empty("error"), None
        if payload.get("warnings") or any("monotonicity" in info for info in payload.get("infos", [])):
            return _empty("partial"), None
        rows = payload["data"]["result"]
        if not isinstance(rows, list):
            return _empty("error"), None
        samples = ((row["metric"], row["value"][1]) for row in rows)
        return _interpret(samples, pods, required_labels, now, allow_model_aliases_across_pods, bucket)
    except (httpx.HTTPError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        # Do not return the upstream URL, authentication headers, or response body.
        return _empty("error"), None


def _select_scope(engine, namespace, pods, additional_targets, cluster_label, model_name):
    targets = [(namespace, pods), *(additional_targets or ())]
    if engine not in {"vllm", "sglang"} or any(not ns or not names or "" in names for ns, names in targets):
        return None
    groups = defaultdict(set)
    for ns, names in targets:
        groups[ns].update(names)
    selectors = [_scope(ns, groups[ns], cluster_label, model_name) for ns in sorted(groups)]
    expected_pods = {(ns, pod) for ns, names in groups.items() for pod in names}
    required_labels = dict([cluster_label]) if cluster_label else {}
    if model_name:
        required_labels["model_name"] = model_name
    return selectors, expected_pods, required_labels


async def read_latency(
    client: httpx.AsyncClient,
    base_url: str,
    *,
    namespace: str,
    pods: list[str],
    engine: str,
    cluster_label: tuple[str, str] | None = None,
    bearer_token: str | None = None,
    now: float | None = None,
    model_name: str | None = None,
    additional_targets: list[tuple[str, list[str]]] | None = None,
    allow_model_aliases_across_pods: bool = False,
) -> dict:
    """One result per metric. All replicas belong to the same engine and serving target."""
    now = time.time() if now is None else now
    engine = engine.lower()
    selection = _select_scope(engine, namespace, pods, additional_targets, cluster_label, model_name)
    if not selection:
        return {"status": "missing", "last_scraped_at": None, "metrics": {key: _empty("missing") for key in METRICS}}
    scopes, expected_pods, required_labels = selection
    url = base_url.rstrip("/") + "/api/v1/query"
    headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}

    async def fetch(key: str) -> tuple[dict, float | None]:
        result = await _read_metric(
            client, url, headers, f"{engine}:{METRICS[key]}_bucket", scopes, expected_pods, required_labels, now,
            allow_model_aliases_across_pods,
        )
        if key == "itl" and engine == "vllm" and result[0]["status"] == "missing":
            # Older vLLM used this name for inter-token latency. Do not use request-level TPOT.
            return await _read_metric(
                client, url, headers, "vllm:time_per_output_token_seconds_bucket", scopes, expected_pods,
                required_labels, now, allow_model_aliases_across_pods,
            )
        return result

    results = await asyncio.gather(*(fetch(key) for key in METRICS))
    metrics = {key: value for key, (value, _) in zip(METRICS, results, strict=True)}
    statuses = {metric["status"] for metric in metrics.values()}
    timestamps = [stamp for _, stamp in results if stamp is not None]
    return {
        "status": next(iter(statuses)) if len(statuses) == 1 else "partial",
        "last_scraped_at": datetime.fromtimestamp(min(timestamps), UTC).isoformat() if timestamps else None,
        "metrics": metrics,
    }

def _history_points(matrix, ticks, expected_pods, required_labels, allow_aliases, bucket):
    # Reuse the instant-query checks at each evaluation time, including the timestamp
    # of the actual scrape. A matrix sample's outer timestamp is only its evaluation time.
    samples = {tick: [] for tick in ticks}
    for series in matrix:
        labels = series["metric"]
        previous = -1
        for tick, value in series["values"]:
            if tick not in samples or tick <= previous:
                raise ValueError("Unexpected range query timestamp")
            samples[tick].append((labels, value))
            previous = tick
    return {
        tick: _interpret(values, expected_pods, required_labels, tick, allow_aliases, bucket)[0]
        for tick, values in samples.items()
    }


async def _range_matrix(client, url, headers, query, ticks):
    params = {"query": query, "start": ticks[0], "end": ticks[-1], "step": HISTORY_STEP_SECONDS, "timeout": "6s"}
    async with client.stream("POST", url, data=params, headers=headers, timeout=7, follow_redirects=False) as response:
        response.raise_for_status()
        body = bytearray()
        async for chunk in response.aiter_bytes():
            if len(body) + len(chunk) > _HISTORY_RESPONSE_BYTES:
                raise ValueError("Prometheus range response exceeded the byte budget")
            body.extend(chunk)
    payload = json.loads(body)
    if payload["status"] != "success" or payload["data"]["resultType"] != "matrix":
        raise ValueError("Unexpected range query result")
    if payload.get("warnings") or any("monotonicity" in info for info in payload.get("infos", [])):
        raise ValueError("Partial range query result")
    matrix = payload["data"]["result"]
    if not isinstance(matrix, list):
        raise ValueError("Unexpected range query series")
    return matrix


def _target_query(targets, cluster_label, *, dimension):
    # up=0 still identifies an expected scrape target. An unhealthy replica must
    # be counted in coverage checks even when its latency samples are unavailable.
    dimensions = ["namespace", "pod"]
    if dimension == "deployment":
        dimensions.append("deployment")
    if cluster_label:
        dimensions.append(cluster_label[0])
    group = ",".join(dimensions)
    selectors = []
    for namespace, names in sorted(targets.items()):
        matcher = f'{dimension}=~{_names(names)}'
        if dimension == "deployment":
            matcher += ',pod!=""'
        selectors.append(_selector(namespace, matcher, cluster_label))
    return f"group by ({group}) (" + " or ".join(f"up{{{s}}}" for s in selectors) + ")"


def _membership_at_ticks(matrix, targets, cluster_label, ticks, dimension):
    pods = {tick: set() for tick in ticks}
    deployments_by_tick = defaultdict(set)
    seen = {}
    for series in matrix:
        labels = series["metric"]
        ns, target = labels["namespace"], labels[dimension]
        zero = dimension == "deployment" and labels.get(_SERIES_KIND) == "zero_replicas"
        pod = labels.get("pod")
        if (not zero and not pod) or target not in targets.get(ns, ()) or (
            cluster_label and labels.get(cluster_label[0]) != cluster_label[1]
        ):
            raise ValueError("Out-of-scope Deployment membership")
        previous = -1
        for tick, value in series["values"]:
            if tick not in pods or tick <= previous or float(value) != 1:
                raise ValueError("Invalid Deployment membership sample")
            previous = tick
            if dimension == "deployment":
                deployments_by_tick[tick].add((ns, target))
            if zero:
                continue
            key = (tick, ns, pod)
            if key in seen and seen[key] != target:
                raise ValueError("Ambiguous Pod owner at evaluation time")
            seen[key] = target
            pods[tick].add((ns, pod))
    if dimension == "deployment":
        expected = {(ns, name) for ns, names in targets.items() for name in names}
        if expected != set().union(*deployments_by_tick.values()):
            # The source returned only some requested Deployments. Falling back to another
            # selector or presenting just the visible subset could misattribute traffic.
            raise ValueError("Incomplete Deployment membership")
        for tick in ticks:
            if deployments_by_tick[tick] != expected:
                # A missing Deployment could be at zero replicas or its exporter could
                # be unavailable. Without population evidence the combined value is unknown.
                pods[tick].clear()
    return {tick: frozenset(names) for tick, names in pods.items()}


async def _deployment_membership(client, url, headers, deployments, cluster_label, ticks, observed_pods):
    # A custom cluster dimension may itself be named "deployment" or "replicaset".
    # Only try membership sources whose own dimensions do not overwrite the cluster filter.
    cluster_key = cluster_label[0] if cluster_label else None
    queries = []
    if cluster_key not in {"owner_kind", "owner_is_controller", "owner_name", "replicaset", "deployment", "phase"}:
        queries.append(("deployment_owner", _owner_query(deployments, cluster_label), deployments, "deployment"))
    if cluster_key != "deployment":
        deployment_targets = _target_query(deployments, cluster_label, dimension="deployment")
        queries.append(("deployment_target", deployment_targets, deployments, "deployment"))
    if observed_pods:
        pod_targets = _target_query(observed_pods, cluster_label, dimension="pod")
        queries.append(("observed_pods", pod_targets, observed_pods, "pod"))
    for source, query, allowed, dimension in queries:
        matrix = await _range_matrix(client, url, headers, query, ticks)
        if matrix:
            return source, _membership_at_ticks(matrix, allowed, cluster_label, ticks, dimension)
    return "current_pods", None


async def read_latency_history(
    client: httpx.AsyncClient,
    base_url: str,
    *,
    namespace: str,
    pods: list[str],
    engine: str,
    cluster_label: tuple[str, str] | None = None,
    bearer_token: str | None = None,
    now: float | None = None,
    model_name: str | None = None,
    additional_targets: list[tuple[str, list[str]]] | None = None,
    allow_model_aliases_across_pods: bool = False,
    deployments: dict[str, list[str]] | None = None,
    observed_pods: dict[str, list[str]] | None = None,
) -> dict:
    """Return [epoch second, median ms] on a common grid; missing or unsuitable samples are gaps.

    A None series represents a range retrieval failure. All queries use the same
    exact Pod/cluster matchers as the live reading, including legacy metric lookup.
    For a confirmed whole Deployment/pool, resolve the Pod roster at each historical
    tick. Adjacent ticks with the same roster share a range query.
    """
    engine = engine.lower()
    end = int(time.time() if now is None else now) // HISTORY_STEP_SECONDS * HISTORY_STEP_SECONDS
    ticks = range(end - HISTORY_SECONDS, end + 1, HISTORY_STEP_SECONDS)
    selection = _select_scope(engine, namespace, pods, additional_targets, cluster_label, model_name)
    url = base_url.rstrip("/") + "/api/v1/query_range"
    headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}
    source, roster = "current_pods", None
    if deployments and engine in {"vllm", "sglang"}:
        if any(not ns or not names or not all(names) for ns, names in deployments.items()):
            raise ValueError("Empty Deployment scope")
        if any(ns not in deployments or not names or not all(names) for ns, names in (observed_pods or {}).items()):
            raise ValueError("Invalid previously observed Pod scope")
        try:
            async with asyncio.timeout(12):
                source, roster = await _deployment_membership(
                    client, url, headers, deployments, cluster_label, ticks, observed_pods,
                )
        except (httpx.HTTPError, KeyError, TypeError, ValueError, IndexError, OverflowError, TimeoutError):
            # A partial or invalid ownership response cannot authorize a wider selector.
            return {**dict.fromkeys(METRICS), "scope_source": "unavailable"}
    if selection:
        _, current_pods, required_labels = selection
    else:
        current_pods = set()
        required_labels = dict([cluster_label]) if cluster_label else {}
        if model_name is not None:
            required_labels["model_name"] = model_name
    if roster is None:
        roster = dict.fromkeys(ticks, frozenset(current_pods))
    elif roster[ticks[-1]] != current_pods:
        # The final grid point can straddle a rollout or a delayed owner scrape.
        # Preserve earlier observed membership; avoid showing an incomplete latest aggregate.
        roster[ticks[-1]] = frozenset()

    async def fetch(bucket, times):
        points = {}
        for names, interval in groupby(times, key=roster.__getitem__):
            interval = list(interval)
            if not names:
                points.update(dict.fromkeys(interval, _empty("missing")))
                continue
            namespaces = defaultdict(set)
            for ns, pod in names:
                namespaces[ns].add(pod)
            selectors = [_scope(ns, namespaces[ns], cluster_label, model_name) for ns in sorted(namespaces)]
            query = _query(bucket, selectors)
            # Split time, not series: combine all replica buckets for each quantile.
            chunk_steps = max(1, 2048 // len(names))
            for offset in range(0, len(interval), chunk_steps):
                chunk = interval[offset:offset + chunk_steps]
                matrix = await _range_matrix(client, url, headers, query, chunk)
                points.update(_history_points(
                    matrix, chunk, names, required_labels, allow_model_aliases_across_pods, bucket,
                ))
        return points

    async def metric(key):
        try:
            async with asyncio.timeout(16):
                points = await fetch(f"{engine}:{METRICS[key]}_bucket", ticks)
                if engine == "vllm" and key == "itl":
                    missing = [tick for tick in ticks if points[tick]["status"] == "missing"]
                    if missing:
                        # Keep valid newer-format readings across an engine upgrade.
                        span = range(missing[0], missing[-1] + 1, HISTORY_STEP_SECONDS)
                        try:
                            legacy = await fetch("vllm:time_per_output_token_seconds_bucket", span)
                        except (httpx.HTTPError, KeyError, TypeError, ValueError, IndexError, OverflowError):
                            if len(missing) == len(ticks):
                                raise
                        else:
                            for tick in missing:
                                points[tick] = legacy[tick]
                history = []
                for tick in ticks:
                    point = points[tick]
                    sample = [tick, point["p50_ms"]]
                    if point["status"] == "above_range":
                        sample.append(point["lower_bound_ms"])
                    history.append(sample)
                return history
        except (httpx.HTTPError, KeyError, TypeError, ValueError, IndexError, OverflowError, TimeoutError):
            return None

    results = await asyncio.gather(*(metric(key) for key in METRICS))
    output = dict(zip(METRICS, results, strict=True))
    if deployments:
        output["scope_source"] = source
    return output
