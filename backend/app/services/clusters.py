"""Resolve a K8sClient for a registered cluster (or the portal default)."""

import uuid

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.k8s import K8sClient
from app.db.models.custom_k8s_cluster import CustomK8sCluster
from app.services import crypto

LOCAL_DEST_SERVER = "https://kubernetes.default.svc"


async def _cluster_row(db: AsyncSession, cluster_id: uuid.UUID) -> CustomK8sCluster | None:
    return (
        await db.execute(select(CustomK8sCluster).where(CustomK8sCluster.id == cluster_id))
    ).scalar_one_or_none()


def _client_for_row(row: CustomK8sCluster) -> K8sClient:
    kubeconfig = yaml.safe_load(crypto.decrypt(row.kubeconfig_encrypted))
    return K8sClient(kubeconfig=kubeconfig, context=row.context)


async def k8s_for_cluster(db: AsyncSession, cluster_id: uuid.UUID | str | None) -> K8sClient:
    """Return a K8sClient bound to the registered cluster.

    ``cluster_id`` of None uses the portal's mounted kubeconfig (the default,
    backward-compatible behaviour). A cluster_id that no longer resolves also
    falls back to the default rather than failing the reconciler.
    """
    if not cluster_id:
        return K8sClient()
    cid = cluster_id if isinstance(cluster_id, uuid.UUID) else uuid.UUID(str(cluster_id))
    row = await _cluster_row(db, cid)
    if row is None:
        return K8sClient()
    return _client_for_row(row)


async def argocd_namespace_for(db: AsyncSession, cluster_id: uuid.UUID | str | None) -> str:
    """The ArgoCD control-plane namespace for a stack's cluster.

    A registered cluster's ``argocd_namespace`` wins; a null cluster (portal
    default kubeconfig) falls back to the global ``settings.argocd_namespace``.
    """
    from app.config import settings

    if not cluster_id:
        return settings.argocd_namespace
    cid = cluster_id if isinstance(cluster_id, uuid.UUID) else uuid.UUID(str(cluster_id))
    row = await _cluster_row(db, cid)
    return (row.argocd_namespace if row and row.argocd_namespace else settings.argocd_namespace)


async def argocd_placement_for(
    db: AsyncSession, cluster_id: uuid.UUID | str | None
) -> tuple[K8sClient, str, str]:
    """Where a stack's Application CR goes and what its destination points at.

    Returns (K8s client to apply the CR with, ArgoCD control-plane namespace,
    ``spec.destination.server``). The target cluster's ``argocd_host_cluster_id``
    names the cluster whose ArgoCD manages it (one hop only; NULL = itself),
    and ``argocd_dest_server`` is the server URL that ArgoCD registers the
    target under (NULL = the in-cluster default). A null or unresolvable
    cluster keeps the portal-default, all-local behaviour; a dangling host id
    falls back to the target itself.
    """
    from app.config import settings

    if not cluster_id:
        return K8sClient(), settings.argocd_namespace, LOCAL_DEST_SERVER
    cid = cluster_id if isinstance(cluster_id, uuid.UUID) else uuid.UUID(str(cluster_id))
    target = await _cluster_row(db, cid)
    if target is None:
        return K8sClient(), settings.argocd_namespace, LOCAL_DEST_SERVER
    dest = target.argocd_dest_server or LOCAL_DEST_SERVER
    host = target
    if target.argocd_host_cluster_id:
        host = await _cluster_row(db, target.argocd_host_cluster_id) or target
    ns = host.argocd_namespace or settings.argocd_namespace
    return _client_for_row(host), ns, dest
