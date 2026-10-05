"""GPU type profiles per cluster + node discovery (Super User only).

Routes hang off the cluster registry; ``{cluster_id}`` may be ``default`` for
the portal's own kubeconfig cluster (profiles with cluster_id NULL).
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_super_user
from app.clients.k8s import K8sNotConfigured
from app.db.models.custom_gpu_profile import CustomGpuProfile
from app.db.models.custom_k8s_cluster import CustomK8sCluster
from app.db.models.custom_user import CustomUser
from app.db.session import get_db
from app.services.clusters import k8s_for_cluster
from app.services.gpu_profile_store import cluster_label_key, list_profiles, parse_cluster_id
from app.services.gpu_profiles import DEFAULT_GPU_RESOURCE_KEY, profile_label_key, validate_profile_name

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["gpu-profiles"])

NODE_SCAN_TIMEOUT = 5.0


class GpuProfileBody(BaseModel):
    name: str
    label_key: str | None = None  # None → cluster.gpu_label_key
    label_value: str = Field(min_length=1, max_length=128)
    gpu_resource_key: str = DEFAULT_GPU_RESOURCE_KEY
    tolerations: list | None = None
    vram_gb: int | None = Field(None, ge=1)
    description: str | None = None
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return validate_profile_name(v)

    @field_validator("label_key", "gpu_resource_key")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        v = (v or "").strip()
        return v or None


def _cid(raw: str) -> uuid.UUID | None:
    try:
        return parse_cluster_id(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="cluster_id must be a UUID or 'default'")


def _serialize(p: CustomGpuProfile, label_key: str) -> dict:
    return {
        "id": str(p.id),
        "cluster_id": str(p.cluster_id) if p.cluster_id else None,
        "name": p.name,
        "label_key": p.label_key,
        "effective_label_key": profile_label_key(p, label_key),
        "label_value": p.label_value,
        "gpu_resource_key": p.gpu_resource_key,
        "tolerations": p.tolerations,
        "vram_gb": p.vram_gb,
        "description": p.description,
        "enabled": p.enabled,
        "created_by": p.created_by,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


@router.get("/k8s-clusters/{cluster_id}/gpu-profiles")
async def list_gpu_profiles(
    cluster_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    cid = _cid(cluster_id)
    label_key = await cluster_label_key(db, cid)
    return {"label_key": label_key, "profiles": [_serialize(p, label_key) for p in await list_profiles(db, cid)]}


@router.post("/k8s-clusters/{cluster_id}/gpu-profiles", status_code=status.HTTP_201_CREATED)
async def create_gpu_profile(
    cluster_id: str,
    body: GpuProfileBody,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    cid = _cid(cluster_id)
    existing = (
        await db.execute(
            select(CustomGpuProfile).where(CustomGpuProfile.cluster_id == cid, CustomGpuProfile.name == body.name)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"GPU profile '{body.name}' already exists on this cluster")
    row = CustomGpuProfile(
        id=uuid.uuid4(),
        cluster_id=cid,
        created_by=user.user_id,
        updated_by=user.user_id,
        **body.model_dump(),
    )
    row.gpu_resource_key = body.gpu_resource_key or DEFAULT_GPU_RESOURCE_KEY
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        raise HTTPException(status_code=409, detail=f"GPU profile '{body.name}' already exists on this cluster")
    return _serialize(row, await cluster_label_key(db, cid))


async def _profile_or_404(db: AsyncSession, cid: uuid.UUID | None, profile_id: str) -> CustomGpuProfile:
    try:
        pid = uuid.UUID(profile_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="GPU profile not found")
    row = (
        await db.execute(select(CustomGpuProfile).where(CustomGpuProfile.id == pid, CustomGpuProfile.cluster_id == cid))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="GPU profile not found")
    return row


@router.put("/k8s-clusters/{cluster_id}/gpu-profiles/{profile_id}")
async def update_gpu_profile(
    cluster_id: str,
    profile_id: str,
    body: GpuProfileBody,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    cid = _cid(cluster_id)
    row = await _profile_or_404(db, cid, profile_id)
    clash = (
        await db.execute(
            select(CustomGpuProfile).where(
                CustomGpuProfile.cluster_id == cid, CustomGpuProfile.name == body.name, CustomGpuProfile.id != row.id
            )
        )
    ).scalar_one_or_none()
    if clash is not None:
        raise HTTPException(status_code=409, detail=f"GPU profile '{body.name}' already exists on this cluster")
    for k, v in body.model_dump().items():
        setattr(row, k, v)
    row.gpu_resource_key = body.gpu_resource_key or DEFAULT_GPU_RESOURCE_KEY
    row.updated_by = user.user_id
    await db.flush()
    await db.refresh(row)
    return _serialize(row, await cluster_label_key(db, cid))


@router.delete("/k8s-clusters/{cluster_id}/gpu-profiles/{profile_id}")
async def delete_gpu_profile(
    cluster_id: str,
    profile_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    cid = _cid(cluster_id)
    row = await _profile_or_404(db, cid, profile_id)
    await db.delete(row)
    await db.flush()
    return {"deleted": True, "id": profile_id}


@router.get("/k8s-clusters/{cluster_id}/gpu-nodes")
async def list_gpu_nodes(
    cluster_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Live, read-only view of GPU nodes grouped by the cluster's label value.

    ``groups`` carries one entry per label value (``label_value`` None for
    unlabelled GPU nodes) with allocatable/requested/available summed over
    the resource keys that profiles on this cluster use (always incl. the
    default). Errors never fail the page: they come back in ``errors``.
    """
    import asyncio

    cid = _cid(cluster_id)
    label_key = await cluster_label_key(db, cid)
    profiles = await list_profiles(db, cid)
    resource_keys = sorted({DEFAULT_GPU_RESOURCE_KEY, *(p.gpu_resource_key for p in profiles)})
    errors: list[str] = []
    nodes: list[dict] = []
    try:
        k8s = await k8s_for_cluster(db, cid)
        nodes = await asyncio.wait_for(k8s.list_gpu_nodes(label_key, resource_keys), timeout=NODE_SCAN_TIMEOUT)
    except K8sNotConfigured as e:
        errors.append(str(e))
    except TimeoutError:
        errors.append(f"node scan timed out after {NODE_SCAN_TIMEOUT:g}s")
    except Exception as e:  # noqa: BLE001 - discovery is best-effort
        logger.warning("GPU node scan failed for cluster %s: %s", cluster_id, e)
        errors.append(str(e) or type(e).__name__)

    groups: dict[str | None, dict] = {}
    for n in nodes:
        g = groups.setdefault(
            n["label_value"],
            {"label_value": n["label_value"], "nodes": 0, "allocatable": {}, "requested": {}, "available": {}},
        )
        g["nodes"] += 1
        for key in resource_keys:
            alloc = n["allocatable"].get(key, 0) if n["schedulable"] else 0
            g["allocatable"][key] = g["allocatable"].get(key, 0) + alloc
            g["requested"][key] = g["requested"].get(key, 0) + n["requested"].get(key, 0)
            g["available"][key] = max(0, g["allocatable"][key] - g["requested"][key])
    by_value = {p.label_value: p.name for p in profiles if profile_label_key(p, label_key) == label_key}
    for g in groups.values():
        g["profile_name"] = by_value.get(g["label_value"])
    return {
        "label_key": label_key,
        "resource_keys": resource_keys,
        "groups": sorted(groups.values(), key=lambda g: (g["label_value"] is None, g["label_value"] or "")),
        "nodes": nodes,
        "errors": errors,
    }


@router.get("/gpu-types")
async def list_gpu_types(
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Union of enabled profile names across clusters, for the recipe picker."""
    rows = list(
        (await db.execute(select(CustomGpuProfile).where(CustomGpuProfile.enabled.is_(True)))).scalars().all()
    )
    clusters = {c.id: c for c in (await db.execute(select(CustomK8sCluster))).scalars().all()}
    types: dict[str, dict] = {}
    for p in rows:
        entry = types.setdefault(p.name, {"name": p.name, "clusters": []})
        c = clusters.get(p.cluster_id) if p.cluster_id else None
        entry["clusters"].append(
            {
                "cluster_id": str(p.cluster_id) if p.cluster_id else None,
                "cluster_name": c.name if c else "default",
                "profile_id": str(p.id),
                "gpu_resource_key": p.gpu_resource_key,
                "vram_gb": p.vram_gb,
                "label_value": p.label_value,
            }
        )
    return {"types": sorted(types.values(), key=lambda t: t["name"])}
