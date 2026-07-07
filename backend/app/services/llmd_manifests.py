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


def default_llmd_values(
    target_model_name: str,
    *,
    epp_registry: str,
    epp_repository: str,
    epp_tag: str,
    endpoint_selector: str | None = None,
) -> dict:
    """The starter ``values.yaml`` for a new stack: the llm-d **standalone router**.

    The GIE ``standalone`` chart already co-locates an Envoy sidecar with the EPP
    and ships cache-aware scorers (queue / kv-cache / prefix-cache) in its default
    EndpointPickerConfig. To get the *llm-d* router we only swap the EPP image to
    llm-d's (GIE EPP extended with llm-d's routing intelligence); the sidecar and
    scorers come from chart defaults. The router fronts already-running model
    servers selected by ``endpointSelector`` on ``targetPorts`` (no InferencePool,
    no Gateway API provider). The user edits this freely.
    """
    selector = endpoint_selector or (f"{LABEL_MODEL}={target_model_name}" if target_model_name else "")
    return {
        "inferenceExtension": {
            "replicas": 1,
            "image": {"registry": epp_registry, "repository": epp_repository, "tag": epp_tag},
            "endpointsServer": {
                "createInferencePool": False,
                "endpointSelector": selector,
                "targetPorts": 8000,
                "modelServerType": "vllm",
            },
        },
    }


def build_llmd_values(
    stack: CustomLlmdStack, *, epp_registry: str, epp_repository: str, epp_tag: str
) -> dict:
    """The values actually sent to ArgoCD: the user's ``helm_values`` with a thin
    base merged underneath, so the llm-d EPP image defaults apply even if the
    user's values.yaml omits them. The user's values always win.
    """
    base = {
        "inferenceExtension": {
            "image": {"registry": epp_registry, "repository": epp_repository, "tag": epp_tag}
        }
    }
    return deep_merge(base, stack.helm_values or {})


def build_argo_application(
    stack: CustomLlmdStack,
    *,
    chart_repo: str,
    chart_name: str,
    chart_version: str,
    values: dict,
    project: str,
    argocd_namespace: str,
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
                "repoURL": chart_repo,
                "chart": chart_name,
                "targetRevision": chart_version,
                "helm": {"valuesObject": values},
            },
            "destination": {"server": "https://kubernetes.default.svc", "namespace": stack.namespace},
            "syncPolicy": {
                "automated": {"prune": True, "selfHeal": True},
                "syncOptions": ["CreateNamespace=true"],
            },
        },
    }
