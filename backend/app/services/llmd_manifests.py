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

if TYPE_CHECKING:
    from app.db.models.custom_llmd_stack import CustomLlmdStack

MANAGED_BY = "litellm-portal"


def argo_app_name_for(name: str) -> str:
    """Deterministic, DNS-safe Application name: `llmd-<sanitised name>`."""
    safe = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    return f"llmd-{safe}"


def build_llmd_values(stack: CustomLlmdStack, *, image_registry: str, hf_secret_name: str) -> dict:
    """Per-model Helm values for the internal llm-d chart."""
    return {
        "model": {"id": stack.model_ref, "servedName": stack.served_model_name},
        "replicas": stack.replicas,
        "resources": {"gpu": {"count": stack.gpu_count, "resourceKey": stack.gpu_resource_key}},
        "image": {"registry": image_registry},
        "hfTokenSecret": hf_secret_name,
        "namespace": stack.namespace,
    }


def build_argo_application(
    stack: CustomLlmdStack,
    *,
    chart_repo: str,
    chart_name: str,
    chart_version: str,
    values: dict,
    project: str,
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
