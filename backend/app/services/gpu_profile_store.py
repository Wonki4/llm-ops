"""DB lookups for GPU profiles shared by the deployment, benchmark and import APIs."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.custom_gpu_profile import CustomGpuProfile
from app.db.models.custom_k8s_cluster import CustomK8sCluster
from app.services.gpu_profiles import DEFAULT_GPU_LABEL_KEY


def parse_cluster_id(raw: str | uuid.UUID | None) -> uuid.UUID | None:
    """``None`` / ``""`` / ``"default"`` → None (portal default cluster); else a UUID.

    Raises ``ValueError`` on a malformed id.
    """
    if raw is None or raw == "" or raw == "default":
        return None
    return raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw))


async def cluster_label_key(db: AsyncSession, cluster_id: uuid.UUID | None) -> str:
    if cluster_id is None:
        return DEFAULT_GPU_LABEL_KEY
    row = (await db.execute(select(CustomK8sCluster).where(CustomK8sCluster.id == cluster_id))).scalar_one_or_none()
    return (row.gpu_label_key if row is not None and row.gpu_label_key else DEFAULT_GPU_LABEL_KEY)


async def list_profiles(db: AsyncSession, cluster_id: uuid.UUID | None, *, enabled_only: bool = False) -> list:
    stmt = select(CustomGpuProfile).where(CustomGpuProfile.cluster_id == cluster_id)
    if enabled_only:
        stmt = stmt.where(CustomGpuProfile.enabled.is_(True))
    return list((await db.execute(stmt.order_by(CustomGpuProfile.name))).scalars().all())


async def get_profile(db: AsyncSession, cluster_id: uuid.UUID | None, name: str) -> CustomGpuProfile | None:
    stmt = select(CustomGpuProfile).where(CustomGpuProfile.cluster_id == cluster_id, CustomGpuProfile.name == name)
    return (await db.execute(stmt)).scalar_one_or_none()


class UnknownGpuTypeError(ValueError):
    def __init__(self, name: str, known: list[str]):
        self.name = name
        self.known = known
        super().__init__(f"unknown gpu_type '{name}' for this cluster; known: {', '.join(known) or 'none'}")


async def resolve_gpu_type(db: AsyncSession, cluster_id: uuid.UUID | None, gpu_type: str | None):
    """``(profile | None, label_key)`` for a requested type name.

    * ``gpu_type`` empty → (None, label_key): explicit placement applies.
    * cluster has no profiles at all → (None, label_key): clusters without
      profiles keep working exactly as before, whatever the name says.
    * otherwise the enabled profile must exist → else ``UnknownGpuType``.
    """
    label_key = await cluster_label_key(db, cluster_id)
    if not gpu_type:
        return None, label_key
    profiles = await list_profiles(db, cluster_id, enabled_only=True)
    if not profiles:
        return None, label_key
    for p in profiles:
        if p.name == gpu_type:
            return p, label_key
    raise UnknownGpuTypeError(gpu_type, [p.name for p in profiles])
