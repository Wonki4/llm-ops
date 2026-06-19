"""Admin endpoints for managing K8s-backed LLM deployments.

PR-A scope: create, list, get, delete. Status only reflects whatever the
worker has last synced — the worker is added in PR-B.
"""

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_super_user
from app.clients.k8s import K8sClient, K8sNotConfigured, get_k8s_client  # noqa: F401
from app.db.models.custom_model_deployment import CustomModelDeployment
from app.db.models.custom_user import CustomUser
from app.db.session import get_db
from app.services.clusters import k8s_for_cluster
from app.services.model_deployment_manifests import build_all, k8s_resource_names

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/model-deployments", tags=["model-deployments"])


DEFAULT_VLLM_IMAGE = "vllm/vllm-openai:latest"


class CreateDeploymentRequest(BaseModel):
    model_name: str
    cluster_id: str | None = None  # registered K8s cluster; None = portal default
    namespace: str = "default"
    image: str = DEFAULT_VLLM_IMAGE
    replicas: int = Field(1, ge=0)
    gpu_count: int = Field(1, ge=0)
    gpu_resource_key: str = "nvidia.com/gpu"
    cpu_request: str | None = None
    cpu_limit: str | None = None
    memory_request: str | None = None
    memory_limit: str | None = None
    node_selector: dict | None = None
    tolerations: list | None = None
    pvc_name: str | None = None
    pvc_mount_path: str | None = None
    model_path: str
    vllm_extra_args: list[str] | None = None
    env: dict | None = None
    ingress_host: str
    ingress_path: str = "/"
    ingress_class: str = "nginx"


class UpdateDeploymentRequest(BaseModel):
    image: str | None = None
    replicas: int | None = Field(None, ge=0)
    gpu_count: int | None = Field(None, ge=0)
    cpu_request: str | None = None
    cpu_limit: str | None = None
    memory_request: str | None = None
    memory_limit: str | None = None
    node_selector: dict | None = None
    tolerations: list | None = None
    pvc_name: str | None = None
    pvc_mount_path: str | None = None
    model_path: str | None = None
    vllm_extra_args: list[str] | None = None
    env: dict | None = None
    ingress_host: str | None = None
    ingress_path: str | None = None
    ingress_class: str | None = None


def _serialize(d: CustomModelDeployment) -> dict:
    return {
        "id": str(d.id),
        "model_name": d.model_name,
        "cluster_id": str(d.cluster_id) if d.cluster_id else None,
        "namespace": d.namespace,
        "image": d.image,
        "replicas": d.replicas,
        "gpu_count": d.gpu_count,
        "gpu_resource_key": d.gpu_resource_key,
        "cpu_request": d.cpu_request,
        "cpu_limit": d.cpu_limit,
        "memory_request": d.memory_request,
        "memory_limit": d.memory_limit,
        "node_selector": d.node_selector,
        "tolerations": d.tolerations,
        "pvc_name": d.pvc_name,
        "pvc_mount_path": d.pvc_mount_path,
        "model_path": d.model_path,
        "vllm_extra_args": d.vllm_extra_args,
        "env": d.env,
        "ingress_host": d.ingress_host,
        "ingress_path": d.ingress_path,
        "ingress_class": d.ingress_class,
        "status": d.status,
        "status_message": d.status_message,
        "ready_replicas": d.ready_replicas,
        "service_cluster_ip": d.service_cluster_ip,
        "litellm_model_id": d.litellm_model_id,
        "last_synced_at": d.last_synced_at.isoformat() if d.last_synced_at else None,
        "created_by": d.created_by,
        "updated_by": d.updated_by,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
    }


@router.get("")
async def list_deployments(
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(CustomModelDeployment).order_by(CustomModelDeployment.created_at.desc()))
    return {"deployments": [_serialize(d) for d in result.scalars().all()]}


@router.get("/{deployment_id}")
async def get_deployment(
    deployment_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(CustomModelDeployment).where(CustomModelDeployment.id == uuid.UUID(deployment_id)))
    dep = result.scalar_one_or_none()
    if not dep:
        raise HTTPException(status_code=404, detail="Deployment not found")
    return _serialize(dep)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_deployment(
    body: CreateDeploymentRequest,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    # Uniqueness on model_name
    existing = await db.execute(select(CustomModelDeployment).where(CustomModelDeployment.model_name == body.model_name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Deployment for '{body.model_name}' already exists")

    k8s = await k8s_for_cluster(db, body.cluster_id)

    dep = CustomModelDeployment(
        id=uuid.uuid4(),
        model_name=body.model_name,
        cluster_id=uuid.UUID(body.cluster_id) if body.cluster_id else None,
        namespace=body.namespace,
        image=body.image,
        replicas=body.replicas,
        gpu_count=body.gpu_count,
        gpu_resource_key=body.gpu_resource_key,
        cpu_request=body.cpu_request,
        cpu_limit=body.cpu_limit,
        memory_request=body.memory_request,
        memory_limit=body.memory_limit,
        node_selector=body.node_selector,
        tolerations=body.tolerations,
        pvc_name=body.pvc_name,
        pvc_mount_path=body.pvc_mount_path,
        model_path=body.model_path,
        vllm_extra_args=body.vllm_extra_args,
        env=body.env,
        ingress_host=body.ingress_host,
        ingress_path=body.ingress_path,
        ingress_class=body.ingress_class,
        status="Pending",
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    db.add(dep)
    await db.flush()
    await db.refresh(dep)

    # Apply to K8s
    try:
        await k8s.create_or_patch(dep.namespace, build_all(dep))
    except K8sNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception:
        logger.exception("K8s apply failed for %s", dep.model_name)
        raise HTTPException(status_code=502, detail="Failed to apply K8s resources; check logs")

    return _serialize(dep)


@router.put("/{deployment_id}")
async def update_deployment(
    deployment_id: str,
    body: UpdateDeploymentRequest,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Update mutable fields and reapply K8s manifests (rolls Deployment).

    Update covers both upgrade (image / args) and scale (replicas, gpu_count).
    """
    result = await db.execute(select(CustomModelDeployment).where(CustomModelDeployment.id == uuid.UUID(deployment_id)))
    dep = result.scalar_one_or_none()
    if not dep:
        raise HTTPException(status_code=404, detail="Deployment not found")

    k8s = await k8s_for_cluster(db, dep.cluster_id)
    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(dep, field, value)
    dep.updated_by = user.user_id
    dep.status = "Updating"
    dep.last_synced_at = datetime.utcnow()
    await db.flush()
    await db.refresh(dep)

    try:
        await k8s.create_or_patch(dep.namespace, build_all(dep))
    except K8sNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception:
        logger.exception("K8s apply failed for %s", dep.model_name)
        raise HTTPException(status_code=502, detail="Failed to apply K8s resources; check logs")

    return _serialize(dep)


@router.delete("/{deployment_id}")
async def delete_deployment(
    deployment_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(CustomModelDeployment).where(CustomModelDeployment.id == uuid.UUID(deployment_id)))
    dep = result.scalar_one_or_none()
    if not dep:
        raise HTTPException(status_code=404, detail="Deployment not found")

    k8s = await k8s_for_cluster(db, dep.cluster_id)
    try:
        await k8s.delete(dep.namespace, k8s_resource_names(dep))
    except K8sNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception:
        logger.exception("K8s delete failed for %s", dep.model_name)
        # Continue to delete the row — orphan K8s resources are easier to clean than orphan DB rows.

    await db.delete(dep)
    return {"deleted": True, "id": deployment_id}


# ─── Deployment events (status transitions + alerts) ────────────


def _serialize_event(e) -> dict:
    return {
        "id": str(e.id),
        "deployment_id": str(e.deployment_id),
        "event_type": e.event_type,
        "severity": e.severity,
        "from_status": e.from_status,
        "to_status": e.to_status,
        "message": e.message,
        "seen": e.seen,
        "alert_sent": e.alert_sent,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


@router.get("/{deployment_id}/events")
async def list_deployment_events(
    deployment_id: str,
    limit: int = 100,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Most-recent-first list of status events for one deployment."""
    from app.db.models.custom_model_deployment_event import CustomModelDeploymentEvent

    result = await db.execute(
        select(CustomModelDeploymentEvent)
        .where(CustomModelDeploymentEvent.deployment_id == uuid.UUID(deployment_id))
        .order_by(CustomModelDeploymentEvent.created_at.desc())
        .limit(min(limit, 500))
    )
    return {"events": [_serialize_event(e) for e in result.scalars().all()]}


@router.post("/{deployment_id}/events/{event_id}/ack")
async def ack_deployment_event(
    deployment_id: str,
    event_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Mark a single event as seen so the UI can hide it from unread badges."""
    from app.db.models.custom_model_deployment_event import CustomModelDeploymentEvent

    result = await db.execute(
        select(CustomModelDeploymentEvent).where(CustomModelDeploymentEvent.id == uuid.UUID(event_id))
    )
    event = result.scalar_one_or_none()
    if not event or str(event.deployment_id) != deployment_id:
        raise HTTPException(status_code=404, detail="Event not found")
    event.seen = True
    await db.flush()
    return _serialize_event(event)
