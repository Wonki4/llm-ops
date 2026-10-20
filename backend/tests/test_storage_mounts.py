# backend/tests/test_storage_mounts.py
"""Pure storage helpers: validation, resolution order, volume rendering."""

import types

import pytest

from app.services.storage_mounts import (
    all_entries,
    check_volume_names,
    default_volume_name,
    entry_storage_ids,
    legacy_pvc_volumes,
    model_path_warnings,
    nfs_override_volumes,
    pick_binding,
    snapshot,
    stale_entries,
    to_volumes,
    validate_entries,
    validate_storage_name,
)
from tests._storage import CLUSTER, SID, SID2, binding, legacy_entry

# ─── names ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["llama-weights", "a", "hf-cache-2", "x" * 50])
def test_storage_name_ok(name):
    assert validate_storage_name(name) == name


@pytest.mark.parametrize("name", ["", "Llama", "-a", "a-", "a_b", "x" * 51, "a b"])
def test_storage_name_rejected(name):
    with pytest.raises(ValueError):
        validate_storage_name(name)


def test_default_volume_name_fits_dns_label():
    assert default_volume_name("x" * 50) == "st-" + "x" * 50
    assert len(default_volume_name("x" * 50)) <= 63


# ─── entries ──────────────────────────────────────────────────────────────────


def test_validate_normalises_and_strips_resolution():
    out = validate_entries(
        [{"storage_id": SID, "mount_path": "/models/", "volume_name": "x", "resolved": {"type": "pvc"}}]
    )
    assert out == [{"storage_id": SID, "mount_path": "/models", "read_only": True}]


def test_validate_keeps_explicit_read_write():
    assert validate_entries([{"storage_id": SID, "mount_path": "/data", "read_only": False}])[0]["read_only"] is False


@pytest.mark.parametrize(
    "raw",
    [
        [{"storage_id": "nope", "mount_path": "/m"}],
        [{"storage_id": SID, "mount_path": "relative"}],
        [{"storage_id": SID, "mount_path": "/"}],
        [{"storage_id": SID, "mount_path": "/dev/shm"}],
        [{"storage_id": SID, "mount_path": "/dev"}],
        [{"storage_id": SID, "mount_path": "/a"}, {"storage_id": SID, "mount_path": "/b"}],
        ["not-an-object"],
    ],
)
def test_validate_rejects(raw):
    with pytest.raises(ValueError):
        validate_entries(raw)


def test_validate_rejects_nested_mounts():
    nested = [{"storage_id": SID, "mount_path": "/models"}, {"storage_id": SID2, "mount_path": "/models/cache"}]
    with pytest.raises(ValueError, match="overlap"):
        validate_entries(nested)
    # siblings sharing a prefix string are fine
    siblings = [{"storage_id": SID, "mount_path": "/models"}, {"storage_id": SID2, "mount_path": "/models2"}]
    assert len(validate_entries(siblings)) == 2


def test_validate_empty():
    assert validate_entries(None) == [] and validate_entries([]) == []


def test_model_path_warnings():
    entries = [{"storage_id": SID, "mount_path": "/models", "read_only": True}]
    assert model_path_warnings("/models/llama", entries) == []
    assert model_path_warnings("/models", entries) == []
    assert model_path_warnings("/data/llama", entries) == ["model_path_outside_storages"]
    assert model_path_warnings("meta-llama/Llama-3-8B", entries) == []  # HF repo id
    assert model_path_warnings("/models2/x", entries) == ["model_path_outside_storages"]


# ─── binding pick ─────────────────────────────────────────────────────────────


def test_pick_exact_namespace_wins_over_cluster_wide():
    wide = binding(cluster_id=CLUSTER, namespace=None, type="nfs", claim_name=None, nfs_server="nfs", nfs_path="/x")
    exact = binding(cluster_id=CLUSTER, namespace="prod")
    assert pick_binding([wide, exact], SID, CLUSTER, "prod") is exact
    assert pick_binding([wide, exact], SID, CLUSTER, "dev") is wide


def test_pick_none_when_cluster_or_storage_differs():
    b = binding(cluster_id=CLUSTER, namespace="prod")
    assert pick_binding([b], SID, None, "prod") is None  # portal default ≠ registered cluster
    assert pick_binding([b], SID2, CLUSTER, "prod") is None
    assert pick_binding([binding(cluster_id=None)], SID, None, "default") is not None


def test_snapshot_shape():
    b = binding()
    assert snapshot(b) == {
        "binding_id": str(b.id), "type": "pvc", "claim_name": "models-pvc", "namespace": "default",
        "nfs_server": None, "nfs_path": None, "sub_path": None,
    }


# ─── rendering ────────────────────────────────────────────────────────────────


def test_legacy_entry_renders_like_old_pvc_columns():
    vols, mounts = to_volumes([legacy_entry("models-pvc", "/models")])
    assert vols == [{"name": "model-weights", "persistentVolumeClaim": {"claimName": "models-pvc"}}]
    assert mounts == [{"name": "model-weights", "mountPath": "/models"}]


def test_read_only_nfs_with_sub_path():
    e = {
        "storage_id": SID, "mount_path": "/cache", "read_only": True, "volume_name": "st-hf-cache",
        "resolved": {"binding_id": "b", "type": "nfs", "claim_name": None, "namespace": None,
                     "nfs_server": "10.0.0.5", "nfs_path": "/export", "sub_path": "hf"},
    }
    vols, mounts = to_volumes([e])
    assert vols == [{"name": "st-hf-cache", "nfs": {"server": "10.0.0.5", "path": "/export", "readOnly": True}}]
    assert mounts == [{"name": "st-hf-cache", "mountPath": "/cache", "readOnly": True, "subPath": "hf"}]


def test_force_read_only_matches_old_bench_mount():
    vols, mounts = to_volumes([legacy_entry("models-pvc", "/models")], force_read_only=True)
    assert vols == [{"name": "model-weights", "persistentVolumeClaim": {"claimName": "models-pvc", "readOnly": True}}]
    assert mounts == [{"name": "model-weights", "mountPath": "/models", "readOnly": True}]


def test_unresolved_entries_are_not_rendered():
    assert to_volumes([{"storage_id": SID, "mount_path": "/m", "read_only": True}]) == ([], [])


def test_legacy_and_nfs_override_helpers():
    assert legacy_pvc_volumes("c", "/m") == (
        [{"name": "model-weights", "persistentVolumeClaim": {"claimName": "c", "readOnly": True}}],
        [{"name": "model-weights", "mountPath": "/m", "readOnly": True}],
    )
    assert legacy_pvc_volumes(None, "/m") == ([], [])
    assert nfs_override_volumes({"nfs_server": "s", "nfs_path": "/p", "nfs_mount_path": "/m"}) == (
        [{"name": "model-weights", "nfs": {"server": "s", "path": "/p", "readOnly": True}}],
        [{"name": "model-weights", "mountPath": "/m", "readOnly": True}],
    )
    assert nfs_override_volumes({"nfs_server": "s"}) is None
    assert nfs_override_volumes(None) is None


def test_all_entries_and_ids_include_pool_overrides():
    base = {"storage_id": SID, "mount_path": "/m"}
    pool = {"storage_id": SID2, "mount_path": "/m"}
    row = types.SimpleNamespace(storages=[base], pd_config={"prefill": {"storages": [pool]}, "decode": {}})
    assert all_entries(row) == [base, pool]
    assert entry_storage_ids(row) == {SID, SID2}
    empty = types.SimpleNamespace(storages=None, pd_config=None)
    assert all_entries(empty) == [] and entry_storage_ids(empty) == set()


def test_stale_entries():
    wide = binding(cluster_id=CLUSTER, namespace=None, type="nfs", claim_name=None, nfs_server="s", nfs_path="/p")
    entry = {"storage_id": SID, "mount_path": "/m", "read_only": True, "volume_name": "st-x",
             "resolved": snapshot(wide)}
    assert stale_entries([entry], [wide], CLUSTER, "prod") == []
    exact = binding(cluster_id=CLUSTER, namespace="prod")
    assert stale_entries([entry, entry], [wide, exact], CLUSTER, "prod") == [SID]  # shadowed, reported once
    assert stale_entries([entry], [], CLUSTER, "prod") == [SID]  # binding deleted


def test_check_volume_names():
    check_volume_names([legacy_entry("a", "/a"), {**legacy_entry("b", "/b", storage_id=SID2), "volume_name": "st-b"}])
    with pytest.raises(ValueError, match="reserved"):
        check_volume_names([{**legacy_entry("a", "/a"), "volume_name": "shm"}])
    with pytest.raises(ValueError, match="twice"):
        check_volume_names([legacy_entry("a", "/a"), legacy_entry("b", "/b", storage_id=SID2)])
