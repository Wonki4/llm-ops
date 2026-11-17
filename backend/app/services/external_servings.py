"""Discovery of externally-deployed vLLM/SGLang servings.

Live-scans Kubernetes Deployments: a Deployment counts as an external
serving when any container image name contains "vllm" or "sglang" and it does
NOT carry the portal's managed-by label. Pure functions + a parallel scan
helper and the scoped discovery used by the two admin serving screens.
"""

import asyncio
import logging
from collections.abc import Iterable
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from kubernetes_asyncio.client.exceptions import ApiException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.k8s import K8sNotConfigured
from app.config import settings
from app.db.models.custom_external_serving import CustomExternalServing
from app.db.models.custom_llmd_stack import CustomLlmdStack
from app.db.models.custom_model_deployment import CustomModelDeployment
from app.services.clusters import NamespaceScope, namespace_scopes, scan_targets, validate_namespace
from app.services.deployment_status import classify
from app.services.model_deployment_manifests import LABEL_OWNER, PORTAL_OWNERS

logger = logging.getLogger(__name__)



def _detect_engine(containers: list[dict]) -> tuple[str, dict] | None:
    """Return (engine, container) for the first vLLM/SGLang container, else None."""
    for c in containers:
        image = (c.get("image") or "").lower()
        if "sglang" in image:
            return "sglang", c
        if "vllm" in image:
            return "vllm", c
    return None


def _extract_model_path(args: list[str]) -> str | None:
    for i, a in enumerate(args):
        if a == "--model" and i + 1 < len(args):
            return str(args[i + 1])
        if isinstance(a, str) and a.startswith("--model="):
            return a.split("=", 1)[1]
    return None


def to_external_serving(dep: dict) -> dict | None:
    """Shape one list_deployments item into an external serving, or None."""
    if dep.get("labels", {}).get(LABEL_OWNER) in PORTAL_OWNERS:
        return None
    detected = _detect_engine(dep.get("containers", []))
    if detected is None:
        return None
    engine, container = detected
    status, message = classify(dep, dep.get("replicas", 0))
    return {
        "namespace": dep["namespace"],
        "deployment_name": dep["name"],
        "engine": engine,
        "image": container.get("image"),
        "replicas": dep.get("replicas", 0),
        "ready_replicas": dep.get("ready", 0),
        "status": status,
        "status_message": message,
        "created_at": dep.get("created_at"),
        "model_path": _extract_model_path(container.get("args", [])),
        "labels": dep.get("labels", {}),
        "args": container.get("args", []),
    }


async def scan_clusters(
    targets: list[tuple[str | None, str, Any, str]], timeout: float = 5.0, *, skip_unconfigured_portal: bool = True
) -> tuple[list[dict], list[dict]]:
    """Scan (cluster_id, cluster_name, k8s_client, namespace) targets in parallel.

    Each target is one namespaced LIST (see ``clusters.scan_scopes``). Returns
    (servings, errors). A missing default kubeconfig is silently skipped;
    timeouts and connection errors become per-target error entries so one
    broken cluster never blanks the page.
    """

    async def _scan(k8s: Any, namespace: str) -> list[dict]:
        raw = await asyncio.wait_for(k8s.list_deployments(namespace), timeout=timeout)
        return [s for s in (to_external_serving(d) for d in raw) if s is not None]

    results = await asyncio.gather(*(_scan(k8s, ns) for _, _, k8s, ns in targets), return_exceptions=True)

    servings: list[dict] = []
    errors: list[dict] = []
    for (cluster_id, cluster_name, _, namespace), result in zip(targets, results):
        if isinstance(result, K8sNotConfigured) and cluster_id is None and skip_unconfigured_portal:
            continue
        if isinstance(result, TimeoutError | asyncio.TimeoutError):
            errors.append(
                {"cluster": cluster_name, "namespace": namespace, "message": f"scan timed out after {timeout:g}s"}
            )
            continue
        if isinstance(result, BaseException):
            if isinstance(result, ApiException):
                message = f"list deployments.apps: HTTP {result.status} {result.reason}"
            else:
                message = str(result) or type(result).__name__
            logger.warning("External serving scan failed for %s/%s: %s", cluster_name, namespace, message)
            errors.append({"cluster": cluster_name, "namespace": namespace, "message": message})
            continue
        for s in result:
            s["cluster_id"] = cluster_id
            s["cluster_name"] = cluster_name
            servings.append(s)
    return servings, errors


def serialize_registration(r: CustomExternalServing) -> dict:
    return {
        "id": str(r.id),
        "model_name": r.model_name,
        "api_base": r.api_base,
        "litellm_model_id": r.litellm_model_id,
    }


async def serving_namespace_scopes(
    db: AsyncSession, extra: Iterable[tuple[UUID | str | None, str | None]], cluster: str | None, ns: str,
) -> tuple[list[NamespaceScope], list[NamespaceScope]]:
    """Resolve the admin's selection even when the namespace has no portal inventory yet."""
    try:
        namespace = validate_namespace(ns)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if cluster == "default" and not namespace:
        extra = [*extra, (None, settings.k8s_default_namespace)]
    elif cluster and namespace:
        extra = [*extra, (None if cluster == "default" else cluster, namespace)]
    scopes = await namespace_scopes(db, extra, include_registered=cluster is not None)
    cluster_scopes = [s for s in scopes if not cluster or (s.cluster_id or "default") == cluster]
    if cluster and not cluster_scopes:
        raise HTTPException(status_code=404, detail="Cluster not found")
    return scopes, [s for s in cluster_scopes if not namespace or s.namespace == namespace]


async def discover_servings(
    db: AsyncSession, cluster: str | None, ns: str, *, extra_scopes: Iterable[tuple[UUID | None, str]] = (),
) -> dict:
    """List live external deployments, their matching registrations and available namespace filters."""
    regs = (await db.execute(select(CustomExternalServing))).scalars().all()
    extra = [(r.cluster_id, r.namespace) for r in regs] + list(extra_scopes)
    if cluster is not None:
        known = select(CustomModelDeployment.cluster_id, CustomModelDeployment.namespace).union(
            select(CustomLlmdStack.cluster_id, CustomLlmdStack.namespace)
        )
        extra.extend((await db.execute(known)).all())
        extra.append((None, settings.k8s_default_namespace))
    scopes, selected = await serving_namespace_scopes(db, extra, cluster, ns)
    errors = []
    targets = scan_targets(selected, errors=errors)
    servings, scan_errors = await scan_clusters(targets, skip_unconfigured_portal=cluster != "default")

    reg_map = {(str(r.cluster_id) if r.cluster_id else None, r.namespace, r.deployment_name): r for r in regs}
    for s in servings:
        r = reg_map.get((s["cluster_id"], s["namespace"], s["deployment_name"]))
        s["registration"] = serialize_registration(r) if r else None

    return {
        "servings": servings,
        "errors": errors + scan_errors,
        "scopes": [{"cluster_id": s.cluster_id, "namespace": s.namespace} for s in scopes
                   if not cluster or (s.cluster_id or "default") == cluster],
    }
