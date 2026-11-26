from datetime import UTC, datetime
from urllib.parse import parse_qs

import httpx
import pytest

from app.services.prometheus_latency import read_latency, read_latency_history

NOW = 1_800_000_000
POD = "engine-abc"


def metric_rows(
    pod=POD, *, stamp=NOW - 5, count=2.0, bounds=("0", "0.1", "+Inf"), p50=0.05, extra=None,
):
    labels = {"pod": pod, "namespace": "serving", "env": "cluster-a", "model_name": "llama", **(extra or {})}

    def row(kind, value, labels):
        return {"metric": {**labels, "portal_latency_kind": kind}, "value": [NOW, str(value)]}

    return [
        row("p50", p50, {}),
        *(row("scrape", stamp, {**labels, "le": bound}) for bound in bounds),
        *(row("bucket", count, {**labels, "le": bound}) for bound in bounds),
    ]


def ok(rows, **extra):
    return httpx.Response(200, json={"status": "success", "data": {"resultType": "vector", "result": rows}, **extra})


async def read(handler, **changes):
    options = dict(namespace="serving", pods=[POD], engine="vllm", cluster_label=("env", "cluster-a"), now=NOW)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await read_latency(client, "https://prom.example/central/", **{**options, **changes})


def query_of(request):
    return parse_qs(request.content.decode())["query"][0]


@pytest.mark.parametrize("engine", ["vllm", "sglang"])
async def test_real_latency_histograms_scoped_and_aggregated_before_p50(engine):
    queries = []

    def handler(request):
        query = query_of(request)
        queries.append(query)
        assert request.method == "POST"
        assert request.url.path == "/central/api/v1/query"
        assert request.headers["Authorization"] == "Bearer test-secret"
        # All selectors (quantile, rates and scrape timestamp) must be scoped identically.
        assert query.count('namespace="serving",pod=~"engine-abc",env="cluster-a",model_name="llama"') == 3
        assert "histogram_quantile(0.5, sum by (le) (rate(" in query
        assert "[5m]" in query
        return ok(metric_rows())

    result = await read(handler, engine=engine, model_name="llama", pods=[POD, POD], bearer_token="test-secret")
    names = ("time_to_first_token_seconds", "e2e_request_latency_seconds", "inter_token_latency_seconds")
    assert all(any(f"{engine}:{name}_bucket" in query for query in queries) for name in names)
    assert len(queries) == 3
    assert result["status"] == "ok"
    assert result["last_scraped_at"] == datetime.fromtimestamp(NOW - 5, UTC).isoformat()
    assert result["metrics"] == {key: {"p50_ms": 50.0, "status": "ok"} for key in ("ttft", "e2e", "itl")}


async def test_escaped_literals_and_fail_closed_empty_scope():
    queries = []

    def handler(request):
        queries.append(query_of(request))
        return ok([])

    await read(handler, cluster_label=("env", 'A" or\n'), pods=["a.b", "x"])
    assert all(r'pod=~"a\\.b|x"' in query and r'env="A\" or\n"' in query for query in queries)
    queries.clear()
    for changes in ({"pods": []}, {"namespace": ""}, {"engine": "unknown"}):
        assert (await read(handler, **changes))["status"] == "missing"
    for invalid_label in ["pod", "le", "model_name", "__name__", 'x="a"}', "portal_latency_kind"]:
        with pytest.raises(ValueError):
            await read(handler, cluster_label=(invalid_label, "A"))
    with pytest.raises(ValueError):
        await read(handler, cluster_label=("env", ""))
    assert not queries


@pytest.mark.parametrize(("rows", "expected", "pods"), [
    ([], "missing", [POD]),
    (metric_rows(count=0, p50="NaN"), "no_samples", [POD]),
    (metric_rows(stamp=NOW - 121), "stale", [POD]),
    (metric_rows(), "partial", [POD, "other"]),
    (metric_rows() + metric_rows(pod="other", stamp=NOW - 121), "partial", [POD, "other"]),
    (metric_rows() + metric_rows(pod="other", bounds=("0.5", "+Inf")), "partial", [POD, "other"]),
    (metric_rows() + metric_rows(extra={"job": "duplicate"}), "partial", [POD]),
    (metric_rows() + metric_rows(extra={"model_name": "another"}), "partial", [POD]),
    (metric_rows()[:2], "partial", [POD]),
    (metric_rows(bounds=("+Inf",)), "partial", [POD]),
    (metric_rows(p50="NaN"), "partial", [POD]),
    (metric_rows(p50=0), "ok", [POD]),
    (metric_rows(extra={"engine": "0"}) + metric_rows(extra={"engine": "1"}), "ok", [POD]),
])
async def test_coverage_freshness_traffic_and_schema(rows, expected, pods):
    result = await read(lambda req: ok(rows), pods=pods)
    assert result["status"] == expected
    assert {metric["status"] for metric in result["metrics"].values()} == {expected}
    if expected != "ok":
        assert all(metric["p50_ms"] is None for metric in result["metrics"].values())


async def test_remote_errors_keep_other_metrics_and_never_expose_upstream_details():
    def handler(request):
        query = query_of(request)
        if "inter_token_latency_seconds" in query:
            return httpx.Response(401, text="upstream-sensitive-value")
        if "e2e_request_latency_seconds" in query:
            return ok(metric_rows(), warnings=["remote_store partial response"])
        return ok(metric_rows())

    result = await read(handler)
    assert result["status"] == "partial"
    assert result["metrics"]["ttft"]["p50_ms"] == 50
    assert result["metrics"]["itl"] == {"status": "error", "p50_ms": None}
    assert result["metrics"]["e2e"] == {"status": "partial", "p50_ms": None}
    assert "sensitive" not in str(result)


async def test_legacy_itl_fallback_used_only_when_modern_histogram_absent():
    queries = []

    def handler(request):
        query = query_of(request)
        queries.append(query)
        return ok([] if "inter_token_latency_seconds" in query else metric_rows())

    assert (await read(handler))["metrics"]["itl"]["p50_ms"] == 50
    assert len(queries) == 4
    assert any("vllm:time_per_output_token_seconds_bucket" in query for query in queries)
    assert all("request_time_per_output_token" not in query for query in queries)


async def test_multiple_namespaces_preserve_exact_pairs_and_distinguish_identically_named_pods():
    additional_targets = [("blue", [POD, "only-blue"]), ("serving", ["only-serving"])]
    rows = (
        metric_rows() + metric_rows("only-serving")
        + metric_rows(extra={"namespace": "blue"}) + metric_rows("only-blue", extra={"namespace": "blue"})
    )

    def handler(request):
        query = query_of(request)
        assert query.count('namespace="blue",pod=~"engine-abc|only-blue",env="cluster-a"') == 3
        assert query.count('namespace="serving",pod=~"engine-abc|only-serving",env="cluster-a"') == 3
        assert 'or rate(' in query and 'or timestamp(' in query
        return ok(rows)

    result = await read(handler, additional_targets=additional_targets)
    assert result["status"] == "ok"
    assert result["metrics"]["ttft"]["p50_ms"] == 50
    # An out-of-scope Pod sharing a permitted name under the wrong namespace cannot enter the aggregate.
    extra = metric_rows("only-blue", extra={"namespace": "serving"})
    result = await read(lambda req: ok(rows + extra), additional_targets=additional_targets)
    assert result["status"] == "error"


async def test_alias_variants_must_be_explicitly_mapped_and_one_model_per_pod():
    rows = metric_rows() + metric_rows("other", extra={"model_name": "alias", "engine": "0"})
    result = await read(lambda req: ok(rows), pods=[POD, "other"])
    assert result["status"] == "partial"
    result = await read(lambda req: ok(rows), pods=[POD, "other"], allow_model_aliases_across_pods=True)
    assert result["status"] == "ok"
    mixed = rows + metric_rows("other", extra={"model_name": "different-adapter", "engine": "1"})
    result = await read(lambda req: ok(mixed), pods=[POD, "other"], allow_model_aliases_across_pods=True)
    assert result["status"] == "partial"


@pytest.mark.parametrize("cluster", ["cluster-b", ""])
async def test_central_result_must_carry_the_requested_cluster_label(cluster):
    # Defense in depth for a federated query proxy which dropped/rewrote labels.
    rows = metric_rows(extra={"env": cluster})
    result = await read(lambda req: ok(rows))
    assert result["status"] == "error"
    assert all(item["p50_ms"] is None for item in result["metrics"].values())


@pytest.mark.parametrize("missing", [True, False])
async def test_removed_or_stale_single_finite_bucket_is_partial_even_when_inf_is_fresh(missing):
    rows = metric_rows()
    stale = next(
        row for row in rows if row["metric"]["portal_latency_kind"] == "scrape" and row["metric"]["le"] == "0.1"
    )
    if missing:
        rows.remove(stale)
    else:
        stale["value"][1] = str(NOW - 121)
    result = await read(lambda req: ok(rows))
    assert result["status"] == "partial"
    assert result["metrics"]["ttft"]["p50_ms"] is None


async def test_prometheus_repairing_non_monotonic_buckets_is_partial():
    result = await read(lambda req: ok(metric_rows(), infos=["input to histogram_quantile fixed for monotonicity"]))
    assert result["status"] == "partial"


def matrix_rowset(frames):
    # Mimic query_range: independent series may omit any step, including all steps.
    series = {}
    for tick, rows in frames.items():
        for row in rows:
            labels = row["metric"]
            ident = tuple(sorted(labels.items()))
            target = series.setdefault(ident, {"metric": labels, "values": []})
            target["values"].append([tick, row["value"][1]])
    return list(series.values())


def matrix(data, **extra):
    return httpx.Response(200, json={"status": "success", "data": {"resultType": "matrix", "result": data}, **extra})


async def history(handler, **changes):
    options = dict(namespace="serving", pods=[POD], engine="vllm", cluster_label=("env", "cluster-a"), now=NOW + 1)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await read_latency_history(client, "https://prom.example/central/", **{**options, **changes})


@pytest.mark.parametrize("second_scrape", [None, "same_mode", "label_dropped"])
async def test_sglang_combines_request_modes_but_rejects_duplicate_scrapes(second_scrape):
    def handler(request):
        query = query_of(request)
        assert "histogram_quantile(0.5, sum by (le) (rate(" in query
        if "sglang:inter_token_latency_seconds" in query:
            rows = metric_rows()
        else:
            # Eight non-streaming requests below 0.1s and eight streaming above it.
            # Only Prometheus emits the population quantile, shared by both histograms.
            rows = metric_rows(p50=0.1, extra={"job": "serving", "is_streaming": "false"})
            streaming = metric_rows(extra={"job": "serving", "is_streaming": "true"})[1:]
            for row in rows[1:] + streaming:
                labels = row["metric"]
                if labels["portal_latency_kind"] == "bucket":
                    in_range = labels["le"] == "0.1" and labels["is_streaming"] == "false"
                    row["value"][1] = "8" if in_range or labels["le"] == "+Inf" else "0"
            rows.extend(streaming)
            if second_scrape:
                for row in streaming:
                    labels = {**row["metric"], "job": "second-job"}
                    if second_scrape == "label_dropped":
                        labels.pop("is_streaming")
                    rows.append({**row, "metric": labels})
        if request.url.path.endswith("/query_range"):
            return matrix(matrix_rowset({NOW: rows}))
        return ok(rows)

    live = await read(handler, engine="sglang")
    series = await history(handler, engine="sglang")
    for metric in ("ttft", "e2e"):
        expected_ms = None if second_scrape else 100
        assert live["metrics"][metric] == {"status": "partial" if second_scrape else "ok", "p50_ms": expected_ms}
        assert series[metric][-1] == [NOW, expected_ms]
    assert live["metrics"]["itl"] == {"status": "ok", "p50_ms": 50}
    assert series["itl"][-1] == [NOW, 50]


async def test_history_uses_actual_prometheus_grid_and_marks_gaps_per_timestamp():
    chunk = matrix_rowset({
        NOW - 150: metric_rows(stamp=NOW - 155, p50=0.01),
        NOW - 120: metric_rows(stamp=NOW - 125, count=0, p50="NaN"),
        NOW - 90: metric_rows(stamp=NOW - 220),
        NOW - 30: metric_rows(stamp=NOW - 35, p50=0),
        NOW: metric_rows(stamp=NOW - 5, p50=0.1),
    })

    def handler(request):
        assert request.url.path == "/central/api/v1/query_range"
        assert request.method == "POST" and request.headers["Authorization"] == "Bearer test-only"
        params = parse_qs(request.content.decode())
        assert (params["start"], params["end"], params["step"]) == ([str(NOW - 1800)], [str(NOW)], ["30"])
        assert params["query"][0].count('namespace="serving",pod=~"engine-abc",env="cluster-a"') == 3
        return matrix(chunk)

    result = await history(handler, engine="sglang", bearer_token="test-only")
    assert set(result) == {"ttft", "e2e", "itl"}
    for points in result.values():
        assert len(points) == 61
        assert points[0] == [NOW - 1800, None]
        assert points[-6:] == [
            [NOW - 150, 10], [NOW - 120, None], [NOW - 90, None], [NOW - 60, None], [NOW - 30, 0], [NOW, 100]
        ]


@pytest.mark.parametrize("gap_rows", [
    metric_rows(extra={"env": "cluster-b"}),
    metric_rows() + [r for r in metric_rows(extra={"job": "dup"}) if r["metric"]["portal_latency_kind"] != "p50"],
    [r for r in metric_rows()
     if not (r["metric"]["portal_latency_kind"] == "bucket" and r["metric"].get("le") == "0.1")],
    metric_rows() + [r for r in metric_rows("other") if r["metric"]["portal_latency_kind"] != "p50"],
])
async def test_suspicious_history_sample_does_not_contaminate_adjacent_time(gap_rows):
    frames = {NOW - 30: metric_rows(stamp=NOW - 35), NOW: gap_rows}
    result = await history(lambda req: matrix(matrix_rowset(frames)), engine="sglang")
    for points in result.values():
        assert points[-2:] == [[NOW - 30, 50], [NOW, None]]


async def test_history_partial_upstream_or_failed_queries_do_not_obscure_other_metrics():
    def handler(request):
        query = query_of(request)
        if "e2e_request_latency" in query:
            return matrix([], warnings=["server-side series limit, truncated"])
        if "inter_token_latency" in query:
            return httpx.Response(503, text="private diagnostics")
        return matrix([])
    result = await history(handler)
    assert result["e2e"] is result["itl"] is None
    assert all(ms is None for _, ms in result["ttft"])


async def test_history_legacy_itl_only_fills_intervals_without_modern_histogram():
    def handler(request):
        query = query_of(request)
        if "time_per_output_token" in query:
            return matrix(matrix_rowset({NOW - 30: metric_rows(stamp=NOW - 31, p50=0.3)}))
        return matrix(matrix_rowset({NOW: metric_rows(p50=0.2)}))
    result = await history(handler)
    assert result["itl"][-2:] == [[NOW - 30, 300.0], [NOW, 200.0]]
    assert result["ttft"][-2:] == [[NOW - 30, None], [NOW, 200.0]]


async def test_history_matrix_byte_budget_fails_closed(monkeypatch):
    monkeypatch.setattr("app.services.prometheus_latency._HISTORY_RESPONSE_BYTES", 70)
    result = await history(lambda req: matrix(matrix_rowset({NOW: metric_rows()})))
    assert all(points is None for points in result.values())


async def test_history_rejects_off_grid_or_out_of_range_samples():
    result = await history(lambda req: matrix(matrix_rowset({NOW - 37: metric_rows()})))
    assert all(points is None for points in result.values())


async def test_large_scope_chunks_time_without_dropping_pods_or_crossing_namespace_pairs():
    chunks = []
    names = ["p" + str(i) for i in range(39)]

    def handler(request):
        params = parse_qs(request.content.decode())
        query = params["query"][0]
        if "time_to_first_token_seconds" in query:
            chunks.append((int(params["start"][0]), int(params["end"][0])))
        assert query.count('namespace="serving",pod=~"engine-abc",env="cluster-a"') == 3
        selector = 'namespace="other",pod=~"' + "|".join(sorted(names)) + '",env="cluster-a"'
        assert query.count(selector) == 3
        assert "limit" not in params
        return matrix([])

    result = await history(handler, engine="sglang", additional_targets=[("other", names)])
    covered = [tick for start, end in chunks for tick in range(start, end + 1, 30)]
    assert len(chunks) > 1
    assert covered == [tick for tick, _ in result["ttft"]]


async def test_legacy_history_failure_preserves_valid_current_format_steps():
    def handler(request):
        if "time_per_output_token_seconds" in query_of(request):
            return httpx.Response(503)
        return matrix(matrix_rowset({NOW: metric_rows(p50=0.2)}))
    result = await history(handler)
    assert result["itl"][-2:] == [[NOW - 30, None], [NOW, 200.0]]


def capped_histogram_rows(pod, finite_count, total, *, stamp=NOW - 5, upper_seconds=10):
    upper = str(upper_seconds)
    rows = metric_rows(pod=pod, stamp=stamp, count=0, p50=upper_seconds, bounds=("0", upper, "+Inf"))
    for row in rows:
        labels = row["metric"]
        if labels["portal_latency_kind"] == "bucket":
            row["value"][1] = str({"0": 0, upper: finite_count, "+Inf": total}[labels["le"]])
    return rows


@pytest.mark.parametrize(("finite", "total", "status"), [(49, 100, "above_range"), (50, 100, "ok"), (51, 100, "ok")])
@pytest.mark.parametrize("upper_seconds", [10, 600])
async def test_classic_histogram_overflow_is_a_lower_bound_only_beyond_half(finite, total, status, upper_seconds):
    result = await read(lambda req: ok(capped_histogram_rows(POD, finite, total, upper_seconds=upper_seconds)))
    expected = {"status": status, "p50_ms": upper_seconds * 1000}
    if status == "above_range":
        expected.update(p50_ms=None, lower_bound_ms=upper_seconds * 1000)
    assert result["metrics"] == {k: expected for k in ("ttft", "e2e", "itl")}


@pytest.mark.parametrize(("normal_traffic", "status"), [(10, "above_range"), (200, "ok")])
async def test_overflow_uses_traffic_weight_across_all_replicas(normal_traffic, status):
    heavy = capped_histogram_rows(POD, 0, 100)
    normal = capped_histogram_rows("other", normal_traffic, normal_traffic)
    result = await read(lambda req: ok(heavy + normal[1:]), pods=[POD, "other"])
    assert result["metrics"]["ttft"]["status"] == status


async def test_range_history_distinguishes_real_median_empty_and_overflow():
    rows = matrix_rowset({
        NOW - 60: capped_histogram_rows(POD, 0, 0, stamp=NOW - 65),
        NOW - 30: capped_histogram_rows(POD, 1, 101, stamp=NOW - 35),
        NOW: capped_histogram_rows(POD, 55, 100),
    })
    result = await history(lambda req: matrix(rows), engine="sglang")
    assert result["ttft"][-3:] == [[NOW - 60, None], [NOW - 30, None, 10_000], [NOW, 10_000]]
