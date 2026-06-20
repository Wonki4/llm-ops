"""Admin endpoints for registering Kubernetes clusters (multi-cluster settings).

Stores cluster connection configs (kubeconfig + context). The kubeconfig is
encrypted at rest and never returned to the client — list/get responses are
masked and expose only a non-secret summary (parsed api_server). Targeting (which
cluster a deployment/benchmark runs on) is handled later in each menu via the
cluster ``id``.
"""

import logging
import uuid

import yaml
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_super_user
from app.clients.k8s import probe_cluster
from app.db.models.custom_k8s_cluster import CustomK8sCluster
from app.db.models.custom_user import CustomUser
from app.db.session import get_db
from app.services import crypto
from app.services.benchmark_manifests import pvc_pair_incomplete

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/k8s-clusters", tags=["k8s-clusters"])


class CreateClusterRequest(BaseModel):
    name: str
    context: str
    namespace: str = "default"
    kubeconfig: str
    description: str | None = None
    is_default: bool = False
    default_pvc_name: str | None = None
    default_pvc_mount_path: str | None = None


class UpdateClusterRequest(BaseModel):
    name: str | None = None
    context: str | None = None
    namespace: str | None = None
    kubeconfig: str | None = None  # omitted/empty = keep existing
    description: str | None = None
    is_default: bool | None = None
    default_pvc_name: str | None = None
    default_pvc_mount_path: str | None = None


class TestClusterRequest(BaseModel):
    kubeconfig: str
    context: str


def _parse_kubeconfig(raw: str, context: str) -> tuple[dict, str | None]:
    """Validate a pasted kubeconfig and resolve the api server for `context`.

    Returns (parsed_dict, api_server). Raises HTTPException(400) on invalid YAML
    or a context that isn't present in the file.
    """
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid kubeconfig YAML: {e}")
    if not isinstance(parsed, dict) or "contexts" not in parsed:
        raise HTTPException(status_code=400, detail="Not a valid kubeconfig (no contexts)")

    ctx_entry = next(
        (c for c in parsed.get("contexts", []) if c.get("name") == context), None
    )
    if ctx_entry is None:
        available = ", ".join(c.get("name", "?") for c in parsed.get("contexts", []))
        raise HTTPException(
            status_code=400,
            detail=f"Context '{context}' not found in kubeconfig. Available: {available or 'none'}",
        )
    cluster_name = (ctx_entry.get("context") or {}).get("cluster")
    cluster_entry = next(
        (c for c in parsed.get("clusters", []) if c.get("name") == cluster_name), None
    )
    api_server = (cluster_entry or {}).get("cluster", {}).get("server")
    return parsed, api_server


def _serialize(c: CustomK8sCluster) -> dict:
    """Masked representation — never includes the kubeconfig."""
    return {
        "id": str(c.id),
        "name": c.name,
        "context": c.context,
        "namespace": c.namespace,
        "api_server": c.api_server,
        "is_default": c.is_default,
        "description": c.description,
        "default_pvc_name": c.default_pvc_name,
        "default_pvc_mount_path": c.default_pvc_mount_path,
        "has_kubeconfig": bool(c.kubeconfig_encrypted),
        "created_by": c.created_by,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


async def _unset_other_defaults(db: AsyncSession, keep_id: uuid.UUID | None) -> None:
    stmt = update(CustomK8sCluster).values(is_default=False)
    if keep_id is not None:
        stmt = stmt.where(CustomK8sCluster.id != keep_id)
    await db.execute(stmt)


@router.get("")
async def list_clusters(
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(CustomK8sCluster).order_by(
            CustomK8sCluster.is_default.desc(), CustomK8sCluster.created_at.desc()
        )
    )
    return {"clusters": [_serialize(c) for c in result.scalars().all()]}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_cluster(
    body: CreateClusterRequest,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    existing = await db.execute(
        select(CustomK8sCluster).where(CustomK8sCluster.name == body.name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Cluster '{body.name}' already exists")

    if pvc_pair_incomplete(body.default_pvc_name, body.default_pvc_mount_path):
        raise HTTPException(
            status_code=400,
            detail="default_pvc_name and default_pvc_mount_path must be set together",
        )

    _parsed, api_server = _parse_kubeconfig(body.kubeconfig, body.context)

    cluster = CustomK8sCluster(
        id=uuid.uuid4(),
        name=body.name,
        context=body.context,
        namespace=body.namespace,
        kubeconfig_encrypted=crypto.encrypt(body.kubeconfig),
        api_server=api_server,
        is_default=body.is_default,
        description=body.description,
        default_pvc_name=body.default_pvc_name or None,
        default_pvc_mount_path=body.default_pvc_mount_path or None,
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    db.add(cluster)
    await db.flush()
    if body.is_default:
        await _unset_other_defaults(db, keep_id=cluster.id)
    await db.commit()
    await db.refresh(cluster)
    return _serialize(cluster)


@router.put("/{cluster_id}")
async def update_cluster(
    cluster_id: str,
    body: UpdateClusterRequest,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(CustomK8sCluster).where(CustomK8sCluster.id == uuid.UUID(cluster_id))
    )
    cluster = result.scalar_one_or_none()
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")

    if body.name is not None:
        cluster.name = body.name
    if body.namespace is not None:
        cluster.namespace = body.namespace
    if body.description is not None:
        cluster.description = body.description
    if body.context is not None:
        cluster.context = body.context
    if body.default_pvc_name is not None:
        cluster.default_pvc_name = body.default_pvc_name or None
    if body.default_pvc_mount_path is not None:
        cluster.default_pvc_mount_path = body.default_pvc_mount_path or None
    if pvc_pair_incomplete(cluster.default_pvc_name, cluster.default_pvc_mount_path):
        raise HTTPException(
            status_code=400,
            detail="default_pvc_name and default_pvc_mount_path must be set together",
        )

    # Re-validate + re-parse api_server when kubeconfig or context changes.
    new_kubeconfig = body.kubeconfig if (body.kubeconfig or "").strip() else None
    if new_kubeconfig is not None or body.context is not None:
        raw = new_kubeconfig if new_kubeconfig is not None else crypto.decrypt(
            cluster.kubeconfig_encrypted
        )
        _parsed, api_server = _parse_kubeconfig(raw, cluster.context)
        cluster.api_server = api_server
        if new_kubeconfig is not None:
            cluster.kubeconfig_encrypted = crypto.encrypt(new_kubeconfig)

    if body.is_default is not None:
        cluster.is_default = body.is_default

    cluster.updated_by = user.user_id
    await db.flush()
    if body.is_default:
        await _unset_other_defaults(db, keep_id=cluster.id)
    await db.commit()
    await db.refresh(cluster)
    return _serialize(cluster)


@router.delete("/{cluster_id}")
async def delete_cluster(
    cluster_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(CustomK8sCluster).where(CustomK8sCluster.id == uuid.UUID(cluster_id))
    )
    cluster = result.scalar_one_or_none()
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    await db.delete(cluster)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Cluster is in use by a deployment or benchmark and cannot be deleted",
        )
    return {"ok": True}


@router.post("/test")
async def test_unsaved_cluster(
    body: TestClusterRequest,
    user: CustomUser = Depends(require_super_user),
) -> dict:
    """Test a kubeconfig+context before saving."""
    parsed, _ = _parse_kubeconfig(body.kubeconfig, body.context)
    return await _run_probe(parsed, body.context)


@router.post("/{cluster_id}/test")
async def test_saved_cluster(
    cluster_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Test a stored cluster's connection."""
    result = await db.execute(
        select(CustomK8sCluster).where(CustomK8sCluster.id == uuid.UUID(cluster_id))
    )
    cluster = result.scalar_one_or_none()
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    raw = crypto.decrypt(cluster.kubeconfig_encrypted)
    parsed, _ = _parse_kubeconfig(raw, cluster.context)
    return await _run_probe(parsed, cluster.context)


async def _run_probe(parsed: dict, context: str) -> dict:
    try:
        version = await probe_cluster(parsed, context)
        return {"ok": True, "server_version": version, "message": "Connected"}
    except Exception as e:  # noqa: BLE001 — surface any connection error to the admin
        logger.info("Cluster connection test failed: %s", e)
        return {"ok": False, "server_version": None, "message": str(e)}
