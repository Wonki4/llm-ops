# backend/tests/test_storage_store.py
"""storage_store: resolution with snapshots, stale detection, bench volumes, claim matching."""

import types
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import storage_store
from app.services.storage_store import UnknownStorageError, UnresolvedStorageError, resolve_entries
from tests._storage import CLUSTER, SID, SID2, binding, legacy_entry


def _storage(sid=SID, name="llama-weights"):
    return types.SimpleNamespace(id=uuid.UUID(sid), name=name)


def _patch(storages, bindings):
    return (
        patch.object(storage_store, "load_storages", AsyncMock(return_value={str(s.id): s for s in storages})),
        patch.object(storage_store, "bindings_for", AsyncMock(return_value=bindings)),
    )


async def test_resolve_snapshots_and_names_volumes():
    b = binding(cluster_id=CLUSTER, namespace="prod")
    p1, p2 = _patch([_storage()], [b])
    with p1, p2:
        out = await resolve_entries(None, [{"storage_id": SID, "mount_path": "/models"}], CLUSTER, "prod")
    assert out == [{
        "storage_id": SID, "mount_path": "/models", "read_only": True,
        "volume_name": "st-llama-weights", "resolved": {**storage_store.snapshot(b)},
    }]


async def test_resolve_keeps_previous_volume_name():
    b = binding(cluster_id=None, namespace="default")
    p1, p2 = _patch([_storage()], [b])
    with p1, p2:
        out = await resolve_entries(
            None, [{"storage_id": SID, "mount_path": "/models", "read_only": False}], None, "default",
            previous=[legacy_entry("models-pvc", "/models")],
        )
    assert out[0]["volume_name"] == "model-weights"


async def test_resolve_unresolved_names_every_missing_storage():
    p1, p2 = _patch([_storage(), _storage(SID2, "hf-cache")], [])
    with p1, p2, pytest.raises(UnresolvedStorageError) as e:
        await resolve_entries(
            None, [{"storage_id": SID, "mount_path": "/a"}, {"storage_id": SID2, "mount_path": "/b"}], CLUSTER, "prod"
        )
    assert e.value.names == ["llama-weights", "hf-cache"]


async def test_resolve_unknown_storage():
    p1, p2 = _patch([], [])
    with p1, p2, pytest.raises(UnknownStorageError):
        await resolve_entries(None, [{"storage_id": SID, "mount_path": "/a"}], CLUSTER, "prod")


async def test_resolve_rejects_reserved_previous_volume_name():
    p1, p2 = _patch([_storage()], [binding(cluster_id=None, namespace="default")])
    with p1, p2, pytest.raises(ValueError, match="reserved"):
        await resolve_entries(
            None, [{"storage_id": SID, "mount_path": "/models"}], None, "default",
            previous=[{**legacy_entry("models-pvc", "/models"), "volume_name": "shm"}],
        )


async def test_resolve_empty_list_skips_db():
    with patch.object(storage_store, "load_storages", AsyncMock()) as load:
        assert await resolve_entries(None, [], CLUSTER, "prod") == []
    load.assert_not_awaited()


async def test_resolve_pool_storages_only_touches_roles_that_set_a_list():
    b = binding(cluster_id=None, namespace="default")
    p1, p2 = _patch([_storage()], [b])
    cfg = {"prefill": {"storages": [{"storage_id": SID, "mount_path": "/m"}]}, "decode": {"replicas": 1}}
    with p1, p2:
        out = await storage_store.resolve_pool_storages(None, cfg, None, "default")
    assert out["prefill"]["storages"][0]["resolved"]["binding_id"] == str(b.id)
    assert "storages" not in out["decode"]
    assert await storage_store.resolve_pool_storages(None, None, None, "default") is None


def _dep(entries, pd_config=None, cluster_id=CLUSTER, namespace="prod"):
    return types.SimpleNamespace(storages=entries, pd_config=pd_config, cluster_id=cluster_id, namespace=namespace)


async def test_not_stale_when_binding_unchanged():
    b = binding(cluster_id=CLUSTER, namespace="prod")
    entry = {"storage_id": SID, "mount_path": "/m", "read_only": True, "volume_name": "st-x",
             "resolved": storage_store.snapshot(b)}
    with patch.object(storage_store, "bindings_for", AsyncMock(return_value=[b])):
        assert await storage_store.stale_storage_ids(None, _dep([entry])) == []


async def test_stale_when_binding_edited_or_deleted():
    b = binding(cluster_id=CLUSTER, namespace="prod")
    entry = {"storage_id": SID, "mount_path": "/m", "read_only": True, "volume_name": "st-x",
             "resolved": storage_store.snapshot(b)}
    edited = binding(id=b.id, cluster_id=CLUSTER, namespace="prod", claim_name="other")
    with patch.object(storage_store, "bindings_for", AsyncMock(return_value=[edited])):
        assert await storage_store.stale_storage_ids(None, _dep([entry])) == [SID]
    with patch.object(storage_store, "bindings_for", AsyncMock(return_value=[])):
        assert await storage_store.stale_storage_ids(None, _dep([entry])) == [SID]


async def test_stale_when_more_specific_binding_appears():
    wide = binding(cluster_id=CLUSTER, namespace=None, type="nfs", claim_name=None, nfs_server="s", nfs_path="/p")
    entry = {"storage_id": SID, "mount_path": "/m", "read_only": True, "volume_name": "st-x",
             "resolved": storage_store.snapshot(wide)}
    exact = binding(cluster_id=CLUSTER, namespace="prod")
    with patch.object(storage_store, "bindings_for", AsyncMock(return_value=[wide, exact])):
        assert await storage_store.stale_storage_ids(None, _dep([entry])) == [SID]


async def test_stale_covers_pool_overrides():
    b = binding(cluster_id=CLUSTER, namespace="prod")
    entry = {"storage_id": SID2, "mount_path": "/m", "read_only": True, "volume_name": "st-x",
             "resolved": storage_store.snapshot(binding(storage_id=uuid.UUID(SID2)))}
    dep = _dep([], pd_config={"prefill": {"storages": [entry]}})
    with patch.object(storage_store, "bindings_for", AsyncMock(return_value=[b])):
        assert await storage_store.stale_storage_ids(None, dep) == [SID2]


async def test_bench_volumes_prefers_params_override():
    vols, mounts = await storage_store.bench_volumes(
        None, CLUSTER, "bench", {"nfs_server": "s", "nfs_path": "/p", "nfs_mount_path": "/m"}
    )
    assert vols[0]["nfs"]["server"] == "s" and mounts[0]["mountPath"] == "/m"


async def test_bench_volumes_from_cluster_bench_storage():
    cluster = types.SimpleNamespace(bench_storage_id=uuid.UUID(SID), bench_mount_path="/models")
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=cluster)))
    b = binding(cluster_id=CLUSTER, namespace=None, type="nfs", claim_name=None, nfs_server="s", nfs_path="/w")
    p1, p2 = _patch([_storage()], [b])
    with p1, p2:
        vols, mounts = await storage_store.bench_volumes(db, CLUSTER, "bench", {})
    assert vols == [{"name": "model-weights", "nfs": {"server": "s", "path": "/w", "readOnly": True}}]
    assert mounts == [{"name": "model-weights", "mountPath": "/models", "readOnly": True}]


async def test_bench_volumes_none_for_default_cluster_or_unresolved():
    assert await storage_store.bench_volumes(None, None, "bench", {}) == ([], [])
    cluster = types.SimpleNamespace(bench_storage_id=uuid.UUID(SID), bench_mount_path="/models")
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=cluster)))
    p1, p2 = _patch([_storage()], [])
    with p1, p2:
        assert await storage_store.bench_volumes(db, CLUSTER, "bench", {}) == ([], [])


def test_null_safe_eq():
    from app.db.models.custom_storage import CustomStorageBinding

    assert "IS NULL" in str(storage_store.null_safe_eq(CustomStorageBinding.cluster_id, None))
    assert "= :cluster_id" in str(storage_store.null_safe_eq(CustomStorageBinding.cluster_id, CLUSTER))


async def test_pvc_phases_maps_and_tolerates_failures():
    k8s = MagicMock(list_pvcs=AsyncMock(return_value=[{"name": "a", "phase": "Bound"}, {"name": "b", "phase": None}]))
    with patch.object(storage_store, "k8s_for_cluster", AsyncMock(return_value=k8s)):
        assert await storage_store.pvc_phases(None, CLUSTER, "prod") == {"a": "Bound", "b": "Pending"}
    k8s.list_pvcs.assert_awaited_once_with("prod")
    broken = MagicMock(list_pvcs=AsyncMock(side_effect=RuntimeError("forbidden")))
    with patch.object(storage_store, "k8s_for_cluster", AsyncMock(return_value=broken)):
        assert await storage_store.pvc_phases(None, CLUSTER, "prod") is None


def _scalars(rows):
    return MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=rows))))


def _db(*result_sets):
    """db whose successive execute() calls return the given row lists."""
    return MagicMock(execute=AsyncMock(side_effect=[_scalars(r) for r in result_sets]))


def _entry(sid, bid, mount="/m"):
    return {"storage_id": sid, "mount_path": mount, "read_only": True, "volume_name": "st-x",
            "resolved": {"binding_id": bid}}


async def test_usage_index_covers_recipes_deployments_pools_and_clusters():
    rid, did, cid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    recipe = types.SimpleNamespace(
        id=rid, name="r1", storages=[], pd_config={"decode": {"storages": [{"storage_id": SID}]}}
    )
    dep = types.SimpleNamespace(
        id=did, model_name="m", cluster_id=CLUSTER, namespace="prod",
        storages=[_entry(SID, "b1", "/a")],
        pd_config={"prefill": {"storages": [_entry(SID, "b2", "/b")]}},
    )
    cluster = types.SimpleNamespace(id=cid, name="c1", bench_storage_id=uuid.UUID(SID2))
    no_bench = types.SimpleNamespace(id=uuid.uuid4(), name="c2", bench_storage_id=None)
    idx = await storage_store.usage_index(_db([recipe], [dep], [cluster, no_bench]))

    assert idx[SID]["recipes"] == [{"id": str(rid), "name": "r1"}]
    assert len(idx[SID]["deployments"]) == 1
    item = idx[SID]["deployments"][0]
    assert item["id"] == str(did) and item["model_name"] == "m"
    assert item["binding_ids"] == {"b1", "b2"}
    assert item["cluster_id"] == CLUSTER and item["namespace"] == "prod"
    assert [e["mount_path"] for e in item["entries"]] == ["/a", "/b"]
    assert idx[SID2]["clusters"] == [{"id": str(cid), "name": "c1"}]
    assert idx[SID2]["recipes"] == [] and idx[SID2]["deployments"] == []
    assert set(idx) == {SID, SID2}


async def test_check_known_raises_with_missing_ids():
    with patch.object(storage_store, "load_storages", AsyncMock(return_value={SID: _storage()})):
        await storage_store.check_known(None, [{"storage_id": SID}])
        with pytest.raises(UnknownStorageError) as e:
            await storage_store.check_known(None, [{"storage_id": SID}, {"storage_id": SID2}])
    assert e.value.ids == [SID2]


async def test_match_claims_maps_claim_to_storage_id():
    rows = [binding(claim_name="a", storage_id=uuid.UUID(SID)), binding(claim_name="b", storage_id=uuid.UUID(SID2))]
    assert await storage_store.match_claims(_db(rows), CLUSTER, "prod", ["a", "b"]) == {"a": SID, "b": SID2}
    assert await storage_store.match_claims(None, CLUSTER, "prod", []) == {}


async def test_list_pvcs_timed_raises_timeout():
    import asyncio

    async def slow(_ns):
        await asyncio.sleep(1)

    k8s = MagicMock(list_pvcs=slow)
    with (
        patch.object(storage_store, "k8s_for_cluster", AsyncMock(return_value=k8s)),
        patch.object(storage_store, "PVC_LOOKUP_TIMEOUT", 0.01),
        pytest.raises(TimeoutError),
    ):
        await storage_store.list_pvcs_timed(None, CLUSTER, "prod")


async def test_resolve_collects_unresolved_as_placeholders_when_asked():
    b = binding(cluster_id=CLUSTER, namespace="prod")
    p1, p2 = _patch([_storage(), _storage(SID2, "hf-cache")], [b])
    unresolved: list[str] = []
    with p1, p2:
        out = await resolve_entries(
            None, [{"storage_id": SID, "mount_path": "/a"}, {"storage_id": SID2, "mount_path": "/b"}],
            CLUSTER, "prod", unresolved_out=unresolved,
        )
    assert unresolved == ["hf-cache"]
    assert out[0]["resolved"] == storage_store.snapshot(b)
    assert out[1]["volume_name"] == "st-hf-cache"
    assert out[1]["resolved"] == {"binding_id": None, "type": "pvc", "claim_name": "<unbound:hf-cache>"}
