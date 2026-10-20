"""Storage tables + storages columns."""

from app.db.models.custom_k8s_cluster import CustomK8sCluster
from app.db.models.custom_model_deployment import CustomModelDeployment
from app.db.models.custom_serving_recipe import CustomServingRecipe
from app.db.models.custom_storage import CustomStorage, CustomStorageBinding


def test_storage_columns():
    cols = CustomStorage.__table__.columns
    assert cols["name"].unique is True
    assert cols["kind"].server_default.arg == "weights"


def test_binding_scope_unique_nulls_not_distinct():
    uq = next(c for c in CustomStorageBinding.__table__.constraints if c.name == "uq_storage_binding_scope")
    assert [c.name for c in uq.columns] == ["storage_id", "cluster_id", "namespace"]
    assert uq.dialect_options["postgresql"]["nulls_not_distinct"] is True
    ck = next(c for c in CustomStorageBinding.__table__.constraints if c.name == "ck_storage_binding_target")
    assert "claim_name IS NOT NULL" in str(ck.sqltext)
    cols = CustomStorageBinding.__table__.columns
    assert cols["cluster_id"].nullable is True
    assert next(iter(cols["storage_id"].foreign_keys)).ondelete == "CASCADE"


def test_storages_json_columns():
    for model in (CustomServingRecipe, CustomModelDeployment):
        col = model.__table__.columns["storages"]
        assert col.nullable is False
        assert col.server_default.arg == "[]"


def test_cluster_bench_storage():
    cols = CustomK8sCluster.__table__.columns
    assert next(iter(cols["bench_storage_id"].foreign_keys)).ondelete == "SET NULL"
    assert cols["bench_mount_path"].nullable is True
