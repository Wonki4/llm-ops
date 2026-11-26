import json
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
    mock_db.scalar.return_value = "revision" if visible else None
    service = AsyncMock(return_value={"rows": []})
    monkeypatch.setattr(models_catalog, "model_live_latency", service)
    path = "/api/models/org%2Fmodel/live-latency"
    response = await user_client.get(path + ("/history" if history else ""))
    if visible:
        assert response.status_code == 200
        assert service.await_args.args[-1] == "org/model"
        assert service.await_args.kwargs == {"history": history, "config_epoch": "revision"}
        assert "no-store" in response.headers["cache-control"]
    else:
        assert response.status_code == 404
        service.assert_not_awaited()


async def test_admin_can_open_model_latency_before_publication(admin_client, mock_db, monkeypatch):
    mock_db.scalar.return_value = "revision"
    service = AsyncMock(return_value={"rows": []})
    monkeypatch.setattr(models_catalog, "model_live_latency", service)
    response = await admin_client.get("/api/models/org%2Fmodel/live-latency")
    assert response.status_code == 200
    assert service.await_args.args[-1] == "org/model"
    assert service.await_args.kwargs["config_epoch"] == "revision"
    assert "custom_model_catalog" not in str(mock_db.scalar.await_args.args[0])


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
        id="deployment-id",
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


def _revision(engine, selector, labels, uid="chat-uid"):
    return {"uid": uid, "engine": engine, "selector": selector, "labels": labels}


@pytest.mark.parametrize("live_pods", [[], ["new-1", "new-2", "new-3"]])
async def test_history_keeps_owned_deployment_across_scale_and_at_zero(monkeypatch, mock_db, mock_litellm, live_pods):
    wire_clusters(monkeypatch, mock_db, [external("A", "a")])
    mock_litellm.get_model_info.return_value = [registration("model", "a", SERVICE)]
    selector = {"app": "engine"}
    snapshot = {
        "pods": [(p, selector, "Running", True, False) for p in live_pods],
        "pod_engines": dict.fromkeys(live_pods, "sglang"),
        "pod_owners": {p: {"deployment": ("chat-uid", "chat")} for p in live_pods},
        "deployment_revisions": {
            "chat": [_revision("sglang", selector, {**selector, "version": v}) for v in ["old", "new"]],
            "chat-canary": [_revision("sglang", {"app": "canary"}, {"app": "canary"}, "other-uid")],
        },
    }
    monkeypatch.setattr(latency.workload_snapshots, "read", AsyncMock(return_value=snapshot))
    history = AsyncMock(return_value={k: [] for k in latency.METRICS} | {"scope_source": "deployment_owner"})
    monkeypatch.setattr(latency, "read_latency_history", history)
    response = await latency.model_live_latency(mock_db, mock_litellm, "model", history=True)
    row, = response["rows"]
    assert (row["deployment_name"], row["engine"], row["scope_source"]) == ("chat", "sglang", "deployment_owner")
    assert history.await_args.kwargs["deployments"] == {"serving": ["chat"]}
    assert history.await_args.kwargs["pods"] == live_pods


def test_release_specific_service_never_expands_to_sibling_revisions():
    target = latency.Target(("A", "serving"), "preview", "service")
    snapshots = {target.scope: {"services": {"preview": {"release": "canary"}}}}
    # Even if all retained replicas match, release is mutable across revisions.
    data = {"deployment_revisions": {
        "chat": [_revision("vllm", {"app": "chat"}, {"app": "chat", "release": "canary"})],
    }}
    assert not list(latency._whole_deployments(target, data, snapshots, {}, {}))


def test_prefill_and_decode_are_distinct_owned_workloads():
    target = latency.Target(("A", "serving"), "chat", "service")
    snapshots = {target.scope: {"services": {"chat": {"app": "chat"}}}}
    revisions = {}
    for role in ("prefill", "decode"):
        labels = {"app": "chat", LABEL_PD_ROLE: role}
        revisions[f"chat-{role}"] = [_revision("vllm", labels, labels, role)]
    assert set(latency._whole_deployments(target, {"deployment_revisions": revisions}, snapshots, {}, {})) == {
        ("chat-prefill", "vllm", "prefill", "prefill"), ("chat-decode", "vllm", "decode", "decode"),
    }


async def test_verified_pod_history_matches_deployment_role_and_engine(mock_db):
    target = latency.Target(("east", "serving"), "chat-dc", "deployment")
    managed = {target: latency.ManagedWorkload("vllm", "managed-id", "decode")}
    group = latency.Pool()
    group.owners["serving", "chat-dc"] = "uid"
    group.pods["serving"].add("current")
    group.deployments["serving"].add("chat-dc")
    mock_db.execute.return_value = [
        ("managed-id", "decode", "vllm", "old"), ("managed-id", "prefill", "vllm", "wrong-role"),
        ("managed-id", "decode", "sglang", "wrong-engine"), ("other-id", "decode", "vllm", "wrong-owner"),
    ]
    known = await latency._observed_pod_names(mock_db, {("east", "vllm", "decode"): group}, managed, 1800)
    assert latency._history_targets(group, "east", managed, known) == {
        "deployments": {"serving": ["chat-dc"]}, "observed_pods": {"serving": ["current", "old"]},
    }
    group.has_unverified_pods = True
    assert latency._history_targets(group, "east", managed, known) == {}


async def test_history_and_live_share_one_route_snapshot_after_half_minute(monkeypatch, mock_db, mock_litellm):
    reader = wire_clusters(monkeypatch, mock_db, [external("A", "a")])
    mock_litellm.get_model_info.return_value = [registration("org/model", "a", SERVICE)]
    wall_time = 1817
    monkeypatch.setattr(latency, "time", SimpleNamespace(time=lambda: wall_time))
    monkeypatch.setattr(latency, "read_latency_history", AsyncMock(return_value=dict.fromkeys(latency.METRICS, [])))
    stored = {}

    async def shared(db, *, scope, history, now, load):
        key = (json.dumps(scope, sort_keys=True), history, now)
        if key not in stored:
            stored[key] = json.dumps(await load())
        return json.loads(stored[key])

    monkeypatch.setattr(latency, "cached_latency", shared)
    for history in (True, False, False, True):
        result = await latency.model_live_latency(
            mock_db, mock_litellm, "org/model", history=history, config_epoch="v1",
        )
        assert {r["pool"] for r in result["rows"]} == {"prefill", "decode"}
    # Both readers discover the targets at :15 even though history ends at :00.
    assert len(stored) == 3
    assert latency.namespace_scopes.await_count == latency.workload_snapshots.read.await_count == 1
    assert reader.await_count == 2 and latency.read_latency_history.await_count == 2
    mock_litellm.get_model_info.assert_awaited_once()
    # A changed generation rebuilds the actual Pod mapping, even within the same tick.
    await latency.model_live_latency(mock_db, mock_litellm, "org/model", config_epoch="v2")
    assert latency.namespace_scopes.await_count == 2
    # A later viewer can reuse the completed 30-second backfill across a 15-second live tick.
    for wall_time in (1831, 1846):
        await latency.model_live_latency(mock_db, mock_litellm, "org/model", history=True, config_epoch="v2")
    assert latency.namespace_scopes.await_count == 3 and latency.read_latency_history.await_count == 4


async def test_rotated_credentials_between_plan_and_measurement_skip_prometheus(monkeypatch, mock_db, mock_litellm):
    reader = wire_clusters(monkeypatch, mock_db, [external("A", "a")])
    mock_litellm.get_model_info.return_value = [registration("org/model", "a", SERVICE)]
    token = "private-token-before-rotation"
    rotated = PrometheusConnection("https://new", True, "env", "different-cluster", "new-secret")
    monkeypatch.setattr(latency, "resolve_prometheus_connection", AsyncMock(side_effect=[
        PrometheusConnection("https://central", True, "env", "a", token), rotated,
    ]))
    plan = await latency._resolve_targets(mock_db, mock_litellm, "org/model", 1815)
    assert token not in json.dumps(plan) and "https://central" not in json.dumps(plan)
    result = await latency._read_plan(mock_db, plan, False, 1815)
    assert {r["status"] for r in result["rows"]} == {"misconfigured"}
    reader.assert_not_awaited()
