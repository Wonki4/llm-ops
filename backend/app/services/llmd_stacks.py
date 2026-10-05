"""llm-d router stack lifecycle, shared by the admin API and P/D deployments.

A stack is an argoproj.io Application the portal renders and applies to the
ArgoCD control-plane namespace on the resolved host cluster, plus an Ingress
(and an optional direct Service/Ingress pair) the portal manages itself on the
target cluster. ``create_stack`` / ``delete_stack`` flush but never commit:
the caller owns the transaction so a P/D deployment and its auto-created
router land (or roll back) together.

Validation errors are raised as ``HTTPException`` (400/409/502) so both the
llm-d admin API and the deployments API surface the same messages.
"""

from __future__ import annotations

import logging
import uuid

import yaml
from fastapi import HTTPException
from kubernetes_asyncio.client.exceptions import ApiException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.k8s import K8sNotConfigured
from app.config import settings
from app.db.models.custom_llmd_stack import CustomLlmdStack
from app.services.clusters import argocd_placement_for, k8s_for_cluster
from app.services.llmd_manifests import (
    argo_app_name_for,
    build_argo_application,
    build_direct_ingress,
    build_direct_service,
    build_llmd_ingress,
    build_llmd_values,
    direct_service_name,
    modelservers_target,
)

logger = logging.getLogger(__name__)


# ─── values YAML ──────────────────────────────────────────────────────────────


def parse_values_yaml(text: str) -> dict:
    if not text or not text.strip():
        return {}
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid values YAML: {e}")
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="values.yaml must be a mapping (key: value)")
    return parsed


class _BlockDumper(yaml.SafeDumper):
    """SafeDumper that emits multi-line strings as ``|`` literal blocks."""


def _str_block_representer(dumper: yaml.SafeDumper, data: str):
    # Plain safe_dump renders a multi-line string as a double-quoted scalar with
    # escaped \n (and line-continuation backslashes) — an embedded config like
    # the Envoy proxy YAML or the P/D EPP plugin config comes back mangled in
    # the editor. Emit it as a `|` literal block so the readback matches.
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_BlockDumper.add_representer(str, _str_block_representer)


def dump_values_yaml(data: dict) -> str:
    """Dump a values dict to YAML, preserving ``|`` literal blocks for
    multi-line string values (readable, lossless round-trip)."""
    return yaml.dump(data, Dumper=_BlockDumper, sort_keys=False, default_flow_style=False)


# ─── status / errors ──────────────────────────────────────────────────────────


def argo_status(obj: dict | None) -> dict:
    """Extract sync/health from an Application CR (Unknown when absent)."""
    if not obj:
        return {"sync_status": "Unknown", "health_status": "Unknown", "status_message": None}
    st = obj.get("status", {}) or {}
    return {
        "sync_status": (st.get("sync") or {}).get("status", "Unknown"),
        "health_status": (st.get("health") or {}).get("status", "Unknown"),
        "status_message": (st.get("health") or {}).get("message"),
    }


async def live_status(db: AsyncSession, stack: CustomLlmdStack) -> dict:
    try:
        k8s, argocd_ns, _dest = await argocd_placement_for(db, stack.cluster_id)
        obj = await k8s.get_application(argocd_ns, stack.argo_app_name)
        return argo_status(obj)
    except Exception as e:  # noqa: BLE001 — status is best-effort
        logger.info("llm-d status read failed for %s: %s", stack.name, e)
        return argo_status(None)


def k8s_error_message(e: Exception) -> str:
    """Human-readable reason a K8s Application op failed, for the UI."""
    if isinstance(e, K8sNotConfigured):
        return "No kubeconfig is configured for this cluster — K8s access is disabled."
    if isinstance(e, ApiException):
        if e.status == 403:
            return "The portal lacks RBAC to manage applications.argoproj.io in the ArgoCD namespace."
        if e.status == 404:
            return "ArgoCD Application CRD or namespace not found — is ArgoCD installed on this cluster?"
        body = (getattr(e, "body", None) or "").strip()
        return body[:600] or f"Kubernetes API returned HTTP {e.status}."
    return str(e) or "Kubernetes request failed."


# ─── effective settings per stack ─────────────────────────────────────────────


def chart_source(stack: CustomLlmdStack) -> tuple[str, str, str]:
    return (
        stack.chart_repo or settings.llmd_chart_repo,
        stack.chart_name or settings.llmd_chart_name,
        stack.chart_version or settings.llmd_chart_version,
    )


def epp_image(stack: CustomLlmdStack) -> tuple[str, str, str]:
    return (
        stack.epp_registry or settings.llmd_epp_image_registry,
        stack.epp_repository or settings.llmd_epp_image_repository,
        stack.epp_tag or settings.llmd_epp_image_tag,
    )


def values_for(stack: CustomLlmdStack) -> dict:
    registry, repository, tag = epp_image(stack)
    return build_llmd_values(stack, epp_registry=registry, epp_repository=repository, epp_tag=tag)


def ingress_host(stack: CustomLlmdStack) -> str:
    """Effective ingress host: per-stack override, else {app}.{global domain}."""
    return stack.ingress_host or f"{stack.argo_app_name}.{settings.effective_ingress_domain}"


def ingress_class(stack: CustomLlmdStack) -> str:
    """Effective ingress class: per-stack override, else the global default
    (which, when empty, omits ingressClassName -> cluster default)."""
    return stack.ingress_class if stack.ingress_class is not None else settings.llmd_ingress_class


def ingress_for(stack: CustomLlmdStack) -> dict:
    return build_llmd_ingress(
        stack,
        host=ingress_host(stack),
        ingress_class=ingress_class(stack),
        ingress_path=settings.llmd_ingress_path or "/",
    )


def direct_host(stack: CustomLlmdStack) -> str:
    """Effective direct-route ingress host: per-stack override, else
    {argo_app_name}-direct.{global domain}."""
    return stack.direct_ingress_host or f"{stack.argo_app_name}-direct.{settings.effective_ingress_domain}"


def direct_selector(stack: CustomLlmdStack) -> tuple[dict, int]:
    """(matchLabels, targetPort) the direct Service should use, read from the
    stack's rendered values (same modelServers block the router targets)."""
    return modelservers_target(stack.values_snapshot)


def direct_cleanup_names(stack: CustomLlmdStack) -> dict:
    return {
        "service": direct_service_name(stack),
        "ingress": f"{stack.argo_app_name}-direct-ingress",
    }


def direct_manifests(stack: CustomLlmdStack) -> list[dict]:
    """[Service, Ingress] for the direct route when enabled, else []."""
    if not stack.direct_route_enabled:
        return []
    match_labels, target_port = direct_selector(stack)
    return [
        build_direct_service(stack, match_labels=match_labels, target_port=target_port),
        build_direct_ingress(
            stack,
            host=direct_host(stack),
            ingress_class=ingress_class(stack),
            ingress_path=settings.llmd_ingress_path or "/",
        ),
    ]


def require_direct_selector(stack: CustomLlmdStack) -> None:
    """400 when the direct route is enabled but modelServers.matchLabels is
    empty — a selector-less Service would bind no endpoints. Must be called
    before any K8s write and outside the 502-mapping try block."""
    if stack.direct_route_enabled and not direct_selector(stack)[0]:
        raise HTTPException(
            status_code=400,
            detail="modelServers.matchLabels is required to enable the direct route.",
        )


def application_for(stack: CustomLlmdStack, argocd_namespace: str, destination_server: str) -> dict:
    chart_repo, chart_name, chart_version = chart_source(stack)
    return build_argo_application(
        stack,
        chart_repo=chart_repo,
        chart_name=chart_name,
        chart_version=chart_version,
        values=stack.values_snapshot,
        project=settings.argo_project,
        argocd_namespace=argocd_namespace,
        destination_server=destination_server,
    )


def require_valid_name(name: str) -> str:
    if not name or not name.strip():
        raise HTTPException(status_code=400, detail="Stack name is required.")
    app_name = argo_app_name_for(name)
    if app_name in ("llmd-", "llmd"):
        raise HTTPException(
            status_code=400,
            detail="Stack name must contain letters or digits (a–z, 0–9, hyphen).",
        )
    if len(app_name) > 53:
        raise HTTPException(
            status_code=400,
            detail=f"Stack name is too long — the resulting app name '{app_name}' exceeds 53 characters.",
        )
    return app_name


# ─── lifecycle ────────────────────────────────────────────────────────────────


def _opt(value: str | None) -> str | None:
    return (value or "").strip() or None


async def apply_stack(db: AsyncSession, stack: CustomLlmdStack, *, verb: str = "apply") -> None:
    """Apply the Application CR, the EPP Ingress and the direct pair (502 on failure)."""
    try:
        k8s, argocd_ns, dest_server = await argocd_placement_for(db, stack.cluster_id)
        await k8s.apply_application(argocd_ns, application_for(stack, argocd_ns, dest_server))
        target_k8s = await k8s_for_cluster(db, stack.cluster_id)
        await target_k8s.create_or_patch(stack.namespace, [ingress_for(stack)])
        direct = direct_manifests(stack)
        if direct:
            await target_k8s.create_or_patch(stack.namespace, direct)
    except Exception as e:
        logger.exception("ArgoCD Application %s failed for stack %s", verb, stack.name)
        raise HTTPException(status_code=502, detail=f"ArgoCD {verb} failed: {k8s_error_message(e)}")


async def create_stack(
    db: AsyncSession,
    *,
    name: str,
    target_model_name: str,
    cluster_id: uuid.UUID | str | None,
    namespace: str,
    values: dict,
    user_id: str | None,
    chart_repo: str | None = None,
    chart_name: str | None = None,
    chart_version: str | None = None,
    epp_registry: str | None = None,
    epp_repository: str | None = None,
    epp_tag: str | None = None,
    ingress_host: str | None = None,
    ingress_class: str | None = None,
    direct_route_enabled: bool = False,
    direct_ingress_host: str | None = None,
) -> CustomLlmdStack:
    """Validate, persist (flush only) and apply a new router stack.

    ``values`` is the full Helm values dict (the caller picks the user's YAML
    or ``default_llmd_values``). Raises 400 (name/namespace/selector), 409
    (duplicate name) or 502 (K8s/ArgoCD write) as ``HTTPException``.
    """
    app_name = require_valid_name(name)
    if not namespace or not namespace.strip():
        raise HTTPException(status_code=400, detail="Namespace is required.")
    if (await db.execute(select(CustomLlmdStack).where(CustomLlmdStack.name == name))).scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Stack '{name}' already exists")

    stack = CustomLlmdStack(
        id=uuid.uuid4(),
        name=name,
        target_model_name=target_model_name,
        cluster_id=uuid.UUID(str(cluster_id)) if cluster_id else None,
        namespace=namespace,
        argo_app_name=app_name,
        helm_values=values,
        values_snapshot={},
        chart_repo=_opt(chart_repo),
        chart_name=_opt(chart_name),
        chart_version=_opt(chart_version),
        epp_registry=_opt(epp_registry),
        epp_repository=_opt(epp_repository),
        epp_tag=_opt(epp_tag),
        ingress_host=_opt(ingress_host),
        ingress_class=_opt(ingress_class),
        direct_route_enabled=direct_route_enabled,
        direct_ingress_host=_opt(direct_ingress_host),
        created_by=user_id,
        updated_by=user_id,
    )
    stack.values_snapshot = values_for(stack)
    require_direct_selector(stack)  # 400 before any K8s write (outside the try)
    db.add(stack)
    await db.flush()
    try:
        await apply_stack(db, stack, verb="apply")
    except HTTPException:
        # Callers that swallow the 502 (P/D deploy) must not keep a row for a
        # stack that never reached ArgoCD.
        await db.delete(stack)
        raise
    return stack


async def delete_stack(db: AsyncSession, stack: CustomLlmdStack) -> None:
    """Delete the Application CR (502 on failure), clean up ingresses best-effort, delete the row."""
    try:
        k8s, argocd_ns, _dest = await argocd_placement_for(db, stack.cluster_id)
        await k8s.delete_application(argocd_ns, stack.argo_app_name)
    except Exception as e:
        logger.exception("ArgoCD Application delete failed for stack %s", stack.name)
        raise HTTPException(status_code=502, detail=f"ArgoCD delete failed: {k8s_error_message(e)}")
    try:
        target_k8s = await k8s_for_cluster(db, stack.cluster_id)
        await target_k8s.delete(stack.namespace, {"ingress": f"{stack.argo_app_name}-ingress"})
        if stack.direct_route_enabled:
            await target_k8s.delete(stack.namespace, direct_cleanup_names(stack))
    except Exception as e:  # noqa: BLE001 — ingress cleanup is best-effort
        logger.info("llm-d ingress cleanup failed for %s: %s", stack.name, e)
    await db.delete(stack)
