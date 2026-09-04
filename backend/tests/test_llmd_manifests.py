import types
import uuid

from app.config import settings
from app.services.llmd_manifests import (
    MANAGED_BY,
    argo_app_name_for,
    build_argo_application,
    build_direct_ingress,
    build_direct_service,
    build_llmd_ingress,
    build_llmd_values,
    deep_merge,
    default_llmd_values,
    direct_service_name,
    llmd_service_name,
    modelservers_target,
)


def _stack(**kw):
    base = dict(
        id=uuid.uuid4(),
        name="my-stack",
        target_model_name="opt-125m",
        namespace="llmd-my-stack",
        argo_app_name="llmd-my-stack",
        helm_values={},
        epp_registry=None,
        epp_repository=None,
        epp_tag=None,
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_llmd_settings_target_standalone_chart():
    assert settings.argo_project == "llm-d"
    assert settings.llmd_chart_name == "llm-d-router-standalone"
    assert settings.llmd_chart_version == "v0.9.0"
    assert "llm-d" in settings.llmd_chart_repo
    # Real llm-d EPP image, on ghcr.io (overridable for air-gap).
    assert settings.llmd_epp_image_registry == "ghcr.io"
    assert settings.llmd_epp_image_repository == "llm-d/llm-d-router-endpoint-picker"
    assert settings.llmd_epp_image_tag == "v0.9.0"


def test_argo_app_name_is_sanitised():
    assert argo_app_name_for("My_Stack.1") == "llmd-my-stack-1"


def test_deep_merge_override_wins_and_nests():
    base = {"a": {"x": 1, "y": 2}, "b": 1}
    out = deep_merge(base, {"a": {"y": 9, "z": 3}, "c": 4})
    assert out == {"a": {"x": 1, "y": 9, "z": 3}, "b": 1, "c": 4}
    assert base == {"a": {"x": 1, "y": 2}, "b": 1}  # base untouched


def test_build_values_merges_epp_image_base_under_helm_values():
    v = build_llmd_values(
        _stack(),
        epp_registry="reg.local",
        epp_repository="llm-d/llm-d-router-endpoint-picker",
        epp_tag="v0.8.1",
    )
    assert v["router"]["epp"]["image"] == {
        "registry": "reg.local",
        "repository": "llm-d/llm-d-router-endpoint-picker",
        "tag": "v0.8.1",
    }


def test_build_values_user_helm_values_win_over_base():
    v = build_llmd_values(
        _stack(
            helm_values={
                "router": {"epp": {"image": {"tag": "custom"}}},
                "tracing": {"enabled": True},
            }
        ),
        epp_registry="reg.local",
        epp_repository="llm-d/llm-d-router-endpoint-picker",
        epp_tag="v0.8.1",
    )
    img = v["router"]["epp"]["image"]
    assert img["registry"] == "reg.local"
    assert img["repository"] == "llm-d/llm-d-router-endpoint-picker"
    assert img["tag"] == "custom"  # user wins
    assert v["tracing"] == {"enabled": True}


def test_build_values_stack_epp_override_wins_over_baked_helm_values():
    # Create seeds helm_values via default_llmd_values(), which bakes the
    # global EPP image into the user's YAML. An explicit per-stack override
    # (the epp_* columns) must still reach the rendered values — otherwise
    # the air-gap image fields on the form are dead.
    helm_values = default_llmd_values(
        "m",
        epp_registry="ghcr.io",
        epp_repository="llm-d/llm-d-router-endpoint-picker",
        epp_tag="v0.9.0",
    )
    v = build_llmd_values(
        _stack(
            helm_values=helm_values,
            epp_registry="mirror.internal",
            epp_tag="v0.9.0-airgap",
        ),
        epp_registry="mirror.internal",
        epp_repository="llm-d/llm-d-router-endpoint-picker",
        epp_tag="v0.9.0-airgap",
    )
    assert v["router"]["epp"]["image"] == {
        "registry": "mirror.internal",
        "repository": "llm-d/llm-d-router-endpoint-picker",
        "tag": "v0.9.0-airgap",
    }


def test_default_values_is_real_router_template():
    v = default_llmd_values(
        "opt-125m",
        epp_registry="reg.local",
        epp_repository="llm-d/llm-d-router-endpoint-picker",
        epp_tag="v0.8.1",
    )
    router = v["router"]
    # llm-d EPP image (not vanilla GIE)
    assert router["epp"]["image"] == {
        "registry": "reg.local",
        "repository": "llm-d/llm-d-router-endpoint-picker",
        "tag": "v0.8.1",
    }
    # Target existing model servers via matchLabels; don't create an InferencePool
    ms = router["modelServers"]
    assert router["inferencePool"]["create"] is False
    assert ms["matchLabels"] == {"llm-ops/model-name": "opt-125m"}
    assert ms["targetPorts"] == [{"number": 8000}]
    assert ms["type"] == "vllm"
    # The Envoy sidecar + scorers come from chart defaults — we don't emit proxy.
    assert "proxy" not in router


def test_default_values_blank_model_yields_empty_selector():
    v = default_llmd_values(
        "",
        epp_registry="reg.local",
        epp_repository="llm-d/llm-d-router-endpoint-picker",
        epp_tag="v0.8.1",
    )
    assert v["router"]["modelServers"]["matchLabels"] == {}


def test_build_application_is_isolated_to_project_and_namespace():
    app = build_argo_application(
        _stack(),
        chart_repo="oci://reg.local/charts",
        chart_name="llm-d-stack",
        chart_version="0.7.0",
        values={"replicas": 2},
        project="llm-d",
        argocd_namespace="argocd",
        destination_server="https://kubernetes.default.svc",
    )
    assert app["apiVersion"] == "argoproj.io/v1alpha1"
    assert app["kind"] == "Application"
    # Isolation guardrails:
    assert app["spec"]["project"] == "llm-d"  # NOT "default"
    assert app["spec"]["destination"]["namespace"] == "llmd-my-stack"
    assert app["metadata"]["namespace"] == "argocd"
    assert app["metadata"]["labels"]["app.kubernetes.io/managed-by"] == MANAGED_BY
    src = app["spec"]["source"]
    assert src["repoURL"] == "reg.local/charts"  # oci:// stripped for ArgoCD 3.x
    assert src["chart"] == "llm-d-stack"
    assert src["targetRevision"] == "0.7.0"
    assert src["helm"]["releaseName"] == "llmd-my-stack"
    assert src["helm"]["valuesObject"] == {"replicas": 2}
    assert app["spec"]["syncPolicy"]["automated"] == {"prune": True, "selfHeal": True}


def test_default_values_uses_explicit_endpoint_selector():
    v = default_llmd_values(
        "qwen", epp_registry="r", epp_repository="repo", epp_tag="t",
        endpoint_selector="app=my-vllm",
    )
    assert v["router"]["modelServers"]["matchLabels"] == {"app": "my-vllm"}


def test_default_values_falls_back_to_model_label():
    v = default_llmd_values("qwen", epp_registry="r", epp_repository="repo", epp_tag="t")
    assert v["router"]["modelServers"]["matchLabels"] == {"llm-ops/model-name": "qwen"}


def test_application_destination_server_configurable():
    stack = _stack()
    app = build_argo_application(
        stack,
        chart_repo="oci://repo",
        chart_name="llmd",
        chart_version="1.0.0",
        values={},
        project="llm-d",
        argocd_namespace="argocd",
        destination_server="https://10.0.0.9:6443",
    )
    assert app["spec"]["destination"] == {
        "server": "https://10.0.0.9:6443",
        "namespace": stack.namespace,
    }


def test_llmd_service_name_is_release_epp():
    assert llmd_service_name(_stack()) == "llmd-my-stack-epp"


def test_build_ingress_shape_and_backend():
    ing = build_llmd_ingress(
        _stack(), host="myrouter.corp.internal", ingress_class="nginx", ingress_path="/"
    )
    assert ing["apiVersion"] == "networking.k8s.io/v1"
    assert ing["kind"] == "Ingress"
    assert ing["metadata"]["name"] == "llmd-my-stack-ingress"
    assert ing["metadata"]["namespace"] == "llmd-my-stack"
    assert ing["metadata"]["labels"]["app.kubernetes.io/managed-by"] == "litellm-portal"
    rule = ing["spec"]["rules"][0]
    # host is used verbatim — the caller resolves override vs {app}.{domain}
    assert rule["host"] == "myrouter.corp.internal"
    path = rule["http"]["paths"][0]
    assert path["path"] == "/"
    assert path["pathType"] == "Prefix"
    assert path["backend"]["service"]["name"] == "llmd-my-stack-epp"
    assert path["backend"]["service"]["port"] == {"name": "http"}
    assert ing["spec"]["ingressClassName"] == "nginx"


def test_build_ingress_omits_class_when_empty():
    ing = build_llmd_ingress(
        _stack(), host="llmd-my-stack.llm-d.local", ingress_class="", ingress_path="/"
    )
    assert "ingressClassName" not in ing["spec"]
    assert ing["spec"]["rules"][0]["host"] == "llmd-my-stack.llm-d.local"


def test_build_ingress_respects_path():
    ing = build_llmd_ingress(
        _stack(), host="llmd-my-stack.llm-d.local", ingress_class="nginx", ingress_path="/router"
    )
    assert ing["spec"]["rules"][0]["http"]["paths"][0]["path"] == "/router"


def test_direct_service_name():
    assert direct_service_name(_stack()) == "llmd-my-stack-direct"


def test_build_direct_service_shape():
    svc = build_direct_service(_stack(), match_labels={"app": "vllm"}, target_port=8000)
    assert svc["apiVersion"] == "v1"
    assert svc["kind"] == "Service"
    assert svc["metadata"]["name"] == "llmd-my-stack-direct"
    assert svc["metadata"]["namespace"] == "llmd-my-stack"
    assert svc["metadata"]["labels"]["app.kubernetes.io/managed-by"] == MANAGED_BY
    spec = svc["spec"]
    assert spec["type"] == "ClusterIP"
    assert "sessionAffinity" not in spec
    assert spec["selector"] == {"app": "vllm"}
    assert spec["ports"] == [{"name": "http", "port": 80, "targetPort": 8000, "protocol": "TCP"}]


def test_build_direct_service_uses_given_target_port():
    svc = build_direct_service(_stack(), match_labels={"app": "vllm"}, target_port=8001)
    assert svc["spec"]["ports"][0]["targetPort"] == 8001


def test_build_direct_ingress_backend_is_direct_service():
    ing = build_direct_ingress(
        _stack(), host="direct.corp.internal", ingress_class="nginx", ingress_path="/"
    )
    assert ing["kind"] == "Ingress"
    assert ing["metadata"]["name"] == "llmd-my-stack-direct-ingress"
    assert ing["metadata"]["labels"]["app.kubernetes.io/managed-by"] == MANAGED_BY
    rule = ing["spec"]["rules"][0]
    assert rule["host"] == "direct.corp.internal"
    backend = rule["http"]["paths"][0]["backend"]["service"]
    assert backend["name"] == "llmd-my-stack-direct"
    assert backend["port"] == {"number": 80}
    assert ing["spec"]["ingressClassName"] == "nginx"


def test_build_direct_ingress_omits_class_when_empty():
    ing = build_direct_ingress(_stack(), host="x", ingress_class="", ingress_path="/")
    assert "ingressClassName" not in ing["spec"]


def test_modelservers_target_extracts_labels_and_port():
    values = {"router": {"modelServers": {"matchLabels": {"app": "vllm"}, "targetPorts": [{"number": 8000}]}}}
    assert modelservers_target(values) == ({"app": "vllm"}, 8000)


def test_modelservers_target_defaults_when_missing():
    assert modelservers_target({}) == ({}, 8000)
    assert modelservers_target({"router": {"modelServers": {}}}) == ({}, 8000)
    assert modelservers_target({"router": {"modelServers": {"targetPorts": []}}}) == ({}, 8000)
