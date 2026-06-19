"""Resolve a K8sClient for a registered cluster (or the portal default)."""

import uuid

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.k8s import K8sClient
from app.db.models.custom_k8s_cluster import CustomK8sCluster
from app.services import crypto


async def k8s_for_cluster(db: AsyncSession, cluster_id: uuid.UUID | str | None) -> K8sClient:
    """Return a K8sClient bound to the registered cluster.

    ``cluster_id`` of None uses the portal's mounted kubeconfig (the default,
    backward-compatible behaviour). A cluster_id that no longer resolves also
    falls back to the default rather than failing the reconciler.
    """
    if not cluster_id:
        return K8sClient()
    cid = cluster_id if isinstance(cluster_id, uuid.UUID) else uuid.UUID(str(cluster_id))
    row = (
        await db.execute(select(CustomK8sCluster).where(CustomK8sCluster.id == cid))
    ).scalar_one_or_none()
    if row is None:
        return K8sClient()
    kubeconfig = yaml.safe_load(crypto.decrypt(row.kubeconfig_encrypted))
    return K8sClient(kubeconfig=kubeconfig, context=row.context)
