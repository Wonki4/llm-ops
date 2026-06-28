import types
import uuid

from app.config import settings
from app.db.models.custom_llmd_stack import CustomLlmdStack
from app.services.llmd_manifests import (
    MANAGED_BY,
    argo_app_name_for,
    build_argo_application,
    build_llmd_values,
    deep_merge,
    default_llmd_values,
)


def test_model_has_expected_columns():
    cols = set(CustomLlmdStack.__table__.columns.keys())
    assert {
        "id", "name", "target_model_name", "argocd_connection_id", "cluster_id",
        "namespace", "argo_app_name", "helm_values", "values_snapshot",
        "created_by", "updated_by", "created_at", "updated_at",
    } <= cols
    # Structured per-field columns were collapsed into helm_values in #174.
    assert not (
        {"replicas", "model_server_type", "target_port", "endpoint_selector", "values_override"} & cols
    )


def test_llmd_settings_target_standalone_chart():
    assert settings.argo_project == "llm-d"
    assert settings.llmd_chart_name == "standalone"
    assert settings.llmd_chart_version == "v1.5.0"
    assert "gateway-api-inference-extension" in settings.llmd_chart_repo
    # Real llm-d EPP image, on ghcr.io (overridable for air-gap).
    assert settings.llmd_epp_image_registry == "ghcr.io"
    assert settings.llmd_epp_image_repository == "llm-d/llm-d-router-endpoint-picker"
    assert settings.llmd_epp_image_tag == "v0.8.1"


def _stack(**kw):
    base = dict(
        id=uuid.uuid4(), name="my-stack", target_model_name="opt-125m",
        namespace="llmd-my-stack", argo_app_name="llmd-my-stack", helm_values={},
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_argo_app_name_is_sanitised():
    assert argo_app_name_for("My_Stack.1") == "llmd-my-stack-1"


def test_deep_merge_override_wins_and_nests():
    base = {"a": {"x": 1, "y": 2}, "b": 1}
    out = deep_merge(base, {"a": {"y": 9, "z": 3}, "c": 4})
    assert out == {"a": {"x": 1, "y": 9, "z": 3}, "b": 1, "c": 4}
    assert base == {"a": {"x": 1, "y": 2}, "b": 1}  # base untouched


def test_build_values_merges_epp_image_base_under_helm_values():
    v = build_llmd_values(
        _stack(), epp_registry="reg.local",
        epp_repository="llm-d/llm-d-router-endpoint-picker", epp_tag="v0.8.1",
    )
    assert v["inferenceExtension"]["image"] == {
        "registry": "reg.local", "repository": "llm-d/llm-d-router-endpoint-picker", "tag": "v0.8.1",
    }


def test_build_values_user_helm_values_win_over_base():
    v = build_llmd_values(
        _stack(helm_values={"inferenceExtension": {"image": {"tag": "custom"}}, "tracing": {"enabled": True}}),
        epp_registry="reg.local",
        epp_repository="llm-d/llm-d-router-endpoint-picker", epp_tag="v0.8.1",
    )
    img = v["inferenceExtension"]["image"]
    assert img["registry"] == "reg.local"
    assert img["repository"] == "llm-d/llm-d-router-endpoint-picker"
    assert img["tag"] == "custom"           # user wins
    assert v["tracing"] == {"enabled": True}


def test_default_values_is_real_router_template():
    v = default_llmd_values(
        "opt-125m", epp_registry="reg.local",
        epp_repository="llm-d/llm-d-router-endpoint-picker", epp_tag="v0.8.1",
    )
    ie = v["inferenceExtension"]
    # llm-d EPP image (not vanilla GIE)
    assert ie["image"] == {
        "registry": "reg.local",
        "repository": "llm-d/llm-d-router-endpoint-picker",
        "tag": "v0.8.1",
    }
    # Target existing model servers; don't create an InferencePool
    es = ie["endpointsServer"]
    assert es["createInferencePool"] is False
    assert es["endpointSelector"] == "llm-ops/model-name=opt-125m"
    assert es["targetPorts"] == 8000
    assert es["modelServerType"] == "vllm"
    # A1: sidecar + scorers come from the chart defaults — these values keys do
    # NOT exist in the GIE standalone chart, so we must not emit them.
    assert "proxy" not in ie
    assert "plugins" not in ie


def test_default_values_blank_model_yields_empty_selector():
    v = default_llmd_values(
        "", epp_registry="reg.local",
        epp_repository="llm-d/llm-d-router-endpoint-picker", epp_tag="v0.8.1",
    )
    assert v["inferenceExtension"]["endpointsServer"]["endpointSelector"] == ""


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


def test_serialize_includes_epp_image():
    from app.api.llmd import _serialize

    stack = _stack(
        argocd_connection_id=None, cluster_id=None, helm_values={"a": 1},
        created_by=None, created_at=None, updated_at=None,
    )
    out = _serialize(stack, {"sync_status": "Synced", "health_status": "Healthy", "status_message": None})
    assert out["epp_image"] == "ghcr.io/llm-d/llm-d-router-endpoint-picker:v0.8.1"


def test_argo_status_extracts_sync_and_health():
    from app.api.llmd import _argo_status

    obj = {"status": {"sync": {"status": "Synced"}, "health": {"status": "Healthy", "message": "ok"}}}
    assert _argo_status(obj) == {"sync_status": "Synced", "health_status": "Healthy", "status_message": "ok"}


def test_argo_status_unknown_when_missing():
    from app.api.llmd import _argo_status

    assert _argo_status(None) == {"sync_status": "Unknown", "health_status": "Unknown", "status_message": None}


def test_preview_yaml_renders_application_round_trip():
    import yaml

    from app.services.yaml_block import dump_block_yaml

    app = build_argo_application(
        _stack(), chart_repo="oci://reg.local/charts", chart_name="llm-d-stack",
        chart_version="0.7.0", values={"replicas": 2}, project="llm-d",
    )
    text = dump_block_yaml(app)
    assert "kind: Application" in text
    assert "project: llm-d" in text
    # Valid YAML that round-trips to the same object.
    assert yaml.safe_load(text) == app
