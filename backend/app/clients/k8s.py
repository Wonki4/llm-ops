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


def _quantity_int(value) -> int:
    """Kubernetes quantity → int for whole-unit extended resources ("8", "2k")."""
    if value is None:
        return 0
    text = str(value).strip()
    try:
        return int(text)
    except ValueError:
        pass
    mult = {"k": 10**3, "M": 10**6, "G": 10**9, "Ki": 1024, "Mi": 1024**2, "Gi": 1024**3}
    for suffix, m in sorted(mult.items(), key=lambda kv: -len(kv[0])):
        if text.endswith(suffix):
            try:
                return int(float(text[: -len(suffix)]) * m)
            except ValueError:
                return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


class K8sClient:
    """Thin wrapper that opens a fresh ApiClient per call.

    By default it loads the portal's mounted kubeconfig (``APP_KUBECONFIG_PATH``).
    Pass an in-memory kubeconfig dict + context to target a specific registered
    cluster instead — see ``app.services.clusters.k8s_for_cluster``.

    kubernetes-asyncio shares a session per ApiClient; reusing across requests
    is fragile inside FastAPI's event loop, so we open and close on each
    operation. Throughput is fine for the deployment-management cadence
    (rare admin actions + ~60s reconciler).
    """

    def __init__(self, kubeconfig: dict | None = None, context: str | None = None) -> None:
        self._kubeconfig = kubeconfig
        self._context = context

    async def _api_client(self) -> client.ApiClient:
        if self._kubeconfig is not None:
            cfg = client.Configuration()
            await config.load_kube_config_from_dict(
                config_dict=self._kubeconfig, context=self._context, client_configuration=cfg
            )
            return client.ApiClient(configuration=cfg)
        if not settings.kubeconfig_path:
            raise K8sNotConfigured("APP_KUBECONFIG_PATH is empty; K8s features disabled")
        await config.load_kube_config(config_file=settings.kubeconfig_path)
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
        """Delete a deployment's resources. ``names`` maps ``<prefix>deployment`` /
        ``<prefix>service`` / ``<prefix>ingress`` keys to object names (the
        aggregated trio or the P/D ``prefill_*``/``decode_*`` quartet).
        Ingresses go first, Deployments last; 404s are ignored."""
        api_client = await self._api_client()
        try:
            apps = client.AppsV1Api(api_client)
            core = client.CoreV1Api(api_client)
            net = client.NetworkingV1Api(api_client)
            ordered: list[tuple[str, str]] = []
            for kind, suffix in (("Ingress", "ingress"), ("Service", "service"), ("Deployment", "deployment")):
                ordered += [(kind, n) for key, n in names.items() if key.endswith(suffix) and n]
            for kind, n in ordered:
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

    async def read_deployment(self, namespace: str, name: str) -> dict | None:
        """Live Deployment spec, sanitized for the external clone-bench builder.

        First container only (vLLM/SGLang servers are single-container); None on 404.
        """
        api_client = await self._api_client()
        try:
            apps = client.AppsV1Api(api_client)
            try:
                dep = await apps.read_namespaced_deployment(name=name, namespace=namespace)
            except ApiException as e:
                if e.status == 404:
                    return None
                raise
            pod = dep.spec.template.spec
            c = pod.containers[0]
            sanitize = api_client.sanitize_for_serialization
            return {
                "name": dep.metadata.name,
                "namespace": dep.metadata.namespace,
                "labels": dict(dep.metadata.labels or {}),
                "replicas": int(dep.spec.replicas or 0),
                "container": {
                    "name": c.name,
                    "image": c.image,
                    "command": list(c.command or []),
                    "args": list(c.args or []),
                    "env": [
                        {"name": e.name, "value": e.value} for e in (c.env or [])
                    ],
                    "env_raw": sanitize(c.env) or [],
                    "env_from": sanitize(c.env_from) or [],
                    "resources": sanitize(c.resources) or {},
                    "ports": sanitize(c.ports) or [],
                    "volume_mounts": sanitize(c.volume_mounts) or [],
                    "readiness_probe": sanitize(c.readiness_probe) or None,
                    "liveness_probe": sanitize(c.liveness_probe) or None,
                    "startup_probe": sanitize(c.startup_probe) or None,
                },
                "volumes": sanitize(pod.volumes) or [],
                "node_selector": dict(pod.node_selector or {}) or None,
                "tolerations": sanitize(pod.tolerations) or None,
                # Facts the recipe importer can only warn about (not clonable).
                "containers_count": len(pod.containers or []),
                "init_containers": [ic.name for ic in (pod.init_containers or [])],
                "affinity": sanitize(pod.affinity) or None,
            }
        finally:
            await api_client.close()

    async def list_gpu_nodes(self, label_key: str, resource_keys: list[str]) -> list[dict]:
        """Nodes that expose any of ``resource_keys``, with the GPU-type label value,
        allocatable counts and the sum of requests from non-terminal pods.

        Read-only (one LIST nodes + one LIST pods). Used by the GPU profile
        discovery endpoint; a node without the label reports ``label_value``
        None so the admin can see what is still unlabelled.
        """
        api_client = await self._api_client()
        try:
            core = client.CoreV1Api(api_client)
            nodes = (await core.list_node()).items
            pods = (await core.list_pod_for_all_namespaces()).items
            requested: dict[str, dict[str, int]] = {}
            for pod in pods:
                node_name = pod.spec.node_name
                if not node_name or (pod.status.phase or "") in ("Succeeded", "Failed"):
                    continue
                bucket = requested.setdefault(node_name, {k: 0 for k in resource_keys})
                for c in pod.spec.containers or []:
                    reqs = (c.resources.requests if c.resources else None) or {}
                    for key in resource_keys:
                        bucket[key] += _quantity_int(reqs.get(key))
            out: list[dict] = []
            for n in nodes:
                allocatable = dict(n.status.allocatable or {})
                if not any(key in allocatable for key in resource_keys):
                    continue
                labels = dict(n.metadata.labels or {})
                out.append(
                    {
                        "name": n.metadata.name,
                        "label_value": labels.get(label_key),
                        "schedulable": not bool(n.spec.unschedulable),
                        "allocatable": {key: _quantity_int(allocatable.get(key)) for key in resource_keys},
                        "requested": requested.get(n.metadata.name, {k: 0 for k in resource_keys}),
                    }
                )
            return out
        finally:
            await api_client.close()

    # ─── ArgoCD Applications (custom resource) ──────────────────────

    _ARGO = dict(group="argoproj.io", version="v1alpha1", plural="applications")

    async def apply_application(self, namespace: str, manifest: dict) -> None:
        """Create-or-patch an argoproj.io Application in ``namespace``.

        Read then create (absent) or merge-patch (present) — mirrors
        create_or_patch for built-in kinds. ArgoCD's controller reconciles it.
        """
        api_client = await self._api_client()
        try:
            co = client.CustomObjectsApi(api_client)
            name = manifest["metadata"]["name"]
            try:
                await co.get_namespaced_custom_object(**self._ARGO, namespace=namespace, name=name)
                exists = True
            except ApiException as e:
                if e.status == 404:
                    exists = False
                else:
                    raise
            if exists:
                await co.patch_namespaced_custom_object(
                    **self._ARGO, namespace=namespace, name=name, body=manifest,
                    _content_type="application/merge-patch+json",
                )
            else:
                await co.create_namespaced_custom_object(**self._ARGO, namespace=namespace, body=manifest)
        finally:
            await api_client.close()

    async def get_application(self, namespace: str, name: str) -> dict | None:
        """Read an Application CR; None if it does not exist."""
        api_client = await self._api_client()
        try:
            co = client.CustomObjectsApi(api_client)
            try:
                return await co.get_namespaced_custom_object(**self._ARGO, namespace=namespace, name=name)
            except ApiException as e:
                if e.status == 404:
                    return None
                raise
        finally:
            await api_client.close()

    async def delete_application(self, namespace: str, name: str) -> None:
        """Delete an Application (cascades to its workloads); 404 swallowed."""
        api_client = await self._api_client()
        try:
            co = client.CustomObjectsApi(api_client)
            try:
                await co.delete_namespaced_custom_object(
                    **self._ARGO, namespace=namespace, name=name, propagation_policy="Foreground"
                )
            except ApiException as e:
                if e.status != 404:
                    raise
        finally:
            await api_client.close()

    async def list_deployments_all(self) -> list[dict]:
        """List Deployments across all namespaces, shaped for discovery.

        One cluster-wide LIST call. Used by the external-serving discovery
        endpoint; the RBAC ClusterRole already grants deployments list.
        """
        api_client = await self._api_client()
        try:
            apps = client.AppsV1Api(api_client)
            result = await apps.list_deployment_for_all_namespaces()
            items: list[dict] = []
            for dep in result.items:
                containers = dep.spec.template.spec.containers or []
                created = dep.metadata.creation_timestamp
                items.append(
                    {
                        "name": dep.metadata.name,
                        "namespace": dep.metadata.namespace,
                        "labels": dict(dep.metadata.labels or {}),
                        "created_at": created.isoformat() if created else None,
                        "containers": [{"image": c.image, "args": list(c.args or [])} for c in containers],
                        "replicas": int(dep.spec.replicas or 0),
                        "ready": int(dep.status.ready_replicas or 0),
                        "available": int(dep.status.available_replicas or 0),
                        "conditions": [
                            {"type": c.type, "status": c.status, "reason": c.reason, "message": c.message}
                            for c in (dep.status.conditions or [])
                        ],
                    }
                )
            return items
        finally:
            await api_client.close()

    # ─── Batch v1 (Jobs) — used by benchmark runner ─────────────────

    async def create_job(self, namespace: str, manifest: dict) -> None:
        """Create a K8s Job. Raises ApiException(409) if name collides."""
        api_client = await self._api_client()
        try:
            batch = client.BatchV1Api(api_client)
            await batch.create_namespaced_job(namespace=namespace, body=manifest)
        finally:
            await api_client.close()

    async def read_job_status(self, namespace: str, name: str) -> dict[str, Any]:
        """Return {'active': int, 'succeeded': int, 'failed': int, 'conditions': [...]}.

        Raises ApiException(404) when the Job is gone.
        """
        api_client = await self._api_client()
        try:
            batch = client.BatchV1Api(api_client)
            job = await batch.read_namespaced_job_status(name=name, namespace=namespace)
            return {
                "active": int(job.status.active or 0),
                "succeeded": int(job.status.succeeded or 0),
                "failed": int(job.status.failed or 0),
                "conditions": [
                    {"type": c.type, "status": c.status, "reason": c.reason, "message": c.message}
                    for c in (job.status.conditions or [])
                ],
            }
        finally:
            await api_client.close()

    async def read_job_pod_logs(self, namespace: str, job_name: str, tail_lines: int = 2000) -> str:
        """Concatenate logs from all pods owned by the Job.

        Used to harvest the runner's stdout JSON result. Returns empty string
        when no pods are found (e.g. Job spawned but pod not yet scheduled).
        """
        api_client = await self._api_client()
        try:
            core = client.CoreV1Api(api_client)
            pods = await core.list_namespaced_pod(
                namespace=namespace, label_selector=f"job-name={job_name}"
            )
            buf: list[str] = []
            for pod in pods.items:
                try:
                    log = await core.read_namespaced_pod_log(
                        name=pod.metadata.name,
                        namespace=namespace,
                        tail_lines=tail_lines,
                    )
                    buf.append(log or "")
                except ApiException:
                    continue
            return "\n".join(buf)
        finally:
            await api_client.close()

    async def delete_job(self, namespace: str, name: str) -> None:
        """Delete the Job and its pods. 404 swallowed (idempotent)."""
        api_client = await self._api_client()
        try:
            batch = client.BatchV1Api(api_client)
            try:
                await batch.delete_namespaced_job(
                    name=name,
                    namespace=namespace,
                    propagation_policy="Background",
                )
            except ApiException as e:
                if e.status != 404:
                    raise
        finally:
            await api_client.close()


def get_k8s_client() -> K8sClient:
    return K8sClient()


async def probe_cluster(kubeconfig: dict, context: str) -> str:
    """Connect to a cluster using an in-memory kubeconfig dict + context.

    Returns the cluster's server version string (e.g. "v1.29.4"). Raises on
    connection / auth failure. Used by the clusters settings "test connection"
    action; does not touch the portal's own mounted kubeconfig.
    """
    cfg = client.Configuration()
    await config.load_kube_config_from_dict(
        config_dict=kubeconfig, context=context, client_configuration=cfg
    )
    api_client = client.ApiClient(configuration=cfg)
    try:
        info = await client.VersionApi(api_client).get_code()
        return info.git_version or "unknown"
    finally:
        await api_client.close()
