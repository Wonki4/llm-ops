# backend/app/api/storages.py
"""Storage objects + per-cluster/namespace bindings (Super User only).

A storage is mounted by recipes/deployments by id; its binding for the deploy
target decides the concrete PVC or NFS volume (see services/storage_store).
"""

import re
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_super_user
from app.db.models.custom_k8s_cluster import CustomK8sCluster
from app.db.models.custom_storage import CustomStorage, CustomStorageBinding
from app.db.models.custom_user import CustomUser
from app.db.session import get_db
from app.services.gpu_profile_store import parse_cluster_id
from app.services.storage_mounts import pick_binding, snapshot, stale_entries, validate_storage_name
from app.services.storage_store import (
    PVC_LOOKUP_TIMEOUT,
    bindings_for,
    list_pvcs_timed,
    load_storages,
    null_safe_eq,
    pvc_phases,
    usage_index,
)

router = APIRouter(prefix="/api/admin", tags=["storages"])

_DNS_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")


class StorageBody(BaseModel):
    name: str
    description: str | None = None
    kind: Literal["weights", "cache", "data", "other"] = "weights"

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return validate_storage_name(v)


class BindingBody(BaseModel):
    cluster_id: str | None = None  # None / "default" = portal default cluster
    namespace: str | None = None  # None = whole cluster (NFS only)
    type: Literal["pvc", "nfs"]
    claim_name: str | None = None
    nfs_server: str | None = None
    nfs_path: str | None = None
    sub_path: str | None = None

    @field_validator("namespace", "claim_name", "nfs_server", "nfs_path", "sub_path")
    @classmethod
    def _blank_is_none(cls, v: str | None) -> str | None:
        v = (v or "").strip()
        return v or None

    @model_validator(mode="after")
    def _target(self):
        if self.namespace and not _DNS_LABEL.match(self.namespace):
            raise ValueError("namespace must be a DNS label")
        if self.type == "pvc":
            if not self.claim_name or not self.namespace:
                raise ValueError("a pvc binding needs claim_name and namespace")
            if self.nfs_server or self.nfs_path:
                raise ValueError("a pvc binding takes no nfs_server/nfs_path")
        else:
            if not self.nfs_server or not self.nfs_path:
                raise ValueError("an nfs binding needs nfs_server and nfs_path")
            if self.claim_name:
                raise ValueError("an nfs binding takes no claim_name")
        if self.sub_path and (self.sub_path.startswith("/") or ".." in self.sub_path.split("/")):
            raise ValueError("sub_path must be relative and must not contain ..")
        return self


class ResolveBody(BaseModel):
    storage_ids: list[str]
    cluster_id: str | None = None
    namespace: str = "default"


def _cid(raw: str | None) -> uuid.UUID | None:
    try:
        return parse_cluster_id(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="cluster_id must be a UUID or 'default'")


def _uuid_or_404(raw: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"{what} not found")


def _serialize(s: CustomStorage) -> dict:
    return {
        "id": str(s.id),
        "name": s.name,
        "description": s.description,
        "kind": s.kind,
        "created_by": s.created_by,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


def _cluster_label(cluster_id: uuid.UUID | None, cluster_names: dict) -> str:
    return cluster_names.get(cluster_id, "default") if cluster_id else "default"


def _serialize_binding(b: CustomStorageBinding, cluster_names: dict) -> dict:
    return {
        "id": str(b.id),
        "storage_id": str(b.storage_id),
        "cluster_id": str(b.cluster_id) if b.cluster_id else None,
        "cluster_name": _cluster_label(b.cluster_id, cluster_names),
        "namespace": b.namespace,
        "type": b.type,
        "claim_name": b.claim_name,
        "nfs_server": b.nfs_server,
        "nfs_path": b.nfs_path,
        "sub_path": b.sub_path,
        "updated_at": b.updated_at.isoformat() if getattr(b, "updated_at", None) else None,
    }


async def _storage_or_404(db: AsyncSession, storage_id: str) -> CustomStorage:
    sid = _uuid_or_404(storage_id, "Storage")
    row = (await db.execute(select(CustomStorage).where(CustomStorage.id == sid))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Storage not found")
    return row


async def _cluster_names(db: AsyncSession) -> dict:
    return {c.id: c.name for c in (await db.execute(select(CustomK8sCluster))).scalars().all()}


def _public_usage(u: dict | None, bindings: list | None = None) -> dict:
    """Usage for responses. With ``bindings`` (all of this storage's) each deployment gets ``stale``."""
    u = u or {"recipes": [], "deployments": [], "clusters": []}
    deployments = []
    for d in u["deployments"]:
        item = {"id": d["id"], "model_name": d["model_name"]}
        if bindings is not None:
            item["stale"] = bool(stale_entries(d["entries"], bindings, d["cluster_id"], d["namespace"]))
        deployments.append(item)
    return {"recipes": u["recipes"], "deployments": deployments, "clusters": u["clusters"]}


@router.get("/storages")
async def list_storages(
    kind: str | None = None,
    q: str | None = None,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    rows = list((await db.execute(select(CustomStorage).order_by(CustomStorage.name))).scalars().all())
    bindings = list((await db.execute(select(CustomStorageBinding))).scalars().all())
    names = await _cluster_names(db)
    usage = await usage_index(db)
    bound_on: dict[str, list[dict]] = {}
    for b in bindings:
        bound_on.setdefault(str(b.storage_id), []).append(
            {
                "cluster_id": str(b.cluster_id) if b.cluster_id else None,
                "cluster_name": _cluster_label(b.cluster_id, names),
                "namespace": b.namespace,
            }
        )
    needle = (q or "").strip().lower()
    out = []
    for s in rows:
        if kind and s.kind != kind:
            continue
        if needle and needle not in s.name and needle not in (s.description or "").lower():
            continue
        u = usage.get(str(s.id)) or {"recipes": [], "deployments": []}
        mine = sorted(bound_on.get(str(s.id), []), key=lambda x: (x["cluster_name"], x["namespace"] or ""))
        out.append(
            {
                **_serialize(s),
                "binding_count": len(mine),
                "bound_on": mine,
                "recipe_count": len(u["recipes"]),
                "deployment_count": len(u["deployments"]),
            }
        )
    return {"storages": out}


@router.post("/storages", status_code=status.HTTP_201_CREATED)
async def create_storage(
    body: StorageBody,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    clash = (await db.execute(select(CustomStorage).where(CustomStorage.name == body.name))).scalar_one_or_none()
    if clash is not None:
        raise HTTPException(status_code=409, detail=f"Storage '{body.name}' already exists")
    row = CustomStorage(id=uuid.uuid4(), created_by=user.user_id, updated_by=user.user_id, **body.model_dump())
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        raise HTTPException(status_code=409, detail=f"Storage '{body.name}' already exists")
    return _serialize(row)


@router.post("/storages/resolve")
async def resolve_storages(
    body: ResolveBody,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Deploy-dialog preview: the binding each storage resolves to, plus the PVC phase."""
    cid = _cid(body.cluster_id)
    ids = []
    for raw in body.storage_ids:
        try:
            ids.append(str(uuid.UUID(raw)))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"invalid storage id: {raw}")
    storages = await load_storages(db, ids)
    bindings = await bindings_for(db, ids, cid)
    phases: dict[str, str] | None = None
    looked_up = False
    results = []
    for sid in ids:
        b = pick_binding(bindings, sid, cid, body.namespace)
        phase = None
        if b is not None and b.type == "pvc":
            if not looked_up:
                phases, looked_up = await pvc_phases(db, cid, body.namespace), True
            phase = phases.get(b.claim_name, "missing") if phases is not None else "error"
        results.append(
            {
                "storage_id": sid,
                "name": storages[sid].name if sid in storages else None,
                "binding": snapshot(b) if b is not None else None,
                "pvc_phase": phase,
            }
        )
    return {"results": results}


@router.get("/storages/{storage_id}")
async def get_storage(
    storage_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    row = await _storage_or_404(db, storage_id)
    bindings = list(
        (await db.execute(select(CustomStorageBinding).where(CustomStorageBinding.storage_id == row.id)))
        .scalars()
        .all()
    )
    names = await _cluster_names(db)
    usage = (await usage_index(db)).get(str(row.id))
    return {
        **_serialize(row),
        "bindings": [_serialize_binding(b, names) for b in bindings],
        "usage": _public_usage(usage, bindings),
    }


@router.put("/storages/{storage_id}")
async def update_storage(
    storage_id: str,
    body: StorageBody,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    row = await _storage_or_404(db, storage_id)
    clash = (
        await db.execute(select(CustomStorage).where(CustomStorage.name == body.name, CustomStorage.id != row.id))
    ).scalar_one_or_none()
    if clash is not None:
        raise HTTPException(status_code=409, detail=f"Storage '{body.name}' already exists")
    for k, v in body.model_dump().items():
        setattr(row, k, v)
    row.updated_by = user.user_id
    await db.flush()
    await db.refresh(row)
    return _serialize(row)


@router.delete("/storages/{storage_id}")
async def delete_storage(
    storage_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    row = await _storage_or_404(db, storage_id)
    usage = _public_usage((await usage_index(db)).get(str(row.id)))
    if usage["recipes"] or usage["deployments"] or usage["clusters"]:
        raise HTTPException(status_code=409, detail={"message": "Storage is in use", "usage": usage})
    await db.delete(row)
    await db.flush()
    return {"deleted": True, "id": storage_id}


async def _binding_clash(db: AsyncSession, sid: uuid.UUID, body: BindingBody, cid, exclude: uuid.UUID | None):
    stmt = select(CustomStorageBinding).where(
        CustomStorageBinding.storage_id == sid,
        null_safe_eq(CustomStorageBinding.cluster_id, cid),
        null_safe_eq(CustomStorageBinding.namespace, body.namespace),
    )
    if exclude is not None:
        stmt = stmt.where(CustomStorageBinding.id != exclude)
    return (await db.execute(stmt)).scalar_one_or_none()


_SCOPE_TAKEN = "A binding for this cluster/namespace already exists"


@router.post("/storages/{storage_id}/bindings", status_code=status.HTTP_201_CREATED)
async def create_binding(
    storage_id: str,
    body: BindingBody,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    storage = await _storage_or_404(db, storage_id)
    cid = _cid(body.cluster_id)
    if await _binding_clash(db, storage.id, body, cid, None) is not None:
        raise HTTPException(status_code=409, detail=_SCOPE_TAKEN)
    row = CustomStorageBinding(
        id=uuid.uuid4(),
        storage_id=storage.id,
        cluster_id=cid,
        created_by=user.user_id,
        updated_by=user.user_id,
        **body.model_dump(exclude={"cluster_id"}),
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        raise HTTPException(status_code=409, detail=_SCOPE_TAKEN)
    return _serialize_binding(row, await _cluster_names(db))


async def _binding_or_404(db: AsyncSession, storage_id: str, binding_id: str) -> CustomStorageBinding:
    sid = _uuid_or_404(storage_id, "Storage")
    bid = _uuid_or_404(binding_id, "Binding")
    row = (
        await db.execute(
            select(CustomStorageBinding).where(CustomStorageBinding.id == bid, CustomStorageBinding.storage_id == sid)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Binding not found")
    return row


@router.put("/storages/{storage_id}/bindings/{binding_id}")
async def update_binding(
    storage_id: str,
    binding_id: str,
    body: BindingBody,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    row = await _binding_or_404(db, storage_id, binding_id)
    cid = _cid(body.cluster_id)
    if await _binding_clash(db, row.storage_id, body, cid, row.id) is not None:
        raise HTTPException(status_code=409, detail=_SCOPE_TAKEN)
    for k, v in body.model_dump(exclude={"cluster_id"}).items():
        setattr(row, k, v)
    row.cluster_id = cid
    row.updated_by = user.user_id
    await db.flush()
    await db.refresh(row)
    return _serialize_binding(row, await _cluster_names(db))


@router.delete("/storages/{storage_id}/bindings/{binding_id}")
async def delete_binding(
    storage_id: str,
    binding_id: str,
    dry_run: bool = False,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Running deployments keep their snapshot; ``affected_deployments`` are the
    ones resolved through this binding (they turn stale and fail to redeploy)."""
    row = await _binding_or_404(db, storage_id, binding_id)
    usage = (await usage_index(db)).get(str(row.storage_id)) or {"deployments": []}
    affected = [
        {"id": d["id"], "model_name": d["model_name"]} for d in usage["deployments"] if str(row.id) in d["binding_ids"]
    ]
    if dry_run:
        return {"deleted": False, "affected_deployments": affected}
    await db.delete(row)
    await db.flush()
    return {"deleted": True, "affected_deployments": affected}


@router.get("/storages/{storage_id}/bindings/status")
async def binding_status(
    storage_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Live PVC phase per pvc binding (one lookup per cluster × namespace); nfs → null."""
    row = await _storage_or_404(db, storage_id)
    bindings = (
        await db.execute(select(CustomStorageBinding).where(CustomStorageBinding.storage_id == row.id))
    ).scalars().all()
    groups: dict[tuple, dict | None] = {}
    out: dict[str, str | None] = {}
    for b in bindings:
        if b.type != "pvc":
            out[str(b.id)] = None
            continue
        key = (b.cluster_id, b.namespace)
        if key not in groups:
            groups[key] = await pvc_phases(db, b.cluster_id, b.namespace)
        phases = groups[key]
        out[str(b.id)] = "error" if phases is None else phases.get(b.claim_name, "missing")
    return {"status": out}


@router.get("/k8s-clusters/{cluster_id}/pvcs")
async def list_cluster_pvcs(
    cluster_id: str,
    namespace: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """PVCs of one namespace for the binding dialog; failures come back in ``error``."""
    cid = _cid(cluster_id)
    try:
        pvcs = await list_pvcs_timed(db, cid, namespace)
    except TimeoutError:
        return {"pvcs": [], "error": f"PVC lookup timed out after {PVC_LOOKUP_TIMEOUT:g}s"}
    except Exception as e:  # noqa: BLE001 - the dialog falls back to free text
        return {"pvcs": [], "error": str(e) or type(e).__name__}
    return {"pvcs": pvcs, "error": None}
