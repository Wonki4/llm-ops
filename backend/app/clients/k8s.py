"""Kubernetes client wrapper for model deployment management.

Same code path for local docker-compose (kubeconfig mounted via volume) and
in-cluster (Secret-mounted kubeconfig). When kubeconfig_path is empty the
client raises ApiException("not configured") so callers can surface a clean
error to the admin instead of crashing the request.
"""

import logging
from typing import Any

from kubernetes_asyncio import client, config
from kubernetes_asyncio.client.exceptions import ApiException

from app.config import settings

logger = logging.getLogger(__name__)


class K8sNotConfigured(RuntimeError):
    """Raised when the portal has no kubeconfig wired up."""


async def _api_client() -> client.ApiClient:
    if not settings.kubeconfig_path:
        raise K8sNotConfigured("APP_KUBECONFIG_PATH is empty; K8s features disabled")
    await config.load_kube_config(config_file=settings.kubeconfig_path)
    return client.ApiClient()


class K8sClient:
    """Thin wrapper that opens a fresh ApiClient per call.

    kubernetes-asyncio shares a session per ApiClient; reusing across requests
    is fragile inside FastAPI's event loop, so we open and close on each
    operation. Throughput is fine for the deployment-management cadence
    (rare admin actions + ~60s reconciler).
    """

    async def create_or_patch(self, namespace: str, manifests: list[dict]) -> None:
        """Apply a list of K8s manifests. Creates new resources, patches existing."""
        api_client = await _api_client()
        try:
            apps = client.AppsV1Api(api_client)
            core = client.CoreV1Api(api_client)
            net = client.NetworkingV1Api(api_client)
            for manifest in manifests:
                kind = manifest["kind"]
                name = manifest["metadata"]["name"]
                ns = manifest["metadata"].get("namespace", namespace)
                if kind == "Deployment":
                    await self._upsert(apps.read_namespaced_deployment, apps.create_namespaced_deployment, apps.patch_namespaced_deployment, name, ns, manifest)
                elif kind == "Service":
                    await self._upsert(core.read_namespaced_service, core.create_namespaced_service, core.patch_namespaced_service, name, ns, manifest)
                elif kind == "Ingress":
                    await self._upsert(net.read_namespaced_ingress, net.create_namespaced_ingress, net.patch_namespaced_ingress, name, ns, manifest)
                else:
                    raise ValueError(f"Unsupported kind: {kind}")
        finally:
            await api_client.close()

    @staticmethod
    async def _upsert(read_fn, create_fn, patch_fn, name: str, namespace: str, manifest: dict) -> None:
        try:
            await read_fn(name=name, namespace=namespace)
            exists = True
        except ApiException as e:
            if e.status == 404:
                exists = False
            else:
                raise
        if exists:
            await patch_fn(name=name, namespace=namespace, body=manifest)
        else:
            await create_fn(namespace=namespace, body=manifest)

    async def delete(self, namespace: str, names: dict[str, str]) -> None:
        """Delete the per-deployment trio. names = {'deployment': X, 'service': Y, 'ingress': Z}."""
        api_client = await _api_client()
        try:
            apps = client.AppsV1Api(api_client)
            core = client.CoreV1Api(api_client)
            net = client.NetworkingV1Api(api_client)
            for kind, n in (("Ingress", names.get("ingress")), ("Service", names.get("service")), ("Deployment", names.get("deployment"))):
                if not n:
                    continue
                try:
                    if kind == "Deployment":
                        await apps.delete_namespaced_deployment(name=n, namespace=namespace)
                    elif kind == "Service":
                        await core.delete_namespaced_service(name=n, namespace=namespace)
                    elif kind == "Ingress":
                        await net.delete_namespaced_ingress(name=n, namespace=namespace)
                except ApiException as e:
                    if e.status != 404:
                        raise
        finally:
            await api_client.close()

    async def read_deployment_status(self, namespace: str, name: str) -> dict[str, Any]:
        """Return {'ready': int, 'desired': int, 'available': int, 'conditions': [...]}.

        Raises ApiException(404) when the deployment doesn't exist.
        """
        api_client = await _api_client()
        try:
            apps = client.AppsV1Api(api_client)
            dep = await apps.read_namespaced_deployment_status(name=name, namespace=namespace)
            return {
                "ready": int(dep.status.ready_replicas or 0),
                "desired": int(dep.spec.replicas or 0),
                "available": int(dep.status.available_replicas or 0),
                "conditions": [{"type": c.type, "status": c.status, "reason": c.reason, "message": c.message} for c in (dep.status.conditions or [])],
            }
        finally:
            await api_client.close()

    async def read_service_cluster_ip(self, namespace: str, name: str) -> str | None:
        api_client = await _api_client()
        try:
            core = client.CoreV1Api(api_client)
            try:
                svc = await core.read_namespaced_service(name=name, namespace=namespace)
                return svc.spec.cluster_ip
            except ApiException as e:
                if e.status == 404:
                    return None
                raise
        finally:
            await api_client.close()


def get_k8s_client() -> K8sClient:
    return K8sClient()
