"""Pure helpers of migration 052."""

import importlib.util
import pathlib

from app.services.storage_mounts import validate_storage_name

_path = pathlib.Path(__file__).parents[1] / "migrations/versions/052_storages_backfill.py"
_spec = importlib.util.spec_from_file_location("m052", _path)
m052 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m052)


def test_slug():
    assert m052._slug("Models_PVC.v2") == "models-pvc-v2"
    assert m052._slug("---") == "storage"
    assert len(m052._slug("x" * 80)) <= 40


def test_unique_name():
    taken = {"pvc-models"}
    assert m052._unique("pvc-models", taken) == "pvc-models-2"
    assert "pvc-models-2" in taken


def test_generated_names_pass_the_storage_name_rule():
    long = "c" * 128  # custom_k8s_cluster.name is String(128)
    assert len(m052._bench_name(long)) == 50
    validate_storage_name(m052._bench_name(long))
    validate_storage_name(m052._bench_name("GPU_Cluster.A"))
    validate_storage_name(m052._unique(f"pvc-{m052._slug('x' * 80)}", {f"pvc-{'x' * 40}"}))


def test_pool_pvcs_fall_back_per_field_to_the_base():
    # Old pool_row fell back per field, so a pool that set only one key mounted it
    # together with the base's other key — the migration must reproduce exactly that.
    cfg = {"prefill": {"pvc_name": "p", "pvc_mount_path": "/m", "replicas": 1}, "decode": {"pvc_name": "d"}}
    assert m052._pool_pvcs(cfg, "base", "/models") == {"prefill": ("p", "/m"), "decode": ("d", "/models")}
    assert m052._pool_pvcs({"decode": {"pvc_mount_path": "/x"}}, "base", "/models") == {"decode": ("base", "/x")}
    # No complete pair even with the fallback → the old renderer mounted nothing for that pool.
    assert m052._pool_pvcs({"decode": {"pvc_name": "d"}}, None, None) == {"decode": None}
    assert m052._pool_pvcs({"decode": {"replicas": 2}}, "base", "/models") == {}
    assert m052._pool_pvcs(None, "base", "/models") == {}


def test_strip_pool_pvcs():
    cfg = {"prefill": {"pvc_name": "p", "pvc_mount_path": "/m", "replicas": 1}, "decode": {"pvc_name": "d"}}
    assert m052._strip_pool_pvcs(cfg) == {"prefill": {"replicas": 1}, "decode": {}}
    assert m052._strip_pool_pvcs(None) is None


def test_migrated_claim_round_trips_the_description():
    # Recipe pool entries have no binding/snapshot; downgrade recovers the claim from
    # the description upgrade wrote, or a recipe's P/D pool loses its pvc_* keys.
    assert m052._migrated_claim(m052._description("prefill-pvc")) == "prefill-pvc"
    assert m052._migrated_claim("Migrated from cluster x default NFS") is None
    assert m052._migrated_claim(None) is None
