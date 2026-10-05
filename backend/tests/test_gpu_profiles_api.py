"""GPU profile CRUD, node discovery, gpu-types union, deploy-time resolution, importer recognition."""

import types
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.clients.k8s import K8sNotConfigured


def _result(scalar=None, all_rows=None):
    r = MagicMock()
    r.scalar_one_or_none.return_value = scalar
    r.scalars.return_value.all.return_value = all_rows or []
    return r


def _profile(**kw):
    base = dict(
        id=uuid.uuid4(), cluster_id=None, name="a100-80g", label_key=None, label_value="a100-80g",
        gpu_resource_key="nvidia.com/gpu", tolerations=None, vram_gb=80, description=None, enabled=True,
        created_by="ADMIN001", updated_by=None, updated_at=None,
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


_BODY = {"name": "a100-80g", "label_value": "a100-80g", "vram_gb": 80}


# ─── CRUD ─────────────────────────────────────────────────────────────────────


async def test_list_profiles_default_cluster(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(all_rows=[_profile(), _profile(name="h100", label_value="h100")]))
    async with client_for_user(super_user) as client:
        resp = await client.get("/api/admin/k8s-clusters/default/gpu-profiles")
    assert resp.status_code == 200
    body = resp.json()
    assert body["label_key"] == "gpu-type"
    assert [p["name"] for p in body["profiles"]] == ["a100-80g", "h100"]
    assert body["profiles"][0]["effective_label_key"] == "gpu-type"


async def test_create_profile_201_and_409(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(scalar=None))
    async with client_for_user(super_user) as client:
        resp = await client.post("/api/admin/k8s-clusters/default/gpu-profiles", json=_BODY)
    assert resp.status_code == 201, resp.text
    assert resp.json()["gpu_resource_key"] == "nvidia.com/gpu"
    mock_db.add.assert_called_once()

    mock_db.execute = AsyncMock(return_value=_result(scalar=_profile()))
    async with client_for_user(super_user) as client:
        resp = await client.post("/api/admin/k8s-clusters/default/gpu-profiles", json=_BODY)
    assert resp.status_code == 409


async def test_create_profile_rejects_bad_name_and_cluster_id(client_for_user, super_user, mock_db):
    async with client_for_user(super_user) as client:
        resp = await client.post("/api/admin/k8s-clusters/default/gpu-profiles", json={**_BODY, "name": "A100 80G"})
        assert resp.status_code == 422
        resp = await client.post("/api/admin/k8s-clusters/not-a-uuid/gpu-profiles", json=_BODY)
        assert resp.status_code == 400


async def test_update_and_delete_profile(client_for_user, super_user, mock_db):
    row = _profile()
    # update: load row, clash check (none)
    mock_db.execute = AsyncMock(side_effect=[_result(scalar=row), _result(scalar=None)])
    async with client_for_user(super_user) as client:
        resp = await client.put(
            f"/api/admin/k8s-clusters/default/gpu-profiles/{row.id}",
            json={**_BODY, "label_key": "accelerator", "enabled": False},
        )
    assert resp.status_code == 200, resp.text
    assert row.label_key == "accelerator" and row.enabled is False
    assert resp.json()["effective_label_key"] == "accelerator"

    mock_db.execute = AsyncMock(return_value=_result(scalar=row))
    async with client_for_user(super_user) as client:
        resp = await client.delete(f"/api/admin/k8s-clusters/default/gpu-profiles/{row.id}")
    assert resp.status_code == 200 and resp.json()["deleted"] is True
    mock_db.delete.assert_called_once_with(row)


async def test_profiles_require_super_user(client_for_user, regular_user, mock_db):
    async with client_for_user(regular_user) as client:
        resp = await client.get("/api/admin/k8s-clusters/default/gpu-profiles")
    assert resp.status_code == 403


# ─── discovery ────────────────────────────────────────────────────────────────


async def test_gpu_nodes_groups_by_label_value(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(all_rows=[_profile()]))
    k8s = MagicMock()
    k8s.list_gpu_nodes = AsyncMock(
        return_value=[
            {"name": "a", "label_value": "a100-80g", "schedulable": True, "allocatable": {"nvidia.com/gpu": 8},
             "requested": {"nvidia.com/gpu": 3}},
            {"name": "b", "label_value": "a100-80g", "schedulable": False, "allocatable": {"nvidia.com/gpu": 8},
             "requested": {"nvidia.com/gpu": 0}},
            {"name": "c", "label_value": None, "schedulable": True, "allocatable": {"nvidia.com/gpu": 4},
             "requested": {"nvidia.com/gpu": 4}},
        ]
    )
    with patch("app.api.gpu_profiles.k8s_for_cluster", AsyncMock(return_value=k8s)):
        async with client_for_user(super_user) as client:
            resp = await client.get("/api/admin/k8s-clusters/default/gpu-nodes")
    assert resp.status_code == 200
    body = resp.json()
    assert body["errors"] == [] and body["resource_keys"] == ["nvidia.com/gpu"]
    k8s.list_gpu_nodes.assert_awaited_once_with("gpu-type", ["nvidia.com/gpu"])
    g = {x["label_value"]: x for x in body["groups"]}
    assert g["a100-80g"]["nodes"] == 2
    assert g["a100-80g"]["allocatable"] == {"nvidia.com/gpu": 8}  # cordoned node excluded
    assert g["a100-80g"]["requested"] == {"nvidia.com/gpu": 3}
    assert g["a100-80g"]["available"] == {"nvidia.com/gpu": 5}
    assert g["a100-80g"]["profile_name"] == "a100-80g"
    assert g[None]["profile_name"] is None and g[None]["available"] == {"nvidia.com/gpu": 0}
    assert [x["label_value"] for x in body["groups"]][-1] is None  # unlabelled last


async def test_gpu_nodes_reports_errors_instead_of_failing(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(all_rows=[]))
    k8s = MagicMock()
    k8s.list_gpu_nodes = AsyncMock(side_effect=K8sNotConfigured("no kubeconfig"))
    with patch("app.api.gpu_profiles.k8s_for_cluster", AsyncMock(return_value=k8s)):
        async with client_for_user(super_user) as client:
            resp = await client.get("/api/admin/k8s-clusters/default/gpu-nodes")
    assert resp.status_code == 200
    assert resp.json()["groups"] == [] and resp.json()["errors"] == ["no kubeconfig"]


async def test_gpu_types_union(client_for_user, super_user, mock_db):
    cid = uuid.uuid4()
    cluster = types.SimpleNamespace(id=cid, name="prod-a")
    rows = [_profile(), _profile(cluster_id=cid, name="a100-80g", vram_gb=80), _profile(cluster_id=cid, name="h100")]
    mock_db.execute = AsyncMock(side_effect=[_result(all_rows=rows), _result(all_rows=[cluster])])
    async with client_for_user(super_user) as client:
        resp = await client.get("/api/admin/gpu-types")
    assert resp.status_code == 200
    types_ = {t["name"]: t for t in resp.json()["types"]}
    assert set(types_) == {"a100-80g", "h100"}
    assert sorted(c["cluster_name"] for c in types_["a100-80g"]["clusters"]) == ["default", "prod-a"]


# ─── deploy-time resolution ───────────────────────────────────────────────────

_DEPLOY = {"model_name": "m1", "model_path": "/w/m", "ingress_host": "m1.local", "gpu_count": 1}


async def test_create_deployment_unknown_gpu_type_400(client_for_user, super_user, mock_db):
    # uniqueness check → free; profiles on the cluster → one, not matching
    mock_db.execute = AsyncMock(side_effect=[_result(scalar=None), _result(all_rows=[_profile(name="h100")])])
    with patch("app.api.model_deployments.k8s_for_cluster", AsyncMock(return_value=MagicMock())):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/model-deployments", json={**_DEPLOY, "gpu_type": "a100-80g"})
    assert resp.status_code == 400
    assert "h100" in resp.json()["detail"]


async def test_create_deployment_resolves_profile_into_placement(client_for_user, super_user, mock_db):
    profile = _profile(tolerations=[{"key": "gpu", "operator": "Exists", "effect": "NoSchedule"}],
                       gpu_resource_key="nvidia.com/mig-3g.40gb")
    mock_db.execute = AsyncMock(side_effect=[_result(scalar=None), _result(all_rows=[profile])])
    k8s = MagicMock()
    k8s.create_or_patch = AsyncMock()
    with patch("app.api.model_deployments.k8s_for_cluster", AsyncMock(return_value=k8s)):
        async with client_for_user(super_user) as client:
            resp = await client.post(
                "/api/model-deployments",
                json={**_DEPLOY, "gpu_type": "a100-80g", "node_selector": {"zone": "a"}},
            )
    assert resp.status_code == 201, resp.text
    dep = mock_db.add.call_args.args[0]
    assert dep.gpu_type == "a100-80g"
    assert dep.node_selector == {"zone": "a", "gpu-type": "a100-80g"}
    assert dep.gpu_resource_key == "nvidia.com/mig-3g.40gb"
    assert dep.tolerations == [{"key": "gpu", "operator": "Exists", "effect": "NoSchedule"}]
    assert resp.json()["gpu_type"] == "a100-80g"


async def test_create_deployment_without_profiles_keeps_explicit_placement(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(side_effect=[_result(scalar=None), _result(all_rows=[])])
    k8s = MagicMock()
    k8s.create_or_patch = AsyncMock()
    with patch("app.api.model_deployments.k8s_for_cluster", AsyncMock(return_value=k8s)):
        async with client_for_user(super_user) as client:
            resp = await client.post(
                "/api/model-deployments",
                json={**_DEPLOY, "gpu_type": "whatever", "node_selector": {"gpu-type": "manual"}},
            )
    assert resp.status_code == 201, resp.text
    dep = mock_db.add.call_args.args[0]
    assert dep.node_selector == {"gpu-type": "manual"} and dep.gpu_type == "whatever"


# ─── importer recognises a profile ────────────────────────────────────────────


def _live_spec(node_selector):
    return {
        "name": "ext", "namespace": "team-a", "labels": {}, "replicas": 1,
        "container": {"name": "s", "image": "vllm/vllm-openai:v0.6.0", "command": [], "args": ["--model", "/m"],
                      "env": [], "env_raw": [], "resources": {"limits": {"nvidia.com/gpu": "2"}}, "ports": [],
                      "volume_mounts": []},
        "volumes": [], "node_selector": node_selector, "tolerations": None,
    }


async def test_recipe_draft_maps_node_selector_to_gpu_type(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(all_rows=[_profile()]))
    k8s = MagicMock()
    k8s.read_deployment = AsyncMock(return_value=_live_spec({"gpu-type": "a100-80g", "zone": "a"}))
    with patch("app.api.model_deployments.k8s_for_cluster", AsyncMock(return_value=k8s)):
        async with client_for_user(super_user) as client:
            resp = await client.get(
                "/api/model-deployments/external/recipe-draft", params={"namespace": "team-a", "deployment_name": "ext"}
            )
    assert resp.status_code == 200, resp.text
    d = resp.json()["draft"]
    assert d["gpu_type"] == "a100-80g" and d["node_selector"] == {"zone": "a"}


async def test_recipe_draft_without_match_leaves_selector(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(all_rows=[_profile()]))
    k8s = MagicMock()
    k8s.read_deployment = AsyncMock(return_value=_live_spec({"gpu-type": "unknown"}))
    with patch("app.api.model_deployments.k8s_for_cluster", AsyncMock(return_value=k8s)):
        async with client_for_user(super_user) as client:
            resp = await client.get(
                "/api/model-deployments/external/recipe-draft", params={"namespace": "team-a", "deployment_name": "ext"}
            )
    d = resp.json()["draft"]
    assert d["gpu_type"] is None and d["node_selector"] == {"gpu-type": "unknown"}
