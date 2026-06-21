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
)


def test_model_has_expected_columns():
    cols = set(CustomLlmdStack.__table__.columns.keys())
    assert {
        "id", "name", "target_model_name", "argocd_connection_id", "cluster_id",
        "namespace", "argo_app_name", "replicas", "model_server_type", "target_port",
        "endpoint_selector", "values_override", "values_snapshot",
        "created_by", "updated_by", "created_at", "updated_at",
    } <= cols
    # Retargeted at an existing model — no provisioning columns.
    assert not ({"served_model_name", "gpu_count", "gpu_resource_key", "model_ref"} & cols)


def test_llmd_settings_target_standalone_chart():
    assert settings.argo_project == "llm-d"
    assert settings.llmd_chart_name == "standalone"
    assert settings.llmd_chart_version == "v1.5.0"
    assert "gateway-api-inference-extension" in settings.llmd_chart_repo


def _stack(**kw):
    base = dict(
        id=uuid.uuid4(), name="my-stack", target_model_name="opt-125m",
        namespace="llmd-my-stack", replicas=2, argo_app_name="llmd-my-stack",
        model_server_type="vllm", target_port=8000, endpoint_selector=None,
        values_override={},
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


def test_build_values_standalone_schema_and_default_selector():
    v = build_llmd_values(_stack(), image_registry="reg.local")
    es = v["inferenceExtension"]["endpointsServer"]
    assert v["inferenceExtension"]["replicas"] == 2
    assert v["inferenceExtension"]["image"]["registry"] == "reg.local"
    assert es["endpointSelector"] == "llm-ops/model-name=opt-125m"  # default from model
    assert es["targetPorts"] == 8000
    assert es["modelServerType"] == "vllm"


def test_build_values_custom_selector_and_override():
    v = build_llmd_values(
        _stack(
            endpoint_selector="app=my-vllm", model_server_type="sglang", target_port=9000,
            values_override={"inferenceExtension": {"image": {"tag": "v9"}}, "tracing": {"enabled": True}},
        ),
        image_registry="reg.local",
    )
    es = v["inferenceExtension"]["endpointsServer"]
    assert es["endpointSelector"] == "app=my-vllm"
    assert es["modelServerType"] == "sglang"
    assert es["targetPorts"] == 9000
    # Override deep-merges: keeps generated registry, adds tag + new top-level key.
    assert v["inferenceExtension"]["image"] == {"registry": "reg.local", "tag": "v9"}
    assert v["tracing"] == {"enabled": True}


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
