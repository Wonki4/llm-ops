import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api import serving_overview
from app.services.clusters import NamespaceScope, namespace_scopes
from app.services.serving_topology import NamespaceSnapshots, build_topology


def registration(name, mid, base):
    return {
        "model_name": name,
        "model_info": {"id": mid},
        "litellm_params": {"api_base": base, "api_key": "secret-value"},
    }


def snapshot(service="pool"):
    return {
        "cluster": "lab",
        "pod_owners": {},
        "paths": [("front", "models.test", "/chat", "Prefix", service)],
        "services": {service: {"app": "server"}},
        "pods": [("pod-a", {"app": "server", "llm-d.ai/role": "decode"}, "Running", True, False)],
    }


def test_many_to_many_shared_ingress_and_pods():
    records = [
        registration(name, mid, "https://models.test/chat/v1?key=secret-value")
        for name, mid in [("chat/v2", "b"), ("chat/v2", "a"), ("alias", "c")]
    ]
    graph = build_topology(records, {"chat/v2": "Chat"}, [], [], {(None, "ns"): snapshot()})
    kinds = [n["kind"] for n in graph["nodes"]]
    assert kinds.count("model") == 2 and kinds.count("registration") == 3
    assert all(kinds.count(k) == 1 for k in ("ingress", "service", "pod"))
    assert len(graph["edges"]) == 8
    assert "secret-value" not in str(graph)


def test_identical_ingress_in_multiple_clusters_is_ambiguous():
    model = registration("chat", "one", "http://models.test/chat/v1")
    graph = build_topology([model], {}, [], [], {(c, "ns"): snapshot() for c in ("a", "b")})
    assert len(graph["edges"]) == 1
    reg = next(n for n in graph["nodes"] if n["kind"] == "registration")
    assert reg["status"] == "ambiguous"
    assert reg["locations"] == [{"cluster_id": "a", "namespace": "ns"}, {"cluster_id": "b", "namespace": "ns"}]


async def test_all_registered_scopes_include_empty_clusters_and_keep_namespaces_distinct():
    rows = [
        SimpleNamespace(id="a", name="first", namespace="serving", is_default=True),
        SimpleNamespace(id="b", name="second", namespace="serving", is_default=False),
    ]
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    db = SimpleNamespace(execute=AsyncMock(return_value=result))
    scopes = await namespace_scopes(db, [("a", "serving"), ("a", "team"), (None, "team")], include_registered=True)
    assert [(s.cluster_id, s.namespace) for s in scopes] == [
        ("a", "serving"), ("b", "serving"), ("a", "team"), (None, "team")
    ]


async def test_reads_coalesce_and_cache_by_cluster_namespace_and_config(monkeypatch):
    a = NamespaceScope(SimpleNamespace(id="a", updated_at=1), "serving")
    b = NamespaceScope(SimpleNamespace(id="b", updated_at=1), "serving")
    reader = AsyncMock(side_effect=[{"pods": ["one"]}, RuntimeError("offline"), {"pods": ["two"]}, {"pods": ["new"]}])
    monkeypatch.setattr(NamespaceScope, "client", lambda scope: scope.cluster_id)
    cache = NamespaceSnapshots(reader)
    first, again = await asyncio.gather(cache.read(a), cache.read(a))
    assert first == again == await cache.read(a) == {"pods": ["one"]}
    with pytest.raises(RuntimeError, match="offline"):
        await cache.read(b)
    assert await cache.read(b) == {"pods": ["two"]}
    a.cluster.updated_at = 2
    assert await cache.read(a) == {"pods": ["new"]}
    assert reader.await_count == 4


def test_alias_on_two_clusters_keeps_distinct_routes_and_namesakes():
    rows = [registration("shared", cluster, f"https://{cluster}.test/chat") for cluster in ("east", "west")]
    snapshots = {}
    for cid in ("east", "west"):
        data = snapshot()
        data["paths"] = [("front", f"{cid}.test", "/chat", "Prefix", "pool")]
        snapshots[cid, "serving"] = data
    graph = build_topology(rows, {}, [], [], snapshots)
    nodes = {n["id"]: n for n in graph["nodes"]}
    roots = [n for n in nodes.values() if n["kind"] == "model"]
    assert len(roots) == 1
    for source, target in graph["edges"]:
        if nodes[source]["kind"] == "registration":
            assert nodes[source]["locations"] == [{"cluster_id": nodes[source]["label"], "namespace": "serving"}]
    assert sum(n["kind"] == "pod" and n["label"] == "pod-a" for n in nodes.values()) == 2
    assert {(e["cluster"], e["ns"], e["name"]) for e in graph["entries"]} == {
        ("east", "serving", "front"), ("west", "serving", "front"),
    }


def test_shared_entry_keeps_all_registrations_and_distinct_path_backends():
    records = [registration(f"alias-{i}", f"id-{i}", "https://models.test/chat/v1") for i in range(13)]
    records.extend([
        registration("second-name", "id-0", "https://models.test/chat/v1"),
        registration("embed", "id-embed", "https://models.test/embed"),
        registration("direct", "id-direct", "http://pool.ns.svc/v1"),
        registration("canary", "id-canary", "https://canary.test/chat"),
        registration("other", "id-other", "https://private.test/embed"),
    ])
    data = snapshot()
    data["paths"].extend([
        ("front", "models.test", "/embed", "Exact", "embeddings"),
        ("other-front", "canary.test", "/chat", "Prefix", "pool"),
        ("wildcard", "*.test", "/embed", "Prefix", "embeddings"),
    ])
    data["services"]["embeddings"] = {"app": "embed"}
    graph = build_topology(records, {}, [], [], {(None, "ns"): data})
    entries = {(entry["kind"], entry["name"]): entry for entry in graph["entries"]}
    nodes = {n["id"]: n for n in graph["nodes"]}
    shared = entries["ingress", "front"]
    assert {nodes[reg]["label"] for reg in shared["registrations"]} == {*(f"id-{i}" for i in range(13)), "id-embed"}
    assert len([edge for edge in graph["edges"] if edge[1] in shared["registrations"]]) == 15
    assert len(shared["registrations"]) == 14
    assert set(entries) == {
        ("ingress", "front"), ("ingress", "other-front"), ("ingress", "wildcard"), ("service", "pool"),
    }
    destinations = {
        (nodes[reg]["label"], nodes[route]["detail"], nodes[svc]["label"])
        for reg, route in graph["edges"] if nodes[route]["kind"] == "ingress"
        for parent, svc in graph["edges"] if parent == route
    }
    assert {d for d in destinations if d[0] in {"id-0", "id-embed", "id-other"}} == {
        ("id-0", "models.test/chat", "pool"),
        ("id-embed", "models.test/embed", "embeddings"),
        ("id-other", "*.test/embed", "embeddings"),
    }


async def test_scoped_summary_reads_pods_only_on_expansion(monkeypatch):
    scope = NamespaceScope(None, "ns")
    scopes = [scope, NamespaceScope(None, "other")]
    monkeypatch.setattr(serving_overview, "namespace_scopes", AsyncMock(return_value=scopes))
    data = snapshot()
    routes = AsyncMock(return_value={k: data[k] for k in ("paths", "services")})
    workloads = AsyncMock(return_value={
        "pods": data["pods"], "pod_owners": {"pod-a": {"controller": "ReplicaSet/rs", "deployment": ("uid", "chat")}},
    })
    monkeypatch.setattr(serving_overview.route_snapshots, "read", routes)
    monkeypatch.setattr(serving_overview.workload_snapshots, "read", workloads)
    result = MagicMock()
    result.all.return_value = result.scalars.return_value.all.return_value = []
    db = SimpleNamespace(execute=AsyncMock(return_value=result), scalars=AsyncMock(return_value=result))
    litellm = SimpleNamespace(get_model_info=AsyncMock(return_value=[registration("chat", "one", "https://models.test/chat")]))
    summary = await serving_overview.serving_topology(None, db, litellm, cluster="default", ns="ns", summary=True)
    assert summary["partial_coverage"] and len(summary["scopes"]) == 2
    assert [entry["name"] for entry in summary["entries"]] == ["front"]
    assert all(n["kind"] not in {"pod", "workload"} and n["status"] != "noPods" for n in summary["nodes"])
    routes.assert_awaited_once_with(scope)
    workloads.assert_not_awaited()
    detail = await serving_overview.topology_workloads("default", "ns", "ingress", "front", None, db)
    workloads.assert_awaited_once_with(scope)
    litellm.get_model_info.assert_awaited_once()
    labels = {n["id"]: n["label"] for n in detail["nodes"]}
    assert [(labels[a], labels[b]) for a, b in detail["edges"]] == [("pool", "pod-a")]
    assert [(labels[a], labels[b]) for a, b in detail["ownership_edges"]] == [("pod-a", "chat")]


def test_origin_is_per_registration_and_respects_custom_endpoints():
    cases = [
        ("local", "https://models.test/chat/v1", "cluster"),
        ("default", None, "cloud"),
        ("azure", "https://example.openai.azure.com/v1", "cloud"),
        ("override", "https://private.test/v1", "unknown"),
        ("forwarded", "http://host.docker.internal:18300/v1", "cluster"),
        ("indirect", "os.environ/MODEL_URL", "unknown"),
    ]
    regs = []
    for mid, url, _ in cases:
        reg = registration("shared", mid, url)
        reg["litellm_params"]["model"] = "openai/chat"
        regs.append(reg)
    owner = {"model_id": "forwarded", "name": "shared", "deployment": "chat-dep", "ref": "dep"}
    graph = build_topology(regs, {}, [owner], [], {(None, "ns"): snapshot()})
    nodes = {n["label"]: n for n in graph["nodes"] if n["kind"] == "registration"}
    assert {mid: n["origin"] for mid, n in nodes.items()} == {mid: expect for mid, _, expect in cases}
    assert nodes["default"]["cloud_provider"] == "OpenAI"
    assert nodes["azure"]["cloud_provider"] == "Azure OpenAI"
    assert nodes["forwarded"]["status"] == "unresolved"
    assert sum(n["kind"] == "model" for n in graph["nodes"]) == 1


def test_llmd_service_follows_scoped_model_server_selector():
    stack = SimpleNamespace(
        id="r",
        cluster_id=None,
        name="r",
        namespace="ns",
        argo_app_name="router",
        values_snapshot={"router": {"modelServers": {"matchLabels": {"pool": "x"}}}},
    )
    ns = snapshot("router-epp")
    ns["pods"].append(("prefill-a", {"pool": "x", "llm-d.ai/role": "prefill"}, "Running", False, False))
    records = [registration("chat", "one", "http://router-epp.ns.svc.cluster.local:8081/v1")]
    graph = build_topology(records, {}, [], [stack], {(None, "ns"): ns})
    assert [n["kind"] for n in graph["nodes"] if n["kind"] in {"ingress", "router", "service"}] == ["router"]
    pods = [n for n in graph["nodes"] if n["kind"] == "pod"]
    assert [(n["label"], n["detail"], n["status"]) for n in pods] == [("prefill-a", "prefill", "notReady")]


def test_unavailable_litellm_does_not_report_stale_registration():
    owners = [{"model_id": "saved", "ref": "d", "deployment": "blue", "name": "chat"}]
    for available, status in [(True, "stale"), (False, "unverified")]:
        graph = build_topology([], {}, owners, [], {}, litellm_available=available)
        assert graph["nodes"][0]["status"] == status


async def test_topology_admin_only(user_client):
    assert (await user_client.get("/api/admin/serving/topology")).status_code == 403
    detail = await user_client.get(
        "/api/admin/serving/topology/workloads",
        params={"cluster": "default", "ns": "ns", "kind": "ingress", "name": "front"},
    )
    assert detail.status_code == 403


def test_rollout_pods_share_deployment_without_expanding_service_routes():
    from app.services.serving_topology import _pod_owner

    def controller(kind, uid, name):
        return SimpleNamespace(controller=True, api_version="apps/v1", kind=kind, uid=uid, name=name)

    deployment = controller("Deployment", "d1", "chat")
    replica_sets = {"rs1": deployment, "rs2": deployment}
    data = snapshot()
    data["services"]["canary"] = {"canary": "true"}
    data["pods"] = []
    for pod, rs_uid, labels in [
        ("old", "rs1", {"app": "server"}),
        ("new", "rs2", {"canary": "true"}),
        ("orphan", "deleted-rs", {"canary": "true"}),
    ]:
        ref = controller("ReplicaSet", rs_uid, "chat-rs")
        data["pods"].append((pod, labels, "Running", True, False))
        data["pod_owners"][pod] = _pod_owner(SimpleNamespace(owner_references=[ref]), replica_sets)
    models = [
        registration("v1", "one", "http://pool.ns.svc"),
        registration("v2", "two", "http://canary.ns.svc"),
    ]
    graph = build_topology(models, {}, [], [], {(None, "ns"): data})
    nodes = {n["id"]: n["label"] for n in graph["nodes"]}
    assert {(nodes[a], nodes[b]) for a, b in graph["ownership_edges"]} == {("old", "chat"), ("new", "chat")}
    assert ("canary", "old") not in [(nodes[a], nodes[b]) for a, b in graph["edges"]]
    assert sum(n["kind"] == "workload" for n in graph["nodes"]) == 1
    assert next(n for n in graph["nodes"] if n["label"] == "orphan")["controller"] == "ReplicaSet/chat-rs"
