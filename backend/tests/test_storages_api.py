# backend/tests/test_storages_api.py
"""Storage + binding CRUD, delete protection, status, resolve, cluster PVC listing."""

import types
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.exc import IntegrityError

from app.services.storage_mounts import snapshot
from tests._storage import CLUSTER, SID, binding

NOW = datetime(2026, 10, 19, tzinfo=UTC)


def _result(scalar=None, all_rows=None):
    r = MagicMock()
    r.scalar_one_or_none.return_value = scalar
    r.scalars.return_value.all.return_value = all_rows or []
    return r


def _storage(**kw):
    base = dict(id=uuid.UUID(SID), name="llama-weights", description=None, kind="weights",
                created_by="ADMIN001", updated_by=None, created_at=NOW, updated_at=NOW)
    base.update(kw)
    return types.SimpleNamespace(**base)


def _usage(**kw):
    return patch("app.api.storages.usage_index", AsyncMock(return_value=kw))


def _k8s_patch(k8s):
    return patch("app.services.storage_store.k8s_for_cluster", AsyncMock(return_value=k8s))


def _cluster_names():
    names = MagicMock()
    names.scalars.return_value.all.return_value = [types.SimpleNamespace(id=CLUSTER, name="gpu-a")]
    return names


async def test_list_storages_with_counts_and_bound_on(client_for_user, super_user, mock_db):
    rows = [_storage(), _storage(id=uuid.uuid4(), name="hf-cache", kind="cache")]
    bindings = [binding(cluster_id=CLUSTER, namespace="prod"), binding()]
    mock_db.execute = AsyncMock(side_effect=[_result(all_rows=rows), _result(all_rows=bindings), _cluster_names()])
    usage = {SID: {"recipes": [{"id": "r", "name": "r"}], "deployments": [], "clusters": []}}
    with _usage(**usage):
        async with client_for_user(super_user) as client:
            resp = await client.get("/api/admin/storages", params={"kind": "weights"})
    assert resp.status_code == 200, resp.text
    items = resp.json()["storages"]
    assert [s["name"] for s in items] == ["llama-weights"]
    assert items[0]["binding_count"] == 2 and items[0]["recipe_count"] == 1 and items[0]["deployment_count"] == 0
    assert items[0]["bound_on"] == [
        {"cluster_id": None, "cluster_name": "default", "namespace": "default"},
        {"cluster_id": str(CLUSTER), "cluster_name": "gpu-a", "namespace": "prod"},
    ]


async def test_get_storage_usage_flags_stale_deployments(client_for_user, super_user, mock_db):
    b = binding(cluster_id=CLUSTER, namespace="prod")
    current = {"storage_id": SID, "mount_path": "/m", "read_only": True, "volume_name": "st-x", "resolved": snapshot(b)}
    edited = {**current, "resolved": {**snapshot(b), "claim_name": "old-pvc"}}
    mock_db.execute = AsyncMock(side_effect=[_result(scalar=_storage()), _result(all_rows=[b]), _cluster_names()])

    def dep(dep_id, name, entry):
        return {"id": dep_id, "model_name": name, "binding_ids": {str(b.id)}, "cluster_id": CLUSTER,
                "namespace": "prod", "entries": [entry]}

    deployments = [dep("d1", "fresh", current), dep("d2", "old", edited)]
    usage = {SID: {"recipes": [], "clusters": [], "deployments": deployments}}
    with _usage(**usage):
        async with client_for_user(super_user) as client:
            resp = await client.get(f"/api/admin/storages/{SID}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["usage"]["deployments"] == [
        {"id": "d1", "model_name": "fresh", "stale": False},
        {"id": "d2", "model_name": "old", "stale": True},
    ]
    assert resp.json()["bindings"][0]["cluster_name"] == "gpu-a"


async def test_create_storage_validates_name_and_409(client_for_user, super_user, mock_db):
    async with client_for_user(super_user) as client:
        assert (await client.post("/api/admin/storages", json={"name": "Bad Name"})).status_code == 422
    mock_db.execute = AsyncMock(return_value=_result(scalar=None))
    async with client_for_user(super_user) as client:
        resp = await client.post("/api/admin/storages", json={"name": "llama-weights", "kind": "weights"})
    assert resp.status_code == 201, resp.text
    mock_db.add.assert_called_once()
    mock_db.execute = AsyncMock(return_value=_result(scalar=_storage()))
    async with client_for_user(super_user) as client:
        assert (await client.post("/api/admin/storages", json={"name": "llama-weights"})).status_code == 409


async def test_delete_storage_in_use_by_pool_or_bench_409(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(scalar=_storage()))
    for usage in (
        {SID: {"recipes": [], "deployments": [{"id": "d", "model_name": "m", "binding_ids": {"b"}}], "clusters": []}},
        {SID: {"recipes": [], "deployments": [], "clusters": [{"id": "c", "name": "gpu-a"}]}},
    ):
        with _usage(**usage):
            async with client_for_user(super_user) as client:
                resp = await client.delete(f"/api/admin/storages/{SID}")
        assert resp.status_code == 409
    with _usage():
        async with client_for_user(super_user) as client:
            resp = await client.delete(f"/api/admin/storages/{SID}")
    assert resp.status_code == 200
    mock_db.delete.assert_awaited_once()


async def test_binding_validation(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(scalar=_storage()))
    url = f"/api/admin/storages/{SID}/bindings"
    bad = [
        {"type": "pvc", "claim_name": "c"},  # pvc needs namespace
        {"type": "pvc", "namespace": "prod"},  # pvc needs claim
        {"type": "nfs", "nfs_server": "s"},  # nfs needs path
        {"type": "nfs", "nfs_server": "s", "nfs_path": "/p", "claim_name": "c"},
        {"type": "nfs", "nfs_server": "s", "nfs_path": "/p", "namespace": "Bad_NS"},
        {"type": "nfs", "nfs_server": "s", "nfs_path": "/p", "sub_path": "/abs"},
        {"type": "nfs", "nfs_server": "s", "nfs_path": "/p", "sub_path": "../up"},
    ]
    async with client_for_user(super_user) as client:
        for body in bad:
            assert (await client.post(url, json=body)).status_code == 422, body


async def test_create_binding_201_and_duplicate_scope_409(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(side_effect=[_result(scalar=_storage()), _result(scalar=None), _cluster_names()])
    body = {"cluster_id": str(CLUSTER), "namespace": "prod", "type": "pvc", "claim_name": "models-pvc"}
    async with client_for_user(super_user) as client:
        resp = await client.post(f"/api/admin/storages/{SID}/bindings", json=body)
    assert resp.status_code == 201, resp.text
    assert resp.json()["claim_name"] == "models-pvc" and resp.json()["cluster_id"] == str(CLUSTER)
    assert resp.json()["cluster_name"] == "gpu-a"

    mock_db.execute = AsyncMock(side_effect=[_result(scalar=_storage()), _result(scalar=binding())])
    async with client_for_user(super_user) as client:
        assert (await client.post(f"/api/admin/storages/{SID}/bindings", json=body)).status_code == 409

    mock_db.execute = AsyncMock(side_effect=[_result(scalar=_storage()), _result(scalar=None)])
    mock_db.flush = AsyncMock(side_effect=IntegrityError("x", {}, Exception()))
    async with client_for_user(super_user) as client:
        assert (await client.post(f"/api/admin/storages/{SID}/bindings", json=body)).status_code == 409


async def test_delete_binding_dry_run_lists_affected(client_for_user, super_user, mock_db):
    b = binding()
    mock_db.execute = AsyncMock(return_value=_result(scalar=b))
    deployments = [
        {"id": "d1", "model_name": "llama", "binding_ids": {str(b.id)}},
        {"id": "d2", "model_name": "other", "binding_ids": {"x"}},
    ]
    usage = {SID: {"recipes": [], "deployments": deployments, "clusters": []}}
    with _usage(**usage):
        async with client_for_user(super_user) as client:
            resp = await client.delete(f"/api/admin/storages/{SID}/bindings/{b.id}", params={"dry_run": "true"})
    assert resp.json() == {"deleted": False, "affected_deployments": [{"id": "d1", "model_name": "llama"}]}
    mock_db.delete.assert_not_awaited()
    with _usage(**usage):
        async with client_for_user(super_user) as client:
            resp = await client.delete(f"/api/admin/storages/{SID}/bindings/{b.id}")
    assert resp.json()["deleted"] is True
    mock_db.delete.assert_awaited_once()


async def test_binding_status_groups_lookups_and_reports_missing(client_for_user, super_user, mock_db):
    b1 = binding(claim_name="models-pvc", namespace="prod")
    b2 = binding(claim_name="gone", namespace="prod")
    b3 = binding(type="nfs", claim_name=None, namespace=None, nfs_server="s", nfs_path="/p")
    mock_db.execute = AsyncMock(side_effect=[_result(scalar=_storage()), _result(all_rows=[b1, b2, b3])])
    k8s = MagicMock()
    k8s.list_pvcs = AsyncMock(return_value=[{"name": "models-pvc", "phase": "Bound"}])
    with _k8s_patch(k8s):
        async with client_for_user(super_user) as client:
            resp = await client.get(f"/api/admin/storages/{SID}/bindings/status")
    assert resp.json()["status"] == {str(b1.id): "Bound", str(b2.id): "missing", str(b3.id): None}
    k8s.list_pvcs.assert_awaited_once_with("prod")


async def test_resolve_endpoint(client_for_user, super_user, mock_db):
    b = binding(cluster_id=CLUSTER, namespace="prod")
    k8s = MagicMock(list_pvcs=AsyncMock(return_value=[{"name": "models-pvc", "phase": "Pending"}]))
    with (
        patch("app.api.storages.load_storages", AsyncMock(return_value={SID: _storage()})),
        patch("app.api.storages.bindings_for", AsyncMock(return_value=[b])),
        _k8s_patch(k8s),
    ):
        async with client_for_user(super_user) as client:
            resp = await client.post(
                "/api/admin/storages/resolve",
                json={"storage_ids": [SID], "cluster_id": str(CLUSTER), "namespace": "prod"},
            )
    assert resp.status_code == 200, resp.text
    [r] = resp.json()["results"]
    assert r["name"] == "llama-weights" and r["binding"]["claim_name"] == "models-pvc" and r["pvc_phase"] == "Pending"


async def test_cluster_pvcs_reports_errors_without_failing(client_for_user, super_user, mock_db):
    k8s = MagicMock(list_pvcs=AsyncMock(side_effect=RuntimeError("forbidden")))
    with _k8s_patch(k8s):
        async with client_for_user(super_user) as client:
            resp = await client.get("/api/admin/k8s-clusters/default/pvcs", params={"namespace": "prod"})
    assert resp.status_code == 200
    assert resp.json() == {"pvcs": [], "error": "forbidden"}


async def test_storage_api_is_super_user_only(client_for_user, regular_user, mock_db):
    async with client_for_user(regular_user) as client:
        assert (await client.get("/api/admin/storages")).status_code == 403
        assert (await client.post("/api/admin/storages", json={"name": "x"})).status_code == 403
