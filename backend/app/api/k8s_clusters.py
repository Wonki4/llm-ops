"""Admin CRUD for registered K8s clusters.

Each row contains the raw kubeconfig YAML for one cluster. Stored as text;
envelope encryption is a follow-up. Only super_user can list/edit.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_super_user
from app.clients.k8s import K8sClient, K8sNotConfigured
from app.db.models.custom_k8s_cluster import CustomK8sCluster
from app.db.models.custom_user import CustomUser
from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/k8s-clusters", tags=["k8s-clusters"])


class CreateClusterRequest(BaseModel):
    name: str
    kubeconfig_content: str
    default_namespace: str = "default"
    description: str | None = None
    enabled: bool = True


class UpdateClusterRequest(BaseModel):
    kubeconfig_content: str | None = None
    default_namespace: str | None = None
    description: str | None = None
    enabled: bool | None = None


def _serialize(c: CustomK8sCluster, *, include_kubeconfig: bool = False) -> dict:
    """Default omits kubeconfig_content; pass include_kubeconfig=True for edit."""
    out = {
        "id": str(c.id),
        "name": c.name,
        "default_namespace": c.default_namespace,
        "description": c.description,
        "enabled": c.enabled,
        "created_by": c.created_by,
        "updated_by": c.updated_by,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }
    if include_kubeconfig:
        out["kubeconfig_content"] = c.kubeconfig_content
    return out


@router.get("")
async def list_clusters(
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(CustomK8sCluster).order_by(CustomK8sCluster.name))
    return {"clusters": [_serialize(c) for c in result.scalars().all()]}


@router.get("/{cluster_id}")
async def get_cluster(
    cluster_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(CustomK8sCluster).where(CustomK8sCluster.id == uuid.UUID(cluster_id)))
    c = result.scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Cluster not found")
    return _serialize(c, include_kubeconfig=True)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_cluster(
    body: CreateClusterRequest,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    existing = await db.execute(select(CustomK8sCluster).where(CustomK8sCluster.name == name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Cluster '{name}' already exists")

    cluster = CustomK8sCluster(
        id=uuid.uuid4(),
        name=name,
        kubeconfig_content=body.kubeconfig_content,
        default_namespace=body.default_namespace or "default",
        description=body.description,
        enabled=body.enabled,
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    db.add(cluster)
    await db.flush()
    await db.refresh(cluster)
    return _serialize(cluster, include_kubeconfig=True)


@router.put("/{cluster_id}")
async def update_cluster(
    cluster_id: str,
    body: UpdateClusterRequest,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(CustomK8sCluster).where(CustomK8sCluster.id == uuid.UUID(cluster_id)))
    c = result.scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Cluster not found")
    updates = body.model_dump(exclude_unset=True)
    for k, v in updates.items():
        setattr(c, k, v)
    c.updated_by = user.user_id
    await db.flush()
    await db.refresh(c)
    return _serialize(c, include_kubeconfig=True)


@router.delete("/{cluster_id}")
async def delete_cluster(
    cluster_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(CustomK8sCluster).where(CustomK8sCluster.id == uuid.UUID(cluster_id)))
    c = result.scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Cluster not found")
    try:
        await db.delete(c)
        await db.flush()
    except Exception as e:
        # Foreign key constraint from deployments will land us here.
        raise HTTPException(
            status_code=409,
            detail="Cluster is still referenced by deployments. Delete or re-point them first.",
        ) from e
    return {"deleted": True, "id": cluster_id}


@router.post("/{cluster_id}/ping")
async def ping_cluster(
    cluster_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Test connectivity by calling /version on the cluster's API server."""
    result = await db.execute(select(CustomK8sCluster).where(CustomK8sCluster.id == uuid.UUID(cluster_id)))
    c = result.scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Cluster not found")
    k8s = K8sClient(c.kubeconfig_content)
    try:
        info = await k8s.ping()
        return {"ok": True, **info}
    except K8sNotConfigured as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": str(e)}
