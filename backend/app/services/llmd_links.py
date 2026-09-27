"""Which model servers does an llm-d stack actually route to?

A stack is linked to a server by *label selector*, never by name: the
router's ``modelServers.matchLabels`` must be a subset of the server's pod
labels (K8s equality-selector semantics). Portal deployments contribute the
labels the manifest builder stamps on their pods; external servings
contribute the labels the cluster scan read off their Deployment.
"""

from __future__ import annotations

from typing import Any

from app.services.llmd_manifests import stack_selector
from app.services.model_deployment_manifests import pod_labels


def selector_matches(selector: dict, labels: dict) -> bool:
    """True when every selector label is present with the same value."""
    if not selector:
        return False
    return all(labels.get(k) == v for k, v in selector.items())


def portal_server(dep: Any) -> dict:
    return {
        "kind": "portal",
        "id": str(dep.id),
        "model_name": dep.model_name,
        "name": dep.model_name,
        "namespace": dep.namespace,
        "status": dep.status,
        "labels": pod_labels(dep),
    }


def external_server(serving: dict, registered_model_name: str | None) -> dict:
    return {
        "kind": "external",
        "id": None,
        "model_name": registered_model_name,
        "name": serving.get("deployment_name"),
        "namespace": serving.get("namespace"),
        "status": serving.get("status"),
        "labels": serving.get("labels") or {},
    }


def link_stacks(stacks: list, servers: list[dict]) -> dict[str, dict]:
    """stack id → {"selector": {...}, "servers": [server, ...]}."""
    out: dict[str, dict] = {}
    for stack in stacks:
        selector = stack_selector(getattr(stack, "values_snapshot", None) or getattr(stack, "helm_values", None) or {})
        out[str(stack.id)] = {
            "selector": selector,
            "servers": [srv for srv in servers if selector_matches(selector, srv["labels"])],
        }
    return out


def stacks_for_server(server: dict, stacks: list) -> list:
    """Stacks whose selector picks this server."""
    return [
        s
        for s in stacks
        if selector_matches(stack_selector(getattr(s, "values_snapshot", None) or {}), server["labels"])
    ]
