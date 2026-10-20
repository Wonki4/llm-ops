# backend/app/services/storage_store.py
"""DB side of storages: resolve a list for a deploy target, detect stale
deployments, find usages, the benchmark default mount, importer matching and
the bounded live PVC lookup."""

from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.custom_k8s_cluster import CustomK8sCluster
from app.db.models.custom_model_deployment import CustomModelDeployment
from app.db.models.custom_serving_recipe import CustomServingRecipe
from app.db.models.custom_storage import CustomStorage, CustomStorageBinding
from app.services.clusters import k8s_for_cluster
from app.services.pd_serving import ROLES
from app.services.storage_mounts import (
    LEGACY_VOLUME_NAME,
    all_entries,
    check_volume_names,
    default_volume_name,
    entry_storage_ids,
    nfs_override_volumes,
    pick_binding,
    snapshot,
    stale_entries,
    to_volumes,
    validate_entries,
)

logger = logging.getLogger(__name__)

PVC_LOOKUP_TIMEOUT = 5.0


class UnknownStorageError(ValueError):
    def __init__(self, ids: list[str]):
        self.ids = ids
        super().__init__(f"unknown storage: {', '.join(ids)}")


class UnresolvedStorageError(ValueError):
    def __init__(self, names: list[str]):
        self.names = names
        super().__init__(
            f"no binding for storage {', '.join(names)} on this cluster/namespace; add one under Admin → Storage"
        )


def null_safe_eq(column, value):
    """``column = value``, or ``column IS NULL`` when ``value`` is None (NULL cluster = portal default)."""
    return column.is_(None) if value is None else column == value


async def load_storages(db: AsyncSession, ids: list[str]) -> dict[str, CustomStorage]:
    if not ids:
        return {}
    stmt = select(CustomStorage).where(CustomStorage.id.in_([uuid.UUID(i) for i in ids]))
    return {str(r.id): r for r in (await db.execute(stmt)).scalars().all()}


async def bindings_for(db: AsyncSession, storage_ids: list[str], cluster_id: uuid.UUID | None) -> list:
    if not storage_ids:
        return []
    stmt = select(CustomStorageBinding).where(
        CustomStorageBinding.storage_id.in_([uuid.UUID(i) for i in storage_ids]),
        null_safe_eq(CustomStorageBinding.cluster_id, cluster_id),
    )
    return list((await db.execute(stmt)).scalars().all())


async def check_known(db: AsyncSession, entries: list[dict]) -> None:
    ids = [e["storage_id"] for e in entries]
    known = await load_storages(db, ids)
    missing = [i for i in ids if i not in known]
    if missing:
        raise UnknownStorageError(missing)


async def resolve_entries(
    db: AsyncSession,
    entries: list | None,
    cluster_id: uuid.UUID | None,
    namespace: str,
    *,
    previous: list | None = None,
) -> list[dict]:
    """Validate ``entries`` and attach ``volume_name`` + ``resolved`` for the target.

    ``previous`` (the deployment's current list) keeps an existing volume name per
    storage — migrated entries stay ``model-weights`` so manifests don't change.
    Raises ``UnknownStorageError`` / ``UnresolvedStorageError``, or a plain
    ``ValueError`` for an invalid list or a reserved/duplicate volume name.
    """
    clean = validate_entries(entries)
    if not clean:
        return []
    ids = [e["storage_id"] for e in clean]
    storages = await load_storages(db, ids)
    missing = [i for i in ids if i not in storages]
    if missing:
        raise UnknownStorageError(missing)
    bindings = await bindings_for(db, ids, cluster_id)
    kept = {str(p.get("storage_id")): p.get("volume_name") for p in previous or [] if p.get("volume_name")}
    out: list[dict] = []
    unresolved: list[str] = []
    for e in clean:
        b = pick_binding(bindings, e["storage_id"], cluster_id, namespace)
        if b is None:
            unresolved.append(storages[e["storage_id"]].name)
            continue
        name = kept.get(e["storage_id"]) or default_volume_name(storages[e["storage_id"]].name)
        out.append({**e, "volume_name": name, "resolved": snapshot(b)})
    if unresolved:
        raise UnresolvedStorageError(unresolved)
    check_volume_names(out)
    return out


async def resolve_pool_storages(
    db: AsyncSession,
    pd_config: dict | None,
    cluster_id: uuid.UUID | None,
    namespace: str,
    *,
    previous: dict | None = None,
) -> dict | None:
    """Resolve each P/D pool's own ``storages`` override (pools without one inherit the base list)."""
    if pd_config is None:
        return None
    cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in pd_config.items()}
    for role in ROLES:
        pool = cfg.get(role)
        if not isinstance(pool, dict) or pool.get("storages") is None:
            continue
        prev = ((previous or {}).get(role) or {}).get("storages")
        pool["storages"] = await resolve_entries(db, pool["storages"], cluster_id, namespace, previous=prev)
    return cfg


async def stale_storage_ids(db: AsyncSession, dep) -> list[str]:
    """Storages whose current resolution differs from the deployment's snapshot."""
    entries = all_entries(dep)
    if not entries:
        return []
    ids = sorted({str(e["storage_id"]) for e in entries})
    bindings = await bindings_for(db, ids, dep.cluster_id)
    return stale_entries(entries, bindings, dep.cluster_id, dep.namespace)


async def usage_index(db: AsyncSession) -> dict[str, dict]:
    """Who references each storage (recipes, deployments incl. pools, cluster bench defaults).

    Deployment items carry ``binding_ids`` (bindings their snapshots point at) and
    ``cluster_id`` / ``namespace`` / ``entries`` so callers can compute staleness.
    """
    index: dict[str, dict] = {}

    def slot(sid: str) -> dict:
        return index.setdefault(sid, {"recipes": [], "deployments": [], "clusters": []})

    for r in (await db.execute(select(CustomServingRecipe))).scalars().all():
        for sid in entry_storage_ids(r):
            slot(sid)["recipes"].append({"id": str(r.id), "name": r.name})
    for d in (await db.execute(select(CustomModelDeployment))).scalars().all():
        per_storage: dict[str, list[dict]] = {}
        for e in all_entries(d):
            per_storage.setdefault(str(e["storage_id"]), []).append(e)
        for sid, entries in per_storage.items():
            slot(sid)["deployments"].append(
                {
                    "id": str(d.id),
                    "model_name": d.model_name,
                    "binding_ids": {str((e.get("resolved") or {}).get("binding_id")) for e in entries},
                    "cluster_id": d.cluster_id,
                    "namespace": d.namespace,
                    "entries": entries,
                }
            )
    for c in (await db.execute(select(CustomK8sCluster))).scalars().all():
        if c.bench_storage_id:
            slot(str(c.bench_storage_id))["clusters"].append({"id": str(c.id), "name": c.name})
    return index


async def bench_volumes(
    db: AsyncSession, cluster_id: uuid.UUID | None, namespace: str, params: dict | None
) -> tuple[list, list]:
    """Weights mount for a benchmark against a raw model_name.

    Run override (``params.nfs_*``) → the cluster's bench storage resolved for
    ``namespace`` (read-only, ``model-weights``) → nothing.
    """
    override = nfs_override_volumes(params)
    if override is not None:
        return override
    if cluster_id is None:
        return [], []
    cluster = (
        await db.execute(select(CustomK8sCluster).where(CustomK8sCluster.id == cluster_id))
    ).scalar_one_or_none()
    if cluster is None or not cluster.bench_storage_id or not cluster.bench_mount_path:
        return [], []
    entry = {"storage_id": str(cluster.bench_storage_id), "mount_path": cluster.bench_mount_path, "read_only": True}
    try:
        resolved = await resolve_entries(
            db, [entry], cluster_id, namespace, previous=[{**entry, "volume_name": LEGACY_VOLUME_NAME}]
        )
    except ValueError as e:  # unresolved / unknown storage: run without the mount
        logger.warning("bench storage for cluster %s not mounted: %s", cluster_id, e)
        return [], []
    return to_volumes(resolved, force_read_only=True)


async def match_claims(
    db: AsyncSession, cluster_id: uuid.UUID | None, namespace: str, claims: list[str]
) -> dict[str, str]:
    """claim name → storage id, for PVC bindings of this cluster/namespace (recipe import)."""
    if not claims:
        return {}
    stmt = select(CustomStorageBinding).where(
        CustomStorageBinding.type == "pvc",
        CustomStorageBinding.namespace == namespace,
        CustomStorageBinding.claim_name.in_(claims),
        null_safe_eq(CustomStorageBinding.cluster_id, cluster_id),
    )
    return {b.claim_name: str(b.storage_id) for b in (await db.execute(stmt)).scalars().all()}


async def list_pvcs_timed(db: AsyncSession, cluster_id: uuid.UUID | None, namespace: str) -> list[dict]:
    """``K8sClient.list_pvcs`` for the cluster, bounded by ``PVC_LOOKUP_TIMEOUT`` (raises on failure)."""
    k8s = await k8s_for_cluster(db, cluster_id)
    return await asyncio.wait_for(k8s.list_pvcs(namespace), timeout=PVC_LOOKUP_TIMEOUT)


async def pvc_phases(db: AsyncSession, cluster_id: uuid.UUID | None, namespace: str) -> dict[str, str] | None:
    """claim → phase for one namespace, or None when the cluster can't be read (best-effort)."""
    try:
        pvcs = await list_pvcs_timed(db, cluster_id, namespace)
    except Exception as e:  # noqa: BLE001 - status/warnings only
        logger.warning("PVC lookup failed for cluster %s/%s: %s", cluster_id, namespace, e)
        return None
    return {p["name"]: p.get("phase") or "Pending" for p in pvcs}
