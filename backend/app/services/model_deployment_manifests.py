"""Build K8s Deployment + Service + Ingress manifests from a deployment row.

The portal only owns the metadata in custom_model_deployment; this module
turns that into the concrete K8s resources we apply via the K8s client.
Aggregated servings render one Deployment/Service/Ingress; prefill/decode
servings (``serving_mode == "pd"``) render two Deployments and two Services
(no Ingress: the llm-d router is the entry) via ``pd_serving``.
Pure functions, no side effects.
"""

from app.db.models.custom_model_deployment import CustomModelDeployment
from app.services import common_labels, pd_serving, serving_rollout
from app.services.serving_engines import (
    SERVING_PORT,
    container_launch,
    engine_of,
    kv_event_ports,
    serving_port,
    with_pod_ip,
)
from app.services.serving_names import resource_name
from app.services.serving_probes import STARTUP_DEFAULTS_PD, render_probes
from app.services.storage_mounts import to_volumes

VLLM_PORT = SERVING_PORT  # kept for existing imports
LABEL_OWNER = "llm-ops/managed-by"
# The owner label value new deployments get. Deployments created before the rename keep
# LEGACY_OWNER in their (immutable) selector; the update path reads it back from the cluster.
OWNER = "llm-ops-portal"
LEGACY_OWNER = "litellm-portal"
PORTAL_OWNERS = (OWNER, LEGACY_OWNER)
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
    """Map lifecycle keys (e.g. prefill_deployment) to Kubernetes object names."""
    if pd_serving.is_pd(dep):
        return pd_serving.role_resource_names(dep)
    return {kind: resource_name(dep.model_name, kind) for kind in ("deployment", "service", "ingress")}


def _labels(dep: CustomModelDeployment, owner: str = OWNER) -> dict[str, str]:
    """Selector labels: immutable once a Deployment exists, so never add here."""
    return {LABEL_OWNER: owner, LABEL_MODEL: dep.model_name}


def pod_labels(dep: CustomModelDeployment, owner: str = OWNER) -> dict[str, str]:
    """Labels stamped on the serving pods: the selector labels plus llm-d's
    ``llm-d.ai/model`` so an llm-d router can target this deployment."""
    return {**_labels(dep, owner), LABEL_LLMD_MODEL: dep.model_name}


def common_pod_labels(dep: CustomModelDeployment, gpu_type: str | None, metrics_port: int) -> dict[str, str]:
    """The portal's common pod labels from the rules ``dep`` was last applied with
    (``dep.common_labels``), for a pool on ``gpu_type``. Never part of a selector."""
    rules = getattr(dep, "common_labels", None)
    if not rules:
        return {}
    return common_labels.render(
        rules,
        model_name=pd_serving.serving_name(dep),
        namespace=dep.namespace,
        gpu_type=gpu_type,
        metrics_port=metrics_port,
    )


def common_pod_annotations(dep: CustomModelDeployment, gpu_type: str | None, metrics_port: int) -> dict[str, str]:
    """The portal's common pod annotations from the rules ``dep`` was last applied with
    (``dep.common_annotations``), for a pool on ``gpu_type``."""
    rules = getattr(dep, "common_annotations", None)
    if not rules:
        return {}
    return common_labels.render_annotations(
        rules,
        model_name=pd_serving.serving_name(dep),
        namespace=dep.namespace,
        gpu_type=gpu_type,
        metrics_port=metrics_port,
    )


def _resources(gpu_count: int | None, gpu_resource_key: str, dep: CustomModelDeployment) -> dict:
    # GPU optional — omit the resource entirely when gpu_count == 0 so the pod is
    # CPU-only and schedulable on nodes without GPUs. CPU/memory are optional too.
    requests: dict = {}
    limits: dict = {}
    if gpu_count and gpu_count > 0:
        # Extended resources need requests == limits; state both so the manifest says what it gets.
        limits[gpu_resource_key] = str(gpu_count)
        requests[gpu_resource_key] = str(gpu_count)
    if dep.cpu_request:
        requests["cpu"] = dep.cpu_request
    if dep.cpu_limit:
        limits["cpu"] = dep.cpu_limit
    if dep.memory_request:
        requests["memory"] = dep.memory_request
    if dep.memory_limit:
        limits["memory"] = dep.memory_limit
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


def engine_ports(row, args: list[str], role: pd_serving.RoleSpec | None = None) -> list[dict]:
    """The portal's ports, then the runtime's ``extra_ports``, then the KV-event sockets
    from ``--kv-events-config``; the first claim on a port number or name wins."""
    out = [{"containerPort": role.port if role else serving_port(row), "name": "http"}]
    if role:
        out.append({"containerPort": role.nixl_port, "name": "nixl", "protocol": "TCP"})
    extra = [
        {"containerPort": int(port), "name": name, "protocol": "TCP"}
        for name, port in (_runtime(row).get("extra_ports") or {}).items()
    ]
    for p in (*extra, *kv_event_ports(args)):
        if all(p["containerPort"] != q["containerPort"] and p["name"] != q.get("name") for q in out):
            out.append(p)
    return out


def metrics_port(ports: list[dict]) -> int:
    """Prefer a declared metrics listener, falling back to the engine HTTP listener."""
    by_name = {port["name"]: port["containerPort"] for port in ports}
    return by_name.get("metrics", by_name["http"])


def _volumes(dep: CustomModelDeployment) -> tuple[list, list]:
    """(volumes, volumeMounts): the resolved storages, then ``/dev/shm`` when runtime asks."""
    volumes, mounts = to_volumes(getattr(dep, "storages", None) or [])
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
) -> dict:
    container: dict = {
        "name": name,
        "image": dep.image,
        "args": args,
        "ports": ports,
        "resources": _resources(gpu_count, dep.gpu_resource_key, dep),
        "env": env,
        "volumeMounts": volume_mounts,
        **probes,
    }
    if command:
        container["command"] = command
    if _runtime(dep).get("privileged"):
        container["securityContext"] = {"privileged": True}
    hook = serving_rollout.lifecycle(_runtime(dep))
    if hook:
        container["lifecycle"] = hook
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
    pod_spec["terminationGracePeriodSeconds"] = serving_rollout.termination_grace_seconds(_runtime(dep))
    return pod_spec


def _deployment(
    name: str,
    namespace: str,
    labels: dict,
    selector: dict,
    pod_labels_: dict,
    replicas: int,
    pod_spec: dict,
    *,
    strategy: dict,
    annotations: dict | None = None,
    pod_annotations: dict | None = None,
) -> dict:
    # No annotations key when there are none: an existing Deployment renders as before.
    metadata: dict = {"name": name, "namespace": namespace, "labels": labels}
    if annotations:
        metadata["annotations"] = dict(annotations)
    template_meta: dict = {"labels": pod_labels_}
    if pod_annotations:
        template_meta["annotations"] = dict(pod_annotations)
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": metadata,
        "spec": {
            "replicas": replicas,
            "selector": {"matchLabels": selector},
            "strategy": strategy,
            "template": {"metadata": template_meta, "spec": pod_spec},
        },
    }


def build_deployment(dep: CustomModelDeployment, owner: str = OWNER) -> dict:
    """The aggregated (single-pool) Deployment."""
    names = k8s_resource_names(dep)
    labels = _labels(dep, owner)
    command, args = container_launch(dep)
    ports = engine_ports(dep, args)
    scrape_port = metrics_port(ports)
    common = common_pod_labels(dep, getattr(dep, "gpu_type", None), scrape_port)
    annotations = common_pod_annotations(dep, getattr(dep, "gpu_type", None), scrape_port)
    volumes, mounts = _volumes(dep)
    container = build_container(
        dep,
        name=engine_of(dep),
        args=args,
        command=command,
        env=with_pod_ip([pd_serving.env_item(k, v) for k, v in (dep.env or {}).items()], args),
        gpu_count=dep.gpu_count,
        ports=ports,
        probes=render_probes(dep, port=serving_port(dep)),
        volume_mounts=mounts,
    )
    return _deployment(
        names["deployment"],
        dep.namespace,
        {**common, **labels},
        labels,
        {**common, **pod_labels(dep, owner)},
        dep.replicas,
        build_pod_spec(dep, [container], volumes),
        strategy=serving_rollout.strategy(_runtime(dep)),
        annotations=annotations,
        pod_annotations=annotations,
    )


def build_pd_deployments(
    dep: CustomModelDeployment, *, sidecar_image: str | None = None, owner: str = OWNER
) -> list[dict]:
    """Prefill + decode Deployments for ``serving_mode == "pd"``.

    Each pool is rendered from its own deployment-like row (``pd_serving.pool_row``:
    image, model path, resources, storage, probes, runtime, placement), so the two
    Deployments can differ in anything but namespace, name and the KV plumbing.
    """
    pd_serving.engine_guard(dep)
    names = k8s_resource_names(dep)
    base_labels = _labels(dep, owner)
    out = []
    for role in pd_serving.ROLES:
        spec = pd_serving.role_view(dep, role)
        pool = spec.pool
        selector = {**base_labels, pd_serving.LABEL_PD_ROLE: role}
        args = pd_serving.role_args(dep, spec)
        ports = engine_ports(pool, args, spec)
        scrape_port = metrics_port(ports)
        common = common_pod_labels(dep, spec.gpu_type, scrape_port)
        annotations = common_pod_annotations(dep, spec.gpu_type, scrape_port)
        # Common labels, then the pool's P/D extra labels, then the portal-owned ones: later wins.
        labels_on_pod = {
            **common,
            **spec.pod_labels,
            **pod_labels(dep, owner),
            pd_serving.LABEL_PD_ROLE: role,
            pd_serving.LABEL_ROLE: role,
        }
        volumes, mounts = _volumes(pool)
        container = build_container(
            pool,
            name="vllm",
            args=args,
            command=None,
            env=with_pod_ip(pd_serving.role_env(spec), args),
            gpu_count=spec.gpu_count,
            ports=ports,
            probes=render_probes(pool, port=spec.port, startup_default=STARTUP_DEFAULTS_PD),
            volume_mounts=mounts,
        )
        if spec.security_context:  # root + IPC_LOCK / SYS_RESOURCE unless the pool's P/D settings say otherwise
            container["securityContext"] = {**container.get("securityContext", {}), **spec.security_context}
        if spec.pd_resources:  # P/D extras (e.g. rdma/ib): requested and limited like runtime extra_resources
            res = container["resources"]
            for key, qty in spec.pd_resources.items():
                res.setdefault("limits", {})[key] = str(qty)
                res.setdefault("requests", {})[key] = str(qty)
        init = None
        if role == "decode":
            init = [
                pd_serving.sidecar_container(
                    pd_serving.sidecar_image_for(dep, sidecar_image), pd_serving.decode_sidecar_spec(dep)
                )
            ]
        out.append(
            _deployment(
                names[f"{role}_deployment"],
                dep.namespace,
                {**common, **selector},
                selector,
                labels_on_pod,
                spec.replicas,
                build_pod_spec(pool, [container], volumes, init_containers=init),
                strategy=serving_rollout.strategy(_runtime(pool)),
                annotations=annotations,
                pod_annotations={**annotations, **spec.pod_annotations},  # the pool's own win
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


def build_service(dep: CustomModelDeployment, owner: str = OWNER) -> dict:
    names = k8s_resource_names(dep)
    labels = _labels(dep, owner)
    return _service(names["service"], dep.namespace, labels, labels, serving_port(dep))


def build_pd_services(dep: CustomModelDeployment, owner: str = OWNER) -> list[dict]:
    """One Service per pool; both expose 80 → 8000 (decode's 8000 is the sidecar)."""
    names = k8s_resource_names(dep)
    base = _labels(dep, owner)
    return [
        _service(names[f"{role}_service"], dep.namespace, base, {**base, pd_serving.LABEL_PD_ROLE: role}, SERVING_PORT)
        for role in pd_serving.ROLES
    ]


def build_ingress(dep: CustomModelDeployment, owner: str = OWNER) -> dict:
    names = k8s_resource_names(dep)
    labels = _labels(dep, owner)
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


def build_all(dep: CustomModelDeployment, *, sidecar_image: str | None = None, owner: str = OWNER) -> list[dict]:
    """All manifests for ``dep``. ``owner`` is the managed-by value its selector carries:
    OWNER for a new deployment, whatever the live Deployment has on update (``live_owner``)."""
    if pd_serving.is_pd(dep):
        return [*build_pd_deployments(dep, sidecar_image=sidecar_image, owner=owner), *build_pd_services(dep, owner)]
    return [build_deployment(dep, owner), build_service(dep, owner), build_ingress(dep, owner)]


async def live_owner(k8s, dep: CustomModelDeployment) -> str:
    """The managed-by value the existing Deployment's selector was created with
    (selectors are immutable), OWNER when there is none yet."""
    names = k8s_resource_names(dep)
    live = await k8s.read_deployment(dep.namespace, names.get("deployment") or names["prefill_deployment"])
    value = ((live or {}).get("labels") or {}).get(LABEL_OWNER)
    return value if value in PORTAL_OWNERS else OWNER
