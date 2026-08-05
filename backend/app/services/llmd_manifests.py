"""Pure builders for an llm-d stack's ArgoCD Application + Helm values.

The portal renders per-model values and wraps them in an argoproj.io Application
that points at the internal llm-d Helm chart. The Application is scoped to a
dedicated AppProject and a per-stack namespace, and labelled managed-by the
portal, so it can never affect other projects' applications. ArgoCD reconciles
it; the values schema here is the contract with the internal chart.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.services.model_deployment_manifests import LABEL_MODEL

if TYPE_CHECKING:
    from app.db.models.custom_llmd_stack import CustomLlmdStack

MANAGED_BY = "litellm-portal"


def argo_app_name_for(name: str) -> str:
    """Deterministic, DNS-safe Application name: `llmd-<sanitised name>`."""
    safe = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    return f"llmd-{safe}"


def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge ``override`` into a copy of ``base`` (override wins)."""
    out = dict(base)
    for key, val in (override or {}).items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def selector_to_match_labels(selector: str) -> dict:
    """Parse an equality label selector (``"k=v,k2=v2"``) into a matchLabels map.

    The llm-d-router chart's ``router.modelServers.matchLabels`` is equality-only,
    so non-equality terms (``!=``, set-based) are dropped. Empty/blank → ``{}``.
    """
    labels: dict[str, str] = {}
    for term in (selector or "").split(","):
        term = term.strip()
        if "=" in term and "!=" not in term:
            key, val = term.split("=", 1)
            key = key.strip()
            if key:
                labels[key] = val.strip()
    return labels


def llmd_service_name(stack: CustomLlmdStack) -> str:
    """The router's entry Service in sidecar mode: ``<release>-epp``.

    ArgoCD's Helm release name is the Application name (``argo_app_name``), which
    build_argo_application pins explicitly, so this name is deterministic.
    """
    return f"{stack.argo_app_name}-epp"


def build_llmd_ingress(
    stack: CustomLlmdStack,
    *,
    ingress_class: str,
    ingress_domain: str,
    ingress_path: str,
) -> dict:
    """An Ingress fronting the llm-d router's Envoy entry Service.

    Backend: Service ``{argo_app_name}-epp`` port name ``http`` (chart sidecar
    entry, 8081). Host is always ``{argo_app_name}.{ingress_domain}`` so multiple
    stacks never collide. ``ingress_class`` empty omits ``ingressClassName`` (the
    cluster's default IngressClass is used). Managed by the portal, not ArgoCD.
    """
    spec: dict = {
        "rules": [
            {
                "host": f"{stack.argo_app_name}.{ingress_domain}",
                "http": {
                    "paths": [
                        {
                            "path": ingress_path,
                            "pathType": "Prefix",
                            "backend": {
                                "service": {
                                    "name": llmd_service_name(stack),
                                    "port": {"name": "http"},
                                }
                            },
                        }
                    ]
                },
            }
        ],
    }
    if ingress_class:
        spec["ingressClassName"] = ingress_class
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "Ingress",
        "metadata": {
            "name": f"{stack.argo_app_name}-ingress",
            "namespace": stack.namespace,
            "labels": {"app.kubernetes.io/managed-by": MANAGED_BY},
        },
        "spec": spec,
    }


def default_llmd_values(
    target_model_name: str,
    *,
    epp_registry: str,
    epp_repository: str,
    epp_tag: str,
    endpoint_selector: str | None = None,
) -> dict:
    """The starter ``values.yaml`` for a new stack: the llm-d **standalone router**
    (``llm-d-router-standalone`` chart, ``router.*`` values schema).

    The chart co-locates an Envoy sidecar with the EPP and ships cache-aware
    scorers in its defaults. We only pin the EPP image (llm-d's router
    endpoint-picker) and point the router at already-running model servers via
    ``router.modelServers.matchLabels`` with the InferencePool disabled; the
    sidecar/scorers come from chart defaults. The user edits this freely.

    (Superseded the older GIE ``standalone`` ``inferenceExtension.*`` schema when
    the chart lineage moved to ``llm-d-router-standalone``, which hard-fails on
    the deprecated top-level ``inferenceExtension`` key.)
    """
    selector = endpoint_selector or (f"{LABEL_MODEL}={target_model_name}" if target_model_name else "")
    return {
        "router": {
            "epp": {
                "replicas": 1,
                "image": {"registry": epp_registry, "repository": epp_repository, "tag": epp_tag},
            },
            "modelServers": {
                "type": "vllm",
                "targetPorts": [{"number": 8000}],
                "matchLabels": selector_to_match_labels(selector),
            },
            "inferencePool": {"create": False},
        },
    }


def build_llmd_values(
    stack: CustomLlmdStack, *, epp_registry: str, epp_repository: str, epp_tag: str
) -> dict:
    """The values actually sent to ArgoCD: the user's ``helm_values`` with a thin
    base merged underneath, so the llm-d EPP image defaults apply even if the
    user's values.yaml omits them. The user's values win over the base — but an
    explicit per-stack EPP override (the stack's ``epp_*`` columns) wins over
    both: create seeds ``helm_values`` with the image already baked in, so
    without this the override fields could never take effect.
    """
    image = {"registry": epp_registry, "repository": epp_repository, "tag": epp_tag}
    out = deep_merge({"router": {"epp": {"image": image}}}, stack.helm_values or {})
    if stack.epp_registry or stack.epp_repository or stack.epp_tag:
        out = deep_merge(out, {"router": {"epp": {"image": image}}})
    return out


def build_argo_application(
    stack: CustomLlmdStack,
    *,
    chart_repo: str,
    chart_name: str,
    chart_version: str,
    values: dict,
    project: str,
    argocd_namespace: str,
    destination_server: str,
) -> dict:
    """An argoproj.io/v1alpha1 Application that deploys the llm-d stack.

    Isolation: ``spec.project`` is a dedicated AppProject (not ``default``) and
    ``destination.namespace`` is the stack's own namespace, so this Application
    can only ever manage its own resources in its own namespace.
    """
    return {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Application",
        "metadata": {
            "name": stack.argo_app_name,
            "namespace": argocd_namespace,
            "labels": {"app.kubernetes.io/managed-by": MANAGED_BY},
        },
        "spec": {
            "project": project,
            "source": {
                # ArgoCD 3.x reads an oci:// repoURL as a native OCI artifact and
                # drops the Helm `chart` field (→ chart not found). Helm-over-OCI
                # wants the bare registry path with `chart` set separately.
                "repoURL": chart_repo.removeprefix("oci://"),
                "chart": chart_name,
                "targetRevision": chart_version,
                "helm": {"releaseName": stack.argo_app_name, "valuesObject": values},
            },
            "destination": {"server": destination_server, "namespace": stack.namespace},
            "syncPolicy": {
                "automated": {"prune": True, "selfHeal": True},
                "syncOptions": ["CreateNamespace=true"],
            },
        },
    }
