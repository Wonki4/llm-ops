"""Build K8s Deployment + Service + Ingress manifests from a deployment row.

The portal only owns the metadata in custom_model_deployment; this module
turns that into the concrete K8s resources we apply via the K8s client.
Aggregated servings render one Deployment/Service/Ingress; prefill/decode
servings (``serving_mode == "pd"``) render two Deployments and two Services
(no Ingress: the llm-d router is the entry) via ``pd_serving``.
Pure functions, no side effects.
"""

from app.db.models.custom_model_deployment import CustomModelDeployment
from app.services import pd_serving
from app.services.serving_engines import SERVING_PORT, container_launch, engine_of
from app.services.serving_probes import STARTUP_DEFAULTS_PD, render_probes

VLLM_PORT = SERVING_PORT  # kept for existing imports
LABEL_OWNER = "llm-ops/managed-by"
LABEL_MODEL = "llm-ops/model-name"
# llm-d's standard model-server label; llm-d routers select servers by it.
LABEL_LLMD_MODEL = "llm-d.ai/model"


def serving_api_key(vllm_extra_args: list | None, env: dict | None) -> str:
    """The API key a client must present to this serving when auth is enabled.

    vLLM/SGLang OpenAI servers are open by default; auth is turned on with a
    ``--api-key <key>`` server arg or a ``VLLM_API_KEY`` / ``OPENAI_API_KEY`` env
    var. Returns that configured key so benchmark runners and LiteLLM
    registration authenticate correctly; ``"EMPTY"`` when no auth is set.
    """
    args = list(vllm_extra_args or [])
    for i, a in enumerate(args):
        if a == "--api-key" and i + 1 < len(args):
            return str(args[i + 1])
        if isinstance(a, str) and a.startswith("--api-key="):
            return a.split("=", 1)[1]
    env = env or {}
    for key in ("VLLM_API_KEY", "OPENAI_API_KEY"):
        if env.get(key):
            return str(env[key])
    return "EMPTY"


def k8s_resource_names(dep: CustomModelDeployment) -> dict[str, str]:
    """Stable resource names.

    Aggregated: ``<model>-deployment / -service / -ingress``. P/D:
    ``<model>-prefill-deployment, -decode-deployment, -prefill-service,
    -decode-service`` (keys ``<role>_deployment`` / ``<role>_service``). The
    K8s client deletes by key suffix, so both shapes work.
    """
    if pd_serving.is_pd(dep):
        return pd_serving.role_resource_names(dep)
    safe = dep.model_name.lower().replace("_", "-").replace(".", "-").replace("/", "-")
    return {
        "deployment": f"{safe}-deployment",
        "service": f"{safe}-service",
        "ingress": f"{safe}-ingress",
    }


def _labels(dep: CustomModelDeployment) -> dict[str, str]:
    """Selector labels: immutable once a Deployment exists, so never add here."""
    return {LABEL_OWNER: "litellm-portal", LABEL_MODEL: dep.model_name}


def pod_labels(dep: CustomModelDeployment) -> dict[str, str]:
    """Labels stamped on the serving pods: the selector labels plus llm-d's
    ``llm-d.ai/model`` so an llm-d router can target this deployment."""
    return {**_labels(dep), LABEL_LLMD_MODEL: dep.model_name}


def _resources(
    gpu_count: int | None, gpu_resource_key: str, dep: CustomModelDeployment, override: dict | None = None
) -> dict:
    # GPU optional — omit the resource entirely when gpu_count == 0 so the pod is
    # CPU-only and schedulable on nodes without GPUs. CPU/memory are optional too;
    # ``override`` (a P/D role's values) replaces the row's when set.
    ov = override or {}

    def pick(key: str):
        return ov.get(key) if ov.get(key) is not None else getattr(dep, key, None)

    requests: dict = {}
    limits: dict = {}
    if gpu_count and gpu_count > 0:
        limits[gpu_resource_key] = str(gpu_count)
    if pick("cpu_request"):
        requests["cpu"] = pick("cpu_request")
    if pick("cpu_limit"):
        limits["cpu"] = pick("cpu_limit")
    if pick("memory_request"):
        requests["memory"] = pick("memory_request")
    if pick("memory_limit"):
        limits["memory"] = pick("memory_limit")
    for key, qty in (_runtime(dep).get("extra_resources") or {}).items():
        limits[key] = str(qty)
        requests[key] = str(qty)
    resources: dict = {}
    if limits:
        resources["limits"] = limits
    if requests:
        resources["requests"] = requests
    return resources


def _runtime(dep) -> dict:
    return dict(getattr(dep, "runtime", None) or {})


def _volumes(dep: CustomModelDeployment) -> tuple[list, list]:
    """(volumes, volumeMounts): the model PVC when configured, ``/dev/shm`` when runtime asks."""
    volumes = []
    mounts = []
    if dep.pvc_name and dep.pvc_mount_path:
        volumes.append({"name": "model-weights", "persistentVolumeClaim": {"claimName": dep.pvc_name}})
        mounts.append({"name": "model-weights", "mountPath": dep.pvc_mount_path})
    shm = _runtime(dep).get("shm_size_gi")
    if shm:
        volumes.append({"name": "shm", "emptyDir": {"medium": "Memory", "sizeLimit": f"{int(shm)}Gi"}})
        mounts.append({"name": "shm", "mountPath": "/dev/shm"})
    return volumes, mounts


def build_container(
    dep: CustomModelDeployment,
    *,
    name: str,
    args: list[str],
    command: list[str] | None,
    env: list[dict],
    gpu_count: int | None,
    ports: list[dict],
    probes: dict,
    volume_mounts: list,
    resources_override: dict | None = None,
) -> dict:
    container: dict = {
        "name": name,
        "image": dep.image,
        "args": args,
        "ports": ports,
        "resources": _resources(gpu_count, dep.gpu_resource_key, dep, resources_override),
        "env": env,
        "volumeMounts": volume_mounts,
        **probes,
    }
    if command:
        container["command"] = command
    if _runtime(dep).get("privileged"):
        container["securityContext"] = {"privileged": True}
    return container


def build_pod_spec(
    dep: CustomModelDeployment,
    containers: list[dict],
    volumes: list,
    *,
    init_containers: list[dict] | None = None,
    node_selector: dict | None = None,
    tolerations: list | None = None,
) -> dict:
    pod_spec: dict = {"containers": containers, "volumes": volumes}
    if init_containers:
        pod_spec["initContainers"] = init_containers
    selector = node_selector if node_selector is not None else dep.node_selector
    tols = tolerations if tolerations is not None else dep.tolerations
    if selector:
        pod_spec["nodeSelector"] = dict(selector)
    if tols:
        pod_spec["tolerations"] = list(tols)
    if _runtime(dep).get("host_ipc"):
        pod_spec["hostIPC"] = True
    return pod_spec


def _deployment(
    name: str, namespace: str, labels: dict, selector: dict, pod_labels_: dict, replicas: int, pod_spec: dict
) -> dict:
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name, "namespace": namespace, "labels": labels},
        "spec": {
            "replicas": replicas,
            "selector": {"matchLabels": selector},
            "template": {"metadata": {"labels": pod_labels_}, "spec": pod_spec},
        },
    }


def build_deployment(dep: CustomModelDeployment) -> dict:
    """The aggregated (single-pool) Deployment."""
    names = k8s_resource_names(dep)
    labels = _labels(dep)
    command, args = container_launch(dep)
    volumes, mounts = _volumes(dep)
    container = build_container(
        dep,
        name=engine_of(dep),
        args=args,
        command=command,
        env=[{"name": k, "value": str(v)} for k, v in (dep.env or {}).items()],
        gpu_count=dep.gpu_count,
        ports=[{"containerPort": VLLM_PORT, "name": "http"}],
        probes=render_probes(dep),
        volume_mounts=mounts,
    )
    return _deployment(
        names["deployment"],
        dep.namespace,
        labels,
        labels,
        pod_labels(dep),
        dep.replicas,
        build_pod_spec(dep, [container], volumes),
    )


def build_pd_deployments(dep: CustomModelDeployment, *, sidecar_image: str | None = None) -> list[dict]:
    """Prefill + decode Deployments for ``serving_mode == "pd"``."""
    pd_serving.engine_guard(dep)
    names = k8s_resource_names(dep)
    base_labels = _labels(dep)
    volumes, mounts = _volumes(dep)
    out = []
    for role in pd_serving.ROLES:
        spec = pd_serving.role_view(dep, role)
        selector = {**base_labels, pd_serving.LABEL_PD_ROLE: role}
        labels_on_pod = {**pod_labels(dep), pd_serving.LABEL_PD_ROLE: role, pd_serving.LABEL_ROLE: role}
        ports = [
            {"containerPort": spec.port, "name": "http"},
            {"containerPort": spec.nixl_port, "name": "nixl", "protocol": "TCP"},
        ]
        container = build_container(
            dep,
            name="vllm",
            args=pd_serving.role_args(dep, spec),
            command=None,
            env=pd_serving.role_env(spec),
            gpu_count=spec.gpu_count,
            ports=ports,
            probes=render_probes(dep, port=spec.port, startup_default=STARTUP_DEFAULTS_PD),
            volume_mounts=mounts,
            resources_override=spec.resources(),
        )
        init = None
        if role == "decode":
            init = [pd_serving.sidecar_container(pd_serving.sidecar_image_for(dep, sidecar_image))]
        out.append(
            _deployment(
                names[f"{role}_deployment"],
                dep.namespace,
                selector,
                selector,
                labels_on_pod,
                spec.replicas,
                build_pod_spec(dep, [container], volumes, init_containers=init),
            )
        )
    return out


def _service(name: str, namespace: str, labels: dict, selector: dict, target_port: int) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": name, "namespace": namespace, "labels": labels},
        "spec": {
            "type": "ClusterIP",
            "selector": selector,
            "ports": [{"name": "http", "port": 80, "targetPort": target_port, "protocol": "TCP"}],
        },
    }


def build_service(dep: CustomModelDeployment) -> dict:
    names = k8s_resource_names(dep)
    labels = _labels(dep)
    return _service(names["service"], dep.namespace, labels, labels, VLLM_PORT)


def build_pd_services(dep: CustomModelDeployment) -> list[dict]:
    """One Service per pool; both expose 80 → 8000 (decode's 8000 is the sidecar)."""
    names = k8s_resource_names(dep)
    base = _labels(dep)
    return [
        _service(names[f"{role}_service"], dep.namespace, base, {**base, pd_serving.LABEL_PD_ROLE: role}, SERVING_PORT)
        for role in pd_serving.ROLES
    ]


def build_ingress(dep: CustomModelDeployment) -> dict:
    names = k8s_resource_names(dep)
    labels = _labels(dep)
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "Ingress",
        "metadata": {"name": names["ingress"], "namespace": dep.namespace, "labels": labels},
        "spec": {
            "ingressClassName": dep.ingress_class,
            "rules": [
                {
                    "host": dep.ingress_host,
                    "http": {
                        "paths": [
                            {
                                "path": dep.ingress_path,
                                "pathType": "Prefix",
                                "backend": {
                                    "service": {
                                        "name": names["service"],
                                        "port": {"number": 80},
                                    }
                                },
                            }
                        ]
                    },
                }
            ],
        },
    }


def build_all(dep: CustomModelDeployment, *, sidecar_image: str | None = None) -> list[dict]:
    if pd_serving.is_pd(dep):
        return [*build_pd_deployments(dep, sidecar_image=sidecar_image), *build_pd_services(dep)]
    return [build_deployment(dep), build_service(dep), build_ingress(dep)]
