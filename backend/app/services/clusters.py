"""Resolve a K8sClient for a registered cluster (or the portal default)."""

import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.k8s import K8sClient
from app.db.models.custom_k8s_cluster import CustomK8sCluster
from app.db.session import async_session_factory
from app.services import crypto
from app.services.oidc_kubeconfig import oidc_provider_config, prepare_oidc_kubeconfig

LOCAL_DEST_SERVER = "https://kubernetes.default.svc"
_DNS_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")


def validate_namespace(value: str | None) -> str | None:
    """Blank = None (the cluster default applies); otherwise a K8s namespace name (RFC 1123 label)."""
    value = (value or "").strip()
    if not value:
        return None
    if not _DNS_LABEL.match(value):
        raise ValueError(
            f"namespace '{value}' is not a valid Kubernetes name: lowercase letters, digits and '-', "
            "starting and ending with a letter or digit, at most 63 characters"
        )
    return value


async def _cluster_row(db: AsyncSession, cluster_id: uuid.UUID) -> CustomK8sCluster | None:
    return (
        await db.execute(select(CustomK8sCluster).where(CustomK8sCluster.id == cluster_id))
    ).scalar_one_or_none()


async def _registered_oidc_kubeconfig(cluster_id: uuid.UUID) -> tuple[dict, str]:
    """Refresh a stored OIDC token once across replicas and save any rotation."""
    async with async_session_factory() as db:
        result = await db.execute(
            select(CustomK8sCluster).where(CustomK8sCluster.id == cluster_id).with_for_update()
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise ValueError(f"Registered cluster {cluster_id} no longer exists")
        raw = yaml.safe_load(crypto.decrypt(row.kubeconfig_encrypted))
        client_config, refreshed = await prepare_oidc_kubeconfig(raw, row.context, allow_refresh=True)
        if refreshed:
            row.kubeconfig_encrypted = crypto.encrypt(yaml.safe_dump(raw, sort_keys=False))
            await db.commit()
        return client_config, row.context


def _client_for_row(row: CustomK8sCluster) -> K8sClient:
    kubeconfig = yaml.safe_load(crypto.decrypt(row.kubeconfig_encrypted))
    if oidc_provider_config(kubeconfig, row.context) is not None:
        async def load() -> tuple[dict, str]:
            return await _registered_oidc_kubeconfig(row.id)

        return K8sClient(kubeconfig_loader=load)
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


async def default_namespace(db: AsyncSession, cluster_id: uuid.UUID | str | None) -> str:
    """The namespace a deploy uses when none is given: the cluster's own, else the portal setting."""
    from app.config import settings

    if cluster_id:
        cid = cluster_id if isinstance(cluster_id, uuid.UUID) else uuid.UUID(str(cluster_id))
        row = await _cluster_row(db, cid)
        if row is not None and row.namespace:
            return row.namespace
    return settings.k8s_default_namespace


# (cluster_id, cluster name, client, namespace): one namespaced LIST of an external-serving scan.
ScanScope = tuple[str | None, str, K8sClient, str]


@dataclass
class NamespaceScope:
    cluster: CustomK8sCluster | None
    namespace: str

    @property
    def cluster_id(self) -> str | None:
        return str(self.cluster.id) if self.cluster else None

    @property
    def name(self) -> str:
        return self.cluster.name if self.cluster else "default"

    def client(self) -> K8sClient:
        return _client_for_row(self.cluster) if self.cluster else K8sClient()


async def namespace_scopes(
    db: AsyncSession,
    extra: Iterable[tuple[uuid.UUID | str | None, str | None]] = (),
    *,
    representative: bool = True,
    include_registered: bool = False,
) -> list[NamespaceScope]:
    """Where an external-serving scan looks, instead of every namespace of every cluster.

    The representative cluster (``is_default``) in its own namespace, or the portal
    kubeconfig in ``k8s_default_namespace`` when none is marked; plus each
    ``(cluster_id, namespace)`` in ``extra`` (registrations, stacks…). Deduplicated;
    an extra on a cluster that no longer exists, or without a namespace, is skipped.
    """
    from app.config import settings

    rows = {str(r.id): r for r in (await db.execute(select(CustomK8sCluster))).scalars().all()}
    scopes: dict[tuple[str | None, str], NamespaceScope] = {}

    def add(cid: str | None, namespace: str) -> None:
        scopes[cid, namespace] = NamespaceScope(rows.get(cid), namespace)

    if representative:
        rep = next((r for r in rows.values() if r.is_default), None)
        if rep is not None:
            add(str(rep.id), rep.namespace or settings.k8s_default_namespace)
        else:
            add(None, settings.k8s_default_namespace)
    if include_registered:
        for cid, row in rows.items():
            add(cid, row.namespace or settings.k8s_default_namespace)
    for cluster_id, namespace in extra:
        cid = str(cluster_id) if cluster_id else None
        if namespace and (cid is None or cid in rows):
            add(cid, namespace)
    return list(scopes.values())


async def scan_scopes(
    db: AsyncSession,
    extra: Iterable[tuple[uuid.UUID | str | None, str | None]] = (),
    *,
    representative: bool = True,
) -> list[ScanScope]:
    clients: dict[str | None, K8sClient] = {}
    out = []
    for scope in await namespace_scopes(db, extra, representative=representative):
        cid = scope.cluster_id
        if cid not in clients:
            clients[cid] = scope.client()
        out.append((cid, scope.name, clients[cid], scope.namespace))
    return out
