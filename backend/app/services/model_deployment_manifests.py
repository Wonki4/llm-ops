"""Build K8s Deployment + Service + Ingress manifests from a deployment row.

The portal only owns the metadata in custom_model_deployment; this module
turns that into the three concrete K8s resources we apply via the K8s client.
Pure functions, no side effects.
"""

from app.db.models.custom_model_deployment import CustomModelDeployment

VLLM_PORT = 8000
LABEL_OWNER = "llm-ops/managed-by"
LABEL_MODEL = "llm-ops/model-name"


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
    """Stable resource names: <model>-deployment / -service / -ingress."""
    safe = dep.model_name.lower().replace("_", "-").replace(".", "-").replace("/", "-")
    return {
        "deployment": f"{safe}-deployment",
        "service": f"{safe}-service",
        "ingress": f"{safe}-ingress",
    }


def _labels(dep: CustomModelDeployment) -> dict[str, str]:
    return {LABEL_OWNER: "litellm-portal", LABEL_MODEL: dep.model_name}


def build_deployment(dep: CustomModelDeployment) -> dict:
    names = k8s_resource_names(dep)
    labels = _labels(dep)

    # Resources: GPU optional — omit the GPU resource entirely when gpu_count == 0
    # so the pod is CPU-only and schedulable on nodes without GPUs. CPU/memory
    # are likewise optional. (`gpu_count and` guards a None on an in-memory row.)
    requests: dict = {}
    limits: dict = {}
    if dep.gpu_count and dep.gpu_count > 0:
        limits[dep.gpu_resource_key] = str(dep.gpu_count)
    if dep.cpu_request:
        requests["cpu"] = dep.cpu_request
    if dep.cpu_limit:
        limits["cpu"] = dep.cpu_limit
    if dep.memory_request:
        requests["memory"] = dep.memory_request
    if dep.memory_limit:
        limits["memory"] = dep.memory_limit
    resources: dict = {}
    if limits:
        resources["limits"] = limits
    if requests:
        resources["requests"] = requests

    # vLLM command/args
    args = ["--model", dep.model_path, "--port", str(VLLM_PORT)]
    if dep.vllm_extra_args:
        args.extend(dep.vllm_extra_args)

    # Env
    env_items = [{"name": k, "value": str(v)} for k, v in (dep.env or {}).items()]

    # Volumes (only when PVC + mount path are both set)
    volumes = []
    volume_mounts = []
    if dep.pvc_name and dep.pvc_mount_path:
        volumes.append({"name": "model-weights", "persistentVolumeClaim": {"claimName": dep.pvc_name}})
        volume_mounts.append({"name": "model-weights", "mountPath": dep.pvc_mount_path})

    pod_spec: dict = {
        "containers": [
            {
                "name": "vllm",
                "image": dep.image,
                "args": args,
                "ports": [{"containerPort": VLLM_PORT, "name": "http"}],
                "resources": resources,
                "env": env_items,
                "volumeMounts": volume_mounts,
                "readinessProbe": {
                    "httpGet": {"path": "/health", "port": VLLM_PORT},
                    "initialDelaySeconds": 60,
                    "periodSeconds": 10,
                    "timeoutSeconds": 5,
                    "failureThreshold": 30,
                },
            }
        ],
        "volumes": volumes,
    }
    if dep.node_selector:
        pod_spec["nodeSelector"] = dict(dep.node_selector)
    if dep.tolerations:
        pod_spec["tolerations"] = list(dep.tolerations)

    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": names["deployment"], "namespace": dep.namespace, "labels": labels},
        "spec": {
            "replicas": dep.replicas,
            "selector": {"matchLabels": labels},
            "template": {
                "metadata": {"labels": labels},
                "spec": pod_spec,
            },
        },
    }


def build_service(dep: CustomModelDeployment) -> dict:
    names = k8s_resource_names(dep)
    labels = _labels(dep)
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": names["service"], "namespace": dep.namespace, "labels": labels},
        "spec": {
            "type": "ClusterIP",
            "selector": labels,
            "ports": [{"name": "http", "port": 80, "targetPort": VLLM_PORT, "protocol": "TCP"}],
        },
    }


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


def build_all(dep: CustomModelDeployment) -> list[dict]:
    return [build_deployment(dep), build_service(dep), build_ingress(dep)]
