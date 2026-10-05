"""Admin endpoints for llm-d serving stacks (ArgoCD CRD-managed).

The portal renders an argoproj.io Application per stack and applies it via the
K8s API to the ArgoCD control-plane namespace on the resolved host cluster,
with a ``spec.destination`` pointing at the stack's target cluster (per-cluster
placement; a null cluster stays fully local). ArgoCD's controller reconciles
it; sync/health is read live from the Application CR, never persisted.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_super_user
from app.clients.k8s import K8sClient
from app.config import settings
from app.db.models.custom_k8s_cluster import CustomK8sCluster
from app.db.models.custom_llmd_stack import CustomLlmdStack
from app.db.models.custom_model_deployment import CustomModelDeployment
from app.db.models.custom_user import CustomUser
from app.db.session import get_db
from app.services import llmd_stacks
from app.services.clusters import argocd_placement_for, k8s_for_cluster
from app.services.external_servings import scan_clusters
from app.services.llmd_links import external_server, link_stacks, portal_server
from app.services.llmd_manifests import default_llmd_values, direct_service_name

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/llmd-stacks", tags=["llmd-stacks"])


class CreateLlmdStackRequest(BaseModel):
    name: str
    target_model_name: str  # an existing model deployment the router targets
    cluster_id: str | None = None  # registered cluster; None = portal default kubeconfig
    namespace: str = "default"
    values_yaml: str = ""  # full Helm values.yaml the user authored
    chart_repo: str | None = None
    chart_name: str | None = None
    chart_version: str | None = None
    epp_registry: str | None = None
    epp_repository: str | None = None
    epp_tag: str | None = None
    ingress_host: str | None = None  # full host override; empty -> {app}.{domain}
    ingress_class: str | None = None  # empty -> global llmd_ingress_class
    direct_route_enabled: bool = False
    direct_ingress_host: str | None = None


class UpdateLlmdStackRequest(BaseModel):
    namespace: str | None = None
    values_yaml: str | None = None
    chart_repo: str | None = None
    chart_name: str | None = None
    chart_version: str | None = None
    epp_registry: str | None = None
    epp_repository: str | None = None
    epp_tag: str | None = None
    ingress_host: str | None = None
    ingress_class: str | None = None
    direct_route_enabled: bool | None = None
    direct_ingress_host: str | None = None


class DefaultValuesRequest(BaseModel):
    target_model_name: str = ""
    endpoint_selector: str | None = None


def _serialize(stack: CustomLlmdStack, status_fields: dict) -> dict:
    return {
        "id": str(stack.id),
        "name": stack.name,
        "target_model_name": stack.target_model_name,
        "cluster_id": str(stack.cluster_id) if stack.cluster_id else None,
        "namespace": stack.namespace,
        "argo_app_name": stack.argo_app_name,
        "chart_repo": llmd_stacks.chart_source(stack)[0],
        "chart_name": llmd_stacks.chart_source(stack)[1],
        "chart_version": llmd_stacks.chart_source(stack)[2],
        "epp_image": "{}/{}:{}".format(*llmd_stacks.epp_image(stack)),
        "ingress_host": llmd_stacks.ingress_host(stack),
        "ingress_class": llmd_stacks.ingress_class(stack),
        "chart_overrides": {
            "chart_repo": stack.chart_repo,
            "chart_name": stack.chart_name,
            "chart_version": stack.chart_version,
            "epp_registry": stack.epp_registry,
            "epp_repository": stack.epp_repository,
            "epp_tag": stack.epp_tag,
        },
        "ingress_overrides": {
            "ingress_host": stack.ingress_host,
            "ingress_class": stack.ingress_class,
        },
        "direct_route_enabled": stack.direct_route_enabled,
        "direct_ingress_host": llmd_stacks.direct_host(stack),
        "direct_service": direct_service_name(stack),
        "direct_overrides": {"ingress_host": stack.direct_ingress_host},
        "helm_values": stack.helm_values,
        "values_yaml": (llmd_stacks.dump_values_yaml(stack.helm_values) if stack.helm_values else ""),
        "created_by": stack.created_by,
        "created_at": stack.created_at.isoformat() if stack.created_at else None,
        "updated_at": stack.updated_at.isoformat() if stack.updated_at else None,
        **status_fields,
    }


@router.get("")
async def list_stacks(
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    rows = (await db.execute(select(CustomLlmdStack).order_by(CustomLlmdStack.created_at.desc()))).scalars().all()
    return {"stacks": [_serialize(s, await llmd_stacks.live_status(db, s)) for s in rows]}


@router.get("/{stack_id}/applied")
async def applied_values(
    stack_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Effective rendered values + live ArgoCD state (applied valuesObject and
    deployed resources from the Application CR status). Live fields are
    best-effort: null/empty when the cluster is unreachable or unsynced."""
    stack = (
        await db.execute(select(CustomLlmdStack).where(CustomLlmdStack.id == uuid.UUID(stack_id)))
    ).scalar_one_or_none()
    if stack is None:
        raise HTTPException(status_code=404, detail="Stack not found")

    live_values: dict | None = None
    resources: list[dict] = []
    revision: str | None = None
    live_error: str | None = None
    try:
        k8s, argocd_ns, _dest = await argocd_placement_for(db, stack.cluster_id)
        obj = await k8s.get_application(argocd_ns, stack.argo_app_name)
        if obj:
            src = (obj.get("spec") or {}).get("source") or {}
            live_values = (src.get("helm") or {}).get("valuesObject")
            st = obj.get("status") or {}
            revision = (st.get("sync") or {}).get("revision")
            resources = [
                {
                    "group": r.get("group") or "",
                    "version": r.get("version") or "v1",
                    "kind": r.get("kind"),
                    "name": r.get("name"),
                    "namespace": r.get("namespace"),
                    "status": r.get("status"),
                    "health": (r.get("health") or {}).get("status"),
                }
                for r in (st.get("resources") or [])
            ]
        else:
            live_error = "The ArgoCD Application was not found — it may have been deleted."
    except Exception as e:  # noqa: BLE001 — live state is best-effort
        logger.info("llm-d applied read failed for %s: %s", stack.name, e)
        live_error = llmd_stacks.k8s_error_message(e)

    # Which model servers this stack's selector actually picks (portal + scanned).
    deployments = list((await db.execute(select(CustomModelDeployment))).scalars().all())
    servers = [portal_server(d) for d in deployments]
    try:
        targets: list = [(None, "default", K8sClient())]
        for row in (await db.execute(select(CustomK8sCluster))).scalars().all():
            targets.append((str(row.id), row.name, await k8s_for_cluster(db, row.id)))
        servings, _errs = await scan_clusters(targets)
        servers += [external_server(sv, None) for sv in servings]
    except Exception as e:  # noqa: BLE001 — linkage is best-effort without a cluster
        logger.info("llm-d linked-servers scan skipped for %s: %s", stack.name, e)
    link = link_stacks([stack], servers)[str(stack.id)]

    return {
        "effective_values": stack.values_snapshot,
        "selector": link["selector"],
        "linked_servers": [{k: v for k, v in srv.items() if k != "labels"} for srv in link["servers"]],
        "live_values": live_values,
        "resources": resources,
        "revision": revision,
        "live_error": live_error,
    }


@router.get("/chart-defaults")
async def chart_defaults(user: CustomUser = Depends(require_super_user)) -> dict:
    """The global chart-source + EPP-image defaults, for prefilling the form."""
    return {
        "chart_repo": settings.llmd_chart_repo,
        "chart_name": settings.llmd_chart_name,
        "chart_version": settings.llmd_chart_version,
        "epp_registry": settings.llmd_epp_image_registry,
        "epp_repository": settings.llmd_epp_image_repository,
        "epp_tag": settings.llmd_epp_image_tag,
        "ingress_class": settings.llmd_ingress_class,
        "ingress_domain": settings.effective_ingress_domain,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_stack(
    body: CreateLlmdStackRequest,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    values = llmd_stacks.parse_values_yaml(body.values_yaml) or default_llmd_values(
        body.target_model_name,
        epp_registry=settings.llmd_epp_image_registry,
        epp_repository=settings.llmd_epp_image_repository,
        epp_tag=settings.llmd_epp_image_tag,
    )
    stack = await llmd_stacks.create_stack(
        db,
        name=body.name,
        target_model_name=body.target_model_name,
        cluster_id=body.cluster_id,
        namespace=body.namespace,
        values=values,
        user_id=user.user_id,
        chart_repo=body.chart_repo,
        chart_name=body.chart_name,
        chart_version=body.chart_version,
        epp_registry=body.epp_registry,
        epp_repository=body.epp_repository,
        epp_tag=body.epp_tag,
        ingress_host=body.ingress_host,
        ingress_class=body.ingress_class,
        direct_route_enabled=body.direct_route_enabled,
        direct_ingress_host=body.direct_ingress_host,
    )
    await db.commit()
    await db.refresh(stack)
    return _serialize(stack, await llmd_stacks.live_status(db, stack))


@router.post("/default-values")
async def default_values(
    body: DefaultValuesRequest,
    user: CustomUser = Depends(require_super_user),
) -> dict:
    values = default_llmd_values(
        body.target_model_name,
        epp_registry=settings.llmd_epp_image_registry,
        epp_repository=settings.llmd_epp_image_repository,
        epp_tag=settings.llmd_epp_image_tag,
        endpoint_selector=body.endpoint_selector,
    )
    return {
        "values": values,
        "values_yaml": llmd_stacks.dump_values_yaml(values),
    }


@router.put("/{stack_id}")
async def update_stack(
    stack_id: str,
    body: UpdateLlmdStackRequest,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    stack = (
        await db.execute(select(CustomLlmdStack).where(CustomLlmdStack.id == uuid.UUID(stack_id)))
    ).scalar_one_or_none()
    if not stack:
        raise HTTPException(status_code=404, detail="Stack not found")

    if body.namespace is not None:
        stack.namespace = body.namespace
    if body.values_yaml is not None:
        stack.helm_values = llmd_stacks.parse_values_yaml(body.values_yaml)
    for field in (
        "chart_repo",
        "chart_name",
        "chart_version",
        "epp_registry",
        "epp_repository",
        "epp_tag",
        "ingress_host",
        "ingress_class",
    ):
        val = getattr(body, field)
        if val is not None:
            setattr(stack, field, val.strip() or None)
    if body.direct_route_enabled is not None:
        stack.direct_route_enabled = body.direct_route_enabled
    if body.direct_ingress_host is not None:
        stack.direct_ingress_host = body.direct_ingress_host.strip() or None
    stack.values_snapshot = llmd_stacks.values_for(stack)
    llmd_stacks.require_direct_selector(stack)  # 400 before any K8s write (outside the try)
    stack.updated_by = user.user_id
    await db.flush()

    try:
        k8s, argocd_ns, dest_server = await argocd_placement_for(db, stack.cluster_id)
        await k8s.apply_application(argocd_ns, llmd_stacks.application_for(stack, argocd_ns, dest_server))
        target_k8s = await k8s_for_cluster(db, stack.cluster_id)
        await target_k8s.create_or_patch(stack.namespace, [llmd_stacks.ingress_for(stack)])
        direct = llmd_stacks.direct_manifests(stack)
        if direct:
            await target_k8s.create_or_patch(stack.namespace, direct)
    except Exception as e:
        logger.exception("ArgoCD Application update failed for stack %s", stack.name)
        raise HTTPException(status_code=502, detail=f"ArgoCD update failed: {llmd_stacks.k8s_error_message(e)}")
    if not stack.direct_route_enabled:
        # Toggled off (or never on): best-effort remove the pair so the cluster
        # matches the desired state. Cleanup failure must not fail the update.
        try:
            await target_k8s.delete(stack.namespace, llmd_stacks.direct_cleanup_names(stack))
        except Exception as e:  # noqa: BLE001 — cleanup is best-effort
            logger.info("llm-d direct cleanup failed for %s: %s", stack.name, e)
    await db.commit()
    await db.refresh(stack)
    return _serialize(stack, await llmd_stacks.live_status(db, stack))


@router.delete("/{stack_id}")
async def delete_stack(
    stack_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    stack = (
        await db.execute(select(CustomLlmdStack).where(CustomLlmdStack.id == uuid.UUID(stack_id)))
    ).scalar_one_or_none()
    if not stack:
        raise HTTPException(status_code=404, detail="Stack not found")
    await llmd_stacks.delete_stack(db, stack)
    await db.commit()
    return {"ok": True}
