from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api import models_catalog
from app.db.models.custom_external_serving import CustomExternalServing
from app.services import model_live_latency as latency
from app.services.cluster_prometheus import PrometheusConfigError, PrometheusConnection
from app.services.clusters import NamespaceScope
from app.services.pd_serving import LABEL_PD_ROLE

SERVICE = "http://engine.serving.svc.cluster.local/v1"


def registration(alias, registration_id, url):
    return {
        "model_name": alias,
        "model_info": {"id": registration_id},
        "litellm_params": {"model": "openai/chat", "api_base": url},
    }


def external(cid, registration_id):
    return SimpleNamespace(
        cluster_id=cid,
        namespace="serving",
        deployment_name="engine",
        litellm_model_id=registration_id,
        api_base=SERVICE,
    )


def routes():
    return {"paths": [], "services": {"engine": {"app": "engine"}}}


def workloads():
    return {
        "pods": [
            ("p1", {"app": "engine", LABEL_PD_ROLE: "prefill"}, "Running", True, False),
            ("d1", {"app": "engine", LABEL_PD_ROLE: "decode"}, "Running", True, False),
            ("d2", {"app": "engine", LABEL_PD_ROLE: "decode"}, "Running", True, False),
            ("d-old", {"app": "engine", LABEL_PD_ROLE: "decode"}, "Running", True, True),
            ("p-old", {"app": "engine", LABEL_PD_ROLE: "prefill"}, "Failed", False, False),
            ("other", {"app": "unrelated"}, "Running", True, False),
        ],
        "pod_owners": {},
        "pod_engines": {pod: "vllm" for pod in ("p1", "d1", "d2", "d-old", "p-old", "other")},
    }


def wire_clusters(monkeypatch, mock_db, external_rows=(), unavailable_routes=(), broken_connections=()):
    scopes = [NamespaceScope(SimpleNamespace(id=cid, name=f"GPU-{cid}"), "serving") for cid in ("A", "B")]

    def db_rows(query):
        items = external_rows if query.column_descriptions[0]["entity"] is CustomExternalServing else []
        return SimpleNamespace(all=lambda: items)

    def read_routes(scope):
        if scope.cluster_id in unavailable_routes:
            raise RuntimeError("cluster unavailable")
        return routes()

    def connection(db, cid):
        if cid in broken_connections:
            raise PrometheusConfigError("central label missing")
        return PrometheusConnection("http://central", True, "kube_cluster", cid, None)

    async def measure(client, url, *, cluster_label, **options):
        value = 10 if cluster_label == ("kube_cluster", "A") else 200
        return {
            "status": "ok",
            "last_scraped_at": "2026-11-26T00:00:00+00:00",
            "metrics": {key: {"status": "ok", "p50_ms": value} for key in ("ttft", "e2e", "itl")},
        }

    reader = AsyncMock(side_effect=measure)
    mock_db.scalars.side_effect = db_rows
    monkeypatch.setattr(latency, "namespace_scopes", AsyncMock(return_value=scopes))
    monkeypatch.setattr(latency.route_snapshots, "read", AsyncMock(side_effect=read_routes))
    monkeypatch.setattr(latency.workload_snapshots, "read", AsyncMock(return_value=workloads()))
    monkeypatch.setattr(latency, "resolve_prometheus_connection", AsyncMock(side_effect=connection))
    monkeypatch.setattr(latency, "read_latency", reader)
    return reader


async def test_shared_dns_registrations_stay_scoped_and_pd_pools_stay_separate(monkeypatch, mock_db, mock_litellm):
    reader = wire_clusters(monkeypatch, mock_db, [external("A", "a"), external("A", "alias"), external("B", "b")])
    mock_litellm.get_model_info.return_value = [
        registration("org/model", "a", SERVICE),
        registration("org/model", "b", SERVICE),
        registration("another", "alias", SERVICE),
        registration("org/model", "external", "https://api.openai.com/v1"),
    ]
    result = await latency.model_live_latency(mock_db, mock_litellm, "org/model")
    assert result["unmapped_count"] == 1
    assert result["window_seconds"] == 300 and result["refresh_seconds"] == 15
    assert {(r["cluster_id"], r["pool"], r["shared"], r["metrics"]["ttft"]["p50_ms"]) for r in result["rows"]} == {
        ("A", "prefill", True, 10),
        ("A", "decode", True, 10),
        ("B", "prefill", False, 200),
        ("B", "decode", False, 200),
    }
    calls = reader.await_args_list
    assert len(calls) == 4 and len({c.kwargs["now"] for c in calls}) == 1
    assert all(c.args[1] == "http://central" and c.kwargs["namespace"] == "serving" for c in calls)
    assert {(c.kwargs["cluster_label"], tuple(c.kwargs["pods"])) for c in calls} == {
        (("kube_cluster", cid), pods) for cid in ("A", "B") for pods in [("d1", "d2"), ("p1",)]
    }


async def test_stale_registration_after_litellm_edit_does_not_select_the_old_deployment(
    monkeypatch, mock_db, mock_litellm
):
    reader = wire_clusters(monkeypatch, mock_db, [external("B", "b")])
    mock_litellm.get_model_info.return_value = [registration("model", "b", "https://unknown.example/v1")]
    result = await latency.model_live_latency(mock_db, mock_litellm, "model")
    assert result["rows"] == [] and result["unmapped_count"] == 1
    reader.assert_not_awaited()


@pytest.mark.parametrize("owner", [None, "B"])
async def test_partial_route_discovery_never_reassigns_ambiguous_dns_to_the_reachable_cluster(
    monkeypatch, mock_db, mock_litellm, owner
):
    reader = wire_clusters(monkeypatch, mock_db, [external(owner, "b")] if owner else [], unavailable_routes=["B"])
    mock_litellm.get_model_info.return_value = [registration("model", "b", SERVICE)]
    result = await latency.model_live_latency(mock_db, mock_litellm, "model")
    assert all(row["cluster_id"] != "A" for row in result["rows"])
    assert all(call.kwargs["cluster_label"] != ("kube_cluster", "A") for call in reader.await_args_list)


async def test_invalid_central_config_leaves_other_cluster_rows_available(monkeypatch, mock_db, mock_litellm):
    reader = wire_clusters(monkeypatch, mock_db, [external("A", "a"), external("B", "b")], broken_connections=["B"])
    mock_litellm.get_model_info.return_value = [registration("model", cid, SERVICE) for cid in ("a", "b")]
    result = await latency.model_live_latency(mock_db, mock_litellm, "model")
    assert {(r["cluster_id"], r["status"]) for r in result["rows"]} == {("A", "ok"), ("B", "misconfigured")}
    assert all(call.kwargs["cluster_label"] == ("kube_cluster", "A") for call in reader.await_args_list)


@pytest.mark.parametrize("registrations", [[], [registration("model", "cloud", "https://api.openai.com/v1")]])
async def test_unregistered_and_cloud_only_models_skip_topology_reads(mock_db, mock_litellm, registrations):
    mock_litellm.get_model_info.return_value = registrations
    result = await latency.model_live_latency(mock_db, mock_litellm, "model")
    assert result["rows"] == [] and result["unmapped_count"] == len(registrations)
    mock_db.scalars.assert_not_awaited()


@pytest.mark.parametrize("visible", [None, False, True])
@pytest.mark.parametrize("history", [False, True])
async def test_catalog_visibility_gates_live_queries_for_regular_user(
    user_client, mock_db, monkeypatch, visible, history
):
    mock_db.scalar.return_value = visible
    service = AsyncMock(return_value={"rows": []})
    monkeypatch.setattr(models_catalog, "model_live_latency", service)
    path = "/api/models/org%2Fmodel/live-latency"
    response = await user_client.get(path + ("/history" if history else ""))
    if visible:
        assert response.status_code == 200
        assert service.await_args.args[-1] == "org/model"
        assert service.await_args.kwargs == {"history": history}
        assert "no-store" in response.headers["cache-control"]
    else:
        assert response.status_code == 404
        service.assert_not_awaited()


async def test_admin_can_open_model_latency_before_publication(admin_client, mock_db, monkeypatch):
    service = AsyncMock(return_value={"rows": []})
    monkeypatch.setattr(models_catalog, "model_live_latency", service)
    response = await admin_client.get("/api/models/org%2Fmodel/live-latency")
    assert response.status_code == 200
    assert service.await_args.args[-1] == "org/model"
    mock_db.scalar.assert_not_awaited()


async def test_multiple_namespaces_send_exact_namespace_and_pod_pairs(monkeypatch, mock_db, mock_litellm):
    reader = wire_clusters(monkeypatch, mock_db)
    cluster = SimpleNamespace(id="B", name="GPU-B")
    scopes = [NamespaceScope(cluster, ns) for ns in ("blue", "green")]
    monkeypatch.setattr(latency, "namespace_scopes", AsyncMock(return_value=scopes))

    def scoped_workloads(scope):
        pod = f"engine-{scope.namespace}"
        return {
            "pods": [(pod, {"app": "engine"}, "Running", True, False)],
            "pod_engines": {pod: "vllm"},
            "pod_owners": {},
        }

    monkeypatch.setattr(latency.workload_snapshots, "read", AsyncMock(side_effect=scoped_workloads))
    mock_litellm.get_model_info.return_value = [
        registration("model", scope.namespace, f"http://engine.{scope.namespace}.svc/v1") for scope in scopes
    ]
    response = await latency.model_live_latency(mock_db, mock_litellm, "model")
    assert len(response["rows"]) == 1 and response["rows"][0]["namespace"] == "blue, green"
    reader.assert_awaited_once()
    data = reader.await_args.kwargs
    assert [(data["namespace"], data["pods"]), *data["additional_targets"]] == [
        ("blue", ["engine-blue"]),
        ("green", ["engine-green"]),
    ]


def test_registration_with_conflicting_healthy_ingress_does_not_show_old_deployment():
    owner = latency.Target(("B", "serving"), "engine", "deployment")
    scopes = {("A", "serving"): routes(), ("B", "serving"): {"paths": [], "services": {}}}
    aliases, missing = latency._map_registrations(
        [registration("model", "id", SERVICE)], scopes, {"id": [(SERVICE, owner)]}
    )
    assert not aliases["model"] and missing["model"] == 1


def test_managed_registration_with_custom_base_pins_only_its_own_service():
    from app.services.model_deployment_manifests import k8s_resource_names

    dep = SimpleNamespace(
        cluster_id="B",
        namespace="serving",
        model_name="internal",
        engine="vllm",
        serving_mode="aggregated",
        aggregated_router=None,
        litellm_model_id="id",
        router_stack_id=None,
        ingress_host="example.internal",
        ingress_path="/",
    )
    resources = k8s_resource_names(dep)
    host = resources["service"] + ".serving.svc.cluster.local"
    dep.litellm_api_base = f"http://{host}:8000/v1"
    owners, _ = latency._registered_targets([dep], [])
    assert (dep.litellm_api_base, latency.Target(("B", "serving"), resources["deployment"], "deployment")) in owners[
        "id"
    ]
    dep.litellm_api_base = "https://different-cluster.internal/v1"
    owners, _ = latency._registered_targets([dep], [])
    assert all(url != dep.litellm_api_base for url, _ in owners["id"])


async def test_history_preserves_cluster_isolation_and_gaps(monkeypatch, mock_db, mock_litellm):
    current = wire_clusters(monkeypatch, mock_db, [external("A", "a"), external("B", "b")])
    mock_litellm.get_model_info.return_value = [registration("org/model", key, SERVICE) for key in ("a", "b")]

    async def history(client, url, *, cluster_label, **kwargs):
        value = 0 if cluster_label == ("kube_cluster", "A") else 750
        return {"ttft": [(30, value), (60, None)], "e2e": [(30, None), (60, value * 10)], "itl": None}

    reader = AsyncMock(side_effect=history)
    monkeypatch.setattr(latency, "read_latency_history", reader)
    data = await latency.model_live_latency(mock_db, mock_litellm, "org/model", history=True)
    assert data["step_seconds"] == 30 and data["history_seconds"] == 1800
    assert {r["cluster_id"] for r in data["rows"]} == {"A", "B"}
    assert {r["pool"] for r in data["rows"]} == {"prefill", "decode"}
    for row in data["rows"]:
        value = 0 if row["cluster_id"] == "A" else 750
        assert row["unavailable_metrics"] == ["itl"]
        assert row["history"] == [
            {"at": "1970-01-01T00:00:30+00:00", "ttft": value, "e2e": None, "itl": None},
            {"at": "1970-01-01T00:01:00+00:00", "ttft": None, "e2e": value * 10, "itl": None},
        ]
    assert len({c.kwargs["now"] for c in reader.await_args_list}) == 1
    current.assert_not_awaited()


def test_history_row_exposes_lower_bounds_only_at_affected_time_and_metric():
    result = latency._history_row({
        "ttft": [[30, None, 10_000], [60, 0]],
        "e2e": [[30, 22_000], [60, None]],
        "itl": None,
    })
    assert result["unavailable_metrics"] == ["itl"]
    first, second = result["history"]
    assert first["ttft"] is None and first["lower_bounds_ms"] == {"ttft": 10_000}
    assert first["e2e"] == 22_000
    assert second["ttft"] == 0 and second["e2e"] is None and "lower_bounds_ms" not in second
