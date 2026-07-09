"""Admin endpoints for llm-d serving stacks (ArgoCD CRD-managed).

The portal renders an argoproj.io Application per stack and applies it via the
K8s API to the ArgoCD control-plane namespace on the resolved host cluster,
with a ``spec.destination`` pointing at the stack's target cluster (per-cluster
placement; a null cluster stays fully local). ArgoCD's controller reconciles
it; sync/health is read live from the Application CR, never persisted.
"""

import logging
import uuid

import yaml
from fastapi import APIRouter, Depends, HTTPException, status
from kubernetes_asyncio.client.exceptions import ApiException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_super_user
from app.clients.k8s import K8sNotConfigured
from app.config import settings
from app.db.models.custom_llmd_stack import CustomLlmdStack
from app.db.models.custom_user import CustomUser
from app.db.session import get_db
from app.services.clusters import argocd_placement_for
from app.services.llmd_manifests import (
    argo_app_name_for,
    build_argo_application,
    build_llmd_values,
    default_llmd_values,
)

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


class UpdateLlmdStackRequest(BaseModel):
    namespace: str | None = None
    values_yaml: str | None = None
    chart_repo: str | None = None
    chart_name: str | None = None
    chart_version: str | None = None
    epp_registry: str | None = None
    epp_repository: str | None = None
    epp_tag: str | None = None


class DefaultValuesRequest(BaseModel):
    target_model_name: str = ""
    endpoint_selector: str | None = None


def _parse_values_yaml(text: str) -> dict:
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


def _argo_status(obj: dict | None) -> dict:
    """Extract sync/health from an Application CR (Unknown when absent)."""
    if not obj:
        return {"sync_status": "Unknown", "health_status": "Unknown", "status_message": None}
    st = obj.get("status", {}) or {}
    return {
        "sync_status": (st.get("sync") or {}).get("status", "Unknown"),
        "health_status": (st.get("health") or {}).get("status", "Unknown"),
        "status_message": (st.get("health") or {}).get("message"),
    }


def _k8s_error_message(e: Exception) -> str:
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


def _chart_source(stack: CustomLlmdStack) -> tuple[str, str, str]:
    return (
        stack.chart_repo or settings.llmd_chart_repo,
        stack.chart_name or settings.llmd_chart_name,
        stack.chart_version or settings.llmd_chart_version,
    )


def _epp_image(stack: CustomLlmdStack) -> tuple[str, str, str]:
    return (
        stack.epp_registry or settings.llmd_epp_image_registry,
        stack.epp_repository or settings.llmd_epp_image_repository,
        stack.epp_tag or settings.llmd_epp_image_tag,
    )


def _values_for(stack: CustomLlmdStack) -> dict:
    registry, repository, tag = _epp_image(stack)
    return build_llmd_values(stack, epp_registry=registry, epp_repository=repository, epp_tag=tag)


def _application_for(stack: CustomLlmdStack, argocd_namespace: str, destination_server: str) -> dict:
    chart_repo, chart_name, chart_version = _chart_source(stack)
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


def _require_valid_name(name: str) -> str:
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


async def _live_status(db: AsyncSession, stack: CustomLlmdStack) -> dict:
    try:
        k8s, argocd_ns, _dest = await argocd_placement_for(db, stack.cluster_id)
        obj = await k8s.get_application(argocd_ns, stack.argo_app_name)
        return _argo_status(obj)
    except Exception as e:  # noqa: BLE001 — status is best-effort
        logger.info("llm-d status read failed for %s: %s", stack.name, e)
        return _argo_status(None)


def _serialize(stack: CustomLlmdStack, status_fields: dict) -> dict:
    return {
        "id": str(stack.id),
        "name": stack.name,
        "target_model_name": stack.target_model_name,
        "cluster_id": str(stack.cluster_id) if stack.cluster_id else None,
        "namespace": stack.namespace,
        "argo_app_name": stack.argo_app_name,
        "chart_repo": _chart_source(stack)[0],
        "chart_name": _chart_source(stack)[1],
        "chart_version": _chart_source(stack)[2],
        "epp_image": "{}/{}:{}".format(*_epp_image(stack)),
        "chart_overrides": {
            "chart_repo": stack.chart_repo,
            "chart_name": stack.chart_name,
            "chart_version": stack.chart_version,
            "epp_registry": stack.epp_registry,
            "epp_repository": stack.epp_repository,
            "epp_tag": stack.epp_tag,
        },
        "helm_values": stack.helm_values,
        "values_yaml": (
            yaml.safe_dump(stack.helm_values, sort_keys=False, default_flow_style=False)
            if stack.helm_values
            else ""
        ),
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
    return {"stacks": [_serialize(s, await _live_status(db, s)) for s in rows]}


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
        live_error = _k8s_error_message(e)

    return {
        "effective_values": stack.values_snapshot,
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
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_stack(
    body: CreateLlmdStackRequest,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    app_name = _require_valid_name(body.name)
    if not body.namespace or not body.namespace.strip():
        raise HTTPException(status_code=400, detail="Namespace is required.")
    if (await db.execute(select(CustomLlmdStack).where(CustomLlmdStack.name == body.name))).scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Stack '{body.name}' already exists")

    helm_values = _parse_values_yaml(body.values_yaml) or default_llmd_values(
        body.target_model_name,
        epp_registry=settings.llmd_epp_image_registry,
        epp_repository=settings.llmd_epp_image_repository,
        epp_tag=settings.llmd_epp_image_tag,
    )
    stack = CustomLlmdStack(
        id=uuid.uuid4(),
        name=body.name,
        target_model_name=body.target_model_name,
        cluster_id=uuid.UUID(body.cluster_id) if body.cluster_id else None,
        namespace=body.namespace,
        argo_app_name=app_name,
        helm_values=helm_values,
        values_snapshot={},
        chart_repo=(body.chart_repo or "").strip() or None,
        chart_name=(body.chart_name or "").strip() or None,
        chart_version=(body.chart_version or "").strip() or None,
        epp_registry=(body.epp_registry or "").strip() or None,
        epp_repository=(body.epp_repository or "").strip() or None,
        epp_tag=(body.epp_tag or "").strip() or None,
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    stack.values_snapshot = _values_for(stack)
    db.add(stack)
    await db.flush()

    try:
        k8s, argocd_ns, dest_server = await argocd_placement_for(db, stack.cluster_id)
        await k8s.apply_application(argocd_ns, _application_for(stack, argocd_ns, dest_server))
    except Exception as e:
        logger.exception("ArgoCD Application apply failed for stack %s", stack.name)
        raise HTTPException(status_code=502, detail=f"ArgoCD apply failed: {_k8s_error_message(e)}")
    await db.commit()
    await db.refresh(stack)
    return _serialize(stack, await _live_status(db, stack))


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
        "values_yaml": yaml.safe_dump(values, sort_keys=False, default_flow_style=False),
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
        stack.helm_values = _parse_values_yaml(body.values_yaml)
    for field in ("chart_repo", "chart_name", "chart_version", "epp_registry", "epp_repository", "epp_tag"):
        val = getattr(body, field)
        if val is not None:
            setattr(stack, field, val.strip() or None)
    stack.values_snapshot = _values_for(stack)
    stack.updated_by = user.user_id
    await db.flush()

    try:
        k8s, argocd_ns, dest_server = await argocd_placement_for(db, stack.cluster_id)
        await k8s.apply_application(argocd_ns, _application_for(stack, argocd_ns, dest_server))
    except Exception as e:
        logger.exception("ArgoCD Application update failed for stack %s", stack.name)
        raise HTTPException(status_code=502, detail=f"ArgoCD update failed: {_k8s_error_message(e)}")
    await db.commit()
    await db.refresh(stack)
    return _serialize(stack, await _live_status(db, stack))


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
    try:
        k8s, argocd_ns, _dest = await argocd_placement_for(db, stack.cluster_id)
        await k8s.delete_application(argocd_ns, stack.argo_app_name)
    except Exception as e:
        logger.exception("ArgoCD Application delete failed for stack %s", stack.name)
        raise HTTPException(status_code=502, detail=f"ArgoCD delete failed: {_k8s_error_message(e)}")
    await db.delete(stack)
    await db.commit()
    return {"ok": True}
