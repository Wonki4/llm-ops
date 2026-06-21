import types
import uuid

from app.config import settings
from app.db.models.custom_llmd_stack import CustomLlmdStack
from app.services.llmd_manifests import (
    MANAGED_BY,
    argo_app_name_for,
    build_argo_application,
    build_llmd_values,
)


def test_model_has_expected_columns():
    cols = set(CustomLlmdStack.__table__.columns.keys())
    assert {
        "id", "name", "model_ref", "served_model_name", "cluster_id",
        "namespace", "argo_app_name", "replicas", "gpu_count",
        "gpu_resource_key", "values_snapshot", "created_by", "updated_by",
        "created_at", "updated_at",
    } <= cols


def test_llmd_settings_internal_defaults():
    assert settings.argo_project == "llm-d"
    assert settings.llmd_hf_secret_name
    assert "registry.k8s.io" not in settings.llmd_chart_repo


def _stack(**kw):
    base = dict(
        id=uuid.uuid4(), name="my-stack", model_ref="facebook/opt-125m",
        served_model_name="opt-125m", namespace="llmd-my-stack", replicas=2,
        gpu_count=1, gpu_resource_key="nvidia.com/gpu",
        argo_app_name="llmd-my-stack",
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_argo_app_name_is_sanitised():
    assert argo_app_name_for("My_Stack.1") == "llmd-my-stack-1"


def test_build_values_uses_internal_registry_and_secret():
    v = build_llmd_values(_stack(), image_registry="reg.local", hf_secret_name="hf")
    assert v["model"] == {"id": "facebook/opt-125m", "servedName": "opt-125m"}
    assert v["replicas"] == 2
    assert v["resources"]["gpu"] == {"count": 1, "resourceKey": "nvidia.com/gpu"}
    assert v["image"]["registry"] == "reg.local"
    assert v["hfTokenSecret"] == "hf"


def test_build_application_is_isolated_to_project_and_namespace():
    app = build_argo_application(
        _stack(), chart_repo="oci://reg.local/charts", chart_name="llm-d-stack",
        chart_version="0.7.0", values={"replicas": 2}, project="llm-d",
    )
    assert app["apiVersion"] == "argoproj.io/v1alpha1"
    assert app["kind"] == "Application"
    # Isolation guardrails:
    assert app["spec"]["project"] == "llm-d"  # NOT "default"
    assert app["spec"]["destination"]["namespace"] == "llmd-my-stack"
    assert app["metadata"]["labels"]["app.kubernetes.io/managed-by"] == MANAGED_BY
    src = app["spec"]["source"]
    assert src["repoURL"] == "oci://reg.local/charts"
    assert src["chart"] == "llm-d-stack"
    assert src["targetRevision"] == "0.7.0"
    assert src["helm"]["valuesObject"] == {"replicas": 2}
    assert app["spec"]["syncPolicy"]["automated"] == {"prune": True, "selfHeal": True}
