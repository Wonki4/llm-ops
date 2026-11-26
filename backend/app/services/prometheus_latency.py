"""Live, traffic-weighted P50 from classic serving-engine Prometheus histograms."""

import asyncio
import json
import math
import re
import time
from collections import defaultdict
from datetime import UTC, datetime

import httpx

METRICS = {
    "ttft": "time_to_first_token_seconds",
    "e2e": "e2e_request_latency_seconds",
    "itl": "inter_token_latency_seconds",
}
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


def _scope(namespace: str, pods: set[str], cluster_label: tuple[str, str] | None, model_name: str | None) -> str:
    # The pattern is a union of literal Pod names. Prometheus anchors both ends of =~ matchers.
    pod_union = "|".join(re.escape(pod).replace(r"\-", "-") for pod in sorted(pods))
    selectors = [f'namespace={json.dumps(namespace)}', f'pod=~{json.dumps(pod_union)}']
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
    now: float, allow_model_aliases_across_pods: bool,
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
    # Verify a single scrape series per logical engine/model dimension before using sum().
    logical_engines = set()
    pod_models = defaultdict(set)
    layout = None
    traffic = 0.0
    within_range = 0.0
    for identity, rates in buckets.items():
        labels = dict(identity)
        pod = (labels["namespace"], labels["pod"])
        pod_models[pod].add(labels.get("model_name"))
        logical = (pod, labels.get("engine"), labels.get("engine_index"), labels.get("dp_rank"))
        if logical in logical_engines:
            return _empty("partial"), oldest
        logical_engines.add(logical)
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
        return _interpret(samples, pods, required_labels, now, allow_model_aliases_across_pods)
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

def _history_points(matrix, ticks, expected_pods, required_labels, allow_aliases):
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
        tick: _interpret(values, expected_pods, required_labels, tick, allow_aliases)[0]
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
) -> dict:
    """Return [epoch second, median ms] on a common grid; missing or unsuitable samples are gaps.

    A None series represents a range retrieval failure. All queries use the same
    exact Pod/cluster matchers as the live reading, including legacy metric lookup.
    """
    engine = engine.lower()
    end = int(time.time() if now is None else now) // HISTORY_STEP_SECONDS * HISTORY_STEP_SECONDS
    ticks = range(end - HISTORY_SECONDS, end + 1, HISTORY_STEP_SECONDS)
    selection = _select_scope(engine, namespace, pods, additional_targets, cluster_label, model_name)
    if not selection:
        return {key: [[tick, None] for tick in ticks] for key in METRICS}
    selectors, expected_pods, required_labels = selection
    url = base_url.rstrip("/") + "/api/v1/query_range"
    headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}

    # Split time, not series: all replica buckets must contribute to the same quantile.
    # This bounds each decoded matrix even for models with many replicas.
    chunk_steps = max(1, 2048 // len(expected_pods))

    async def fetch(bucket, times):
        query = _query(bucket, selectors)
        points = {}
        for offset in range(0, len(times), chunk_steps):
            chunk = times[offset:offset + chunk_steps]
            matrix = await _range_matrix(client, url, headers, query, chunk)
            points.update(_history_points(
                matrix, chunk, expected_pods, required_labels, allow_model_aliases_across_pods,
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
    return dict(zip(METRICS, results, strict=True))
