"""Kubernetes client wrapper for model deployment management.

A `K8sClient` instance is bound to a single cluster's kubeconfig content
(stored in custom_k8s_cluster.kubeconfig_content). Pass the raw kubeconfig
YAML string in, and the client loads it via kubernetes_asyncio's
load_kube_config_from_dict (parsing the YAML ourselves so we don't depend on
the file-path-only variant).

Each operation opens and closes its own ApiClient — kubernetes-asyncio
shares HTTP state per ApiClient and reusing it across FastAPI requests is
fragile inside the event loop. Throughput is fine for deployment-management
cadence (rare admin actions + ~60s reconciler).
"""

import logging
from typing import Any

import yaml
from kubernetes_asyncio import client, config
from kubernetes_asyncio.client.exceptions import ApiException

logger = logging.getLogger(__name__)


class K8sNotConfigured(RuntimeError):
    """Raised when no usable kubeconfig was provided for this cluster."""


class K8sClient:
    """Cluster-scoped Kubernetes client. Cheap to construct.

    Pass `kubeconfig_content` from `custom_k8s_cluster.kubeconfig_content`.
    Constructing the client doesn't touch the network; failures are deferred
    to the first ApiClient open.
    """

    def __init__(self, kubeconfig_content: str | None) -> None:
        self._kubeconfig_content = (kubeconfig_content or "").strip()

    async def _api_client(self) -> client.ApiClient:
        if not self._kubeconfig_content:
            raise K8sNotConfigured(
                "Cluster kubeconfig is empty; edit it in 포털 설정 → 클러스터 관리"
            )
        try:
            cfg_dict = yaml.safe_load(self._kubeconfig_content)
        except yaml.YAMLError as e:
            raise K8sNotConfigured(f"Invalid kubeconfig YAML: {e}") from e
        if not isinstance(cfg_dict, dict):
            raise K8sNotConfigured("kubeconfig content must be a YAML mapping")
        await config.load_kube_config_from_dict(cfg_dict)
        return client.ApiClient()

    async def create_or_patch(self, namespace: str, manifests: list[dict]) -> None:
        """Apply a list of K8s manifests. Creates new resources, patches existing."""
        api_client = await self._api_client()
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
        api_client = await self._api_client()
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
        api_client = await self._api_client()
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
        api_client = await self._api_client()
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

    async def ping(self) -> dict:
        """Connectivity check used by the cluster registry UI. Returns API version."""
        api_client = await self._api_client()
        try:
            v = client.VersionApi(api_client)
            info = await v.get_code()
            return {"git_version": info.git_version, "platform": info.platform}
        finally:
            await api_client.close()


def get_k8s_client_for(kubeconfig_content: str | None) -> K8sClient:
    """Build a per-cluster K8sClient from raw kubeconfig YAML."""
    return K8sClient(kubeconfig_content)
