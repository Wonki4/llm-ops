"""Discovery of externally-deployed vLLM/SGLang servings.

Live-scans clusters (no persistence): a Deployment counts as an external
serving when any container image name contains "vllm" or "sglang" and it does
NOT carry the portal's managed-by label. Pure functions + a parallel scan
helper; the API layer owns DB access and LiteLLM registration.
"""

import asyncio
import logging
from typing import Any

from app.clients.k8s import K8sNotConfigured
from app.services.deployment_status import classify
from app.services.model_deployment_manifests import LABEL_OWNER

logger = logging.getLogger(__name__)

PORTAL_MANAGED_VALUE = "litellm-portal"


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
    """Shape one list_deployments_all item into an external serving, or None."""
    if dep.get("labels", {}).get(LABEL_OWNER) == PORTAL_MANAGED_VALUE:
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
    targets: list[tuple[str | None, str, Any]], timeout: float = 5.0
) -> tuple[list[dict], list[dict]]:
    """Scan (cluster_id, cluster_name, k8s_client) targets in parallel.

    Returns (servings, errors). A missing default kubeconfig is silently
    skipped; timeouts and connection errors become per-cluster error entries
    so one broken cluster never blanks the page.
    """

    async def _scan(k8s: Any) -> list[dict]:
        raw = await asyncio.wait_for(k8s.list_deployments_all(), timeout=timeout)
        return [s for s in (to_external_serving(d) for d in raw) if s is not None]

    results = await asyncio.gather(*(_scan(k8s) for _, _, k8s in targets), return_exceptions=True)

    servings: list[dict] = []
    errors: list[dict] = []
    for (cluster_id, cluster_name, _), result in zip(targets, results):
        if isinstance(result, K8sNotConfigured):
            continue
        if isinstance(result, TimeoutError | asyncio.TimeoutError):
            errors.append({"cluster": cluster_name, "message": f"scan timed out after {timeout:g}s"})
            continue
        if isinstance(result, BaseException):
            logger.warning("External serving scan failed for %s: %s", cluster_name, result)
            errors.append({"cluster": cluster_name, "message": str(result) or type(result).__name__})
            continue
        for s in result:
            s["cluster_id"] = cluster_id
            s["cluster_name"] = cluster_name
            servings.append(s)
    return servings, errors
