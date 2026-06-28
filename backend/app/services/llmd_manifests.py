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


def default_llmd_values(target_model_name: str, *, image_registry: str) -> dict:
    """The starter ``values.yaml`` shown to the user for a new stack.

    A minimal, correct set of values for the gateway-api-inference-extension
    ``standalone`` chart, which deploys the EPP / inference scheduler (the
    prefix-cache-aware router) in front of **already-running** model servers — it
    does not provision vLLM itself. The router selects model-server pods by
    ``endpointSelector`` (defaults to the portal's ``llm-ops/model-name=<target>``
    label) on ``targetPorts``. The user edits this freely.
    """
    return {
        "inferenceExtension": {
            "replicas": 1,
            "image": {"registry": image_registry},
            "endpointsServer": {
                "createInferencePool": False,
                "endpointSelector": f"{LABEL_MODEL}={target_model_name}" if target_model_name else "",
                "targetPorts": 8000,
                "modelServerType": "vllm",
            },
        },
    }


def build_llmd_values(stack: CustomLlmdStack, *, image_registry: str) -> dict:
    """The values actually sent to ArgoCD: the user's ``helm_values`` with a thin
    base merged underneath, so the (air-gapped) image registry default applies
    even if the user's values.yaml omits it. The user's values always win.
    """
    base = {"inferenceExtension": {"image": {"registry": image_registry}}}
    return deep_merge(base, stack.helm_values or {})


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
