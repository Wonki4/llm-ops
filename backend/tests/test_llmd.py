from app.db.models.custom_llmd_stack import CustomLlmdStack


def test_model_has_expected_columns():
    cols = set(CustomLlmdStack.__table__.columns.keys())
    assert {
        "id", "name", "model_ref", "served_model_name", "cluster_id",
        "namespace", "argo_app_name", "replicas", "gpu_count",
        "gpu_resource_key", "values_snapshot", "created_by", "updated_by",
        "created_at", "updated_at",
    } <= cols
