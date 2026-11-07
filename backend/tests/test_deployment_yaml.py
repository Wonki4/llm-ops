from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from kubernetes_asyncio.client import ApiClient, Configuration

from app.clients.k8s import K8sClient
from tests.test_storage_recipes_deployments import _dep_row, _result


@pytest.mark.parametrize("mode,expected", [
    ("aggregated", ["llama-dep", "llama-svc", "llama-ing"]),
    ("pd", ["llama-dep-pf", "llama-svc-pf", "llama-dep-dc", "llama-svc-dc"]),
])
async def test_live_yaml_comes_from_scoped_cluster_objects(client_for_user, super_user, mock_db, mode, expected):
    row = _dep_row(serving_mode=mode)
    mock_db.execute = AsyncMock(return_value=_result(row))
    names = []

    async def read(method, url, **kwargs):
        assert method == "GET" and "/namespaces/prod/" in url
        name = url.rsplit("/", 1)[1]
        names.append(name)
        response = MagicMock()
        response.status = 404 if name == expected[-1] else 200
        resource = url.split("/")[-2]
        api_version = "apps/v1" if resource == "deployments" else "v1"
        response.json = AsyncMock(return_value={
            "apiVersion": api_version,
            "metadata": {"name": name, "namespace": "prod", "managedFields": [{}], "labels": {"from-cluster": "yes"}},
            "spec": {"replicas": 7} if resource == "deployments" else {"clusterIP": "10.0.0.1"},
            "status": {"ignored": True},
        })
        return response

    api = ApiClient(Configuration(host="https://cluster.example"))
    with (
        patch.object(api, "request", side_effect=read),
        patch.object(K8sClient, "_api_client", AsyncMock(return_value=api)),
    ):
        async with client_for_user(super_user) as client:
            resp = await client.get(f"/api/model-deployments/{row.id}/manifests")
    assert resp.status_code == 200, resp.text
    assert sorted(names) == sorted(expected)
    data = resp.json()
    missing_kind = "Service" if mode == "pd" else "Ingress"
    assert data["missing"] == [f"{missing_kind}/{expected[-1]}"]
    assert {m["name"] for m in data["manifests"]} == set(expected[:-1])
    for manifest in data["manifests"]:
        doc = yaml.safe_load(manifest["yaml"])
        assert doc["metadata"] == {"name": manifest["name"], "namespace": "prod", "labels": {"from-cluster": "yes"}}
        assert "status" not in doc
        if manifest["kind"] == "Deployment":
            assert doc["spec"]["replicas"] == 7


@pytest.mark.parametrize("direct", [False, True])
async def test_pd_yaml_includes_linked_router_ingresses(client_for_user, super_user, mock_db, direct):
    from uuid import uuid4

    from app.db.models.custom_llmd_stack import CustomLlmdStack
    from app.services import llmd_stacks

    stack = CustomLlmdStack(
        id=uuid4(), cluster_id=uuid4(), argo_app_name="linked-router", namespace="routing",
        ingress_host="router.example.test", direct_route_enabled=direct,
    )
    row = _dep_row(serving_mode="pd", router_stack_id=stack.id)
    mock_db.execute = AsyncMock(return_value=_result(row))
    mock_db.get = AsyncMock(return_value=stack)
    pools = AsyncMock()
    pools.read_manifests.return_value = []
    router = AsyncMock()
    ingress = llmd_stacks.ingress_for(stack)
    ingress["spec"]["rules"][0]["host"] = "live.example.test"
    names = {"ingress": "linked-router-ing"}
    live = [("Ingress", names["ingress"], ingress)]
    if direct:
        names["direct_ingress"] = "linked-router-direct-ing"
        live.append(("Ingress", names["direct_ingress"], None))
    router.read_manifests.return_value = live
    with patch("app.api.model_deployments.k8s_for_cluster", AsyncMock(side_effect=[pools, router])) as clusters:
        async with client_for_user(super_user) as client:
            resp = await client.get(f"/api/model-deployments/{row.id}/manifests")
    assert resp.status_code == 200, resp.text
    clusters.assert_any_await(mock_db, stack.cluster_id)
    router.read_manifests.assert_awaited_once_with("routing", names)
    data = resp.json()
    assert [m["name"] for m in data["manifests"]] == ["linked-router-ing"]
    assert yaml.safe_load(data["manifests"][0]["yaml"])["spec"]["rules"][0]["host"] == "live.example.test"
    assert data["missing"] == (["Ingress/linked-router-direct-ing"] if direct else [])
