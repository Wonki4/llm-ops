"""Ephemeral serving for benchmark runs.

A benchmark run can clone an existing serving deployment into a throwaway
Deployment + Service (optionally overriding resources / args), benchmark it,
then tear it down. These are pure helpers shared by the API (which provisions)
and the reconciler (which gates on readiness, creates the job, and tears down).

The temp resources are NOT persisted in custom_model_deployment and are NOT
registered with LiteLLM — they exist only for the lifetime of the run.
"""

from __future__ import annotations

import uuid

from app.db.models.custom_model_deployment import CustomModelDeployment
from app.services.model_deployment_manifests import (
    build_deployment,
    build_service,
    k8s_resource_names,
)


def ephemeral_model_name(run_id: uuid.UUID) -> str:
    """DNS-safe base name for a run's temp serving (≤ K8s 63-char budget once
    `-deployment` / `-service` suffixes are added)."""
    return f"bench-{str(run_id)[:12]}"


# Keys callers may override on the cloned spec (others are inherited verbatim).
_SCALAR_OVERRIDES = {
    "image",
    "model_path",
    "replicas",
    "gpu_count",
    "gpu_resource_key",
    "cpu_request",
    "cpu_limit",
    "memory_request",
    "memory_limit",
}


def build_ephemeral_deployment(
    base: CustomModelDeployment,
    *,
    name: str,
    namespace: str,
    overrides: dict | None,
) -> CustomModelDeployment:
    """Clone `base` into an in-memory deployment (not added to any session) used
    only to render K8s manifests. `overrides` may set resource / arg / env knobs;
    `gpu_type` is folded into the node selector under the `gpu-type` label.
    """
    ov = dict(overrides or {})
    dep = CustomModelDeployment(
        model_name=name,
        namespace=namespace,
        image=base.image,
        replicas=base.replicas,
        gpu_count=base.gpu_count,
        gpu_resource_key=base.gpu_resource_key,
        cpu_request=base.cpu_request,
        cpu_limit=base.cpu_limit,
        memory_request=base.memory_request,
        memory_limit=base.memory_limit,
        node_selector=dict(base.node_selector or {}),
        tolerations=list(base.tolerations or []) or None,
        pvc_name=base.pvc_name,
        pvc_mount_path=base.pvc_mount_path,
        model_path=base.model_path,
        vllm_extra_args=list(base.vllm_extra_args or []),
        env=dict(base.env or {}),
        # Ingress is required by the column but unused for ephemeral servings
        # (we hit the Service directly); give it a harmless placeholder.
        ingress_host=f"{name}.invalid",
        ingress_path="/",
        ingress_class="nginx",
    )

    for key in _SCALAR_OVERRIDES:
        if key in ov and ov[key] is not None:
            setattr(dep, key, ov[key])
    if ov.get("vllm_extra_args") is not None:
        dep.vllm_extra_args = list(ov["vllm_extra_args"])
    if ov.get("env") is not None:
        dep.env = dict(ov["env"])
    # GPU type → node selector label.
    gpu_type = ov.get("gpu_type")
    if gpu_type:
        ns_sel = dict(dep.node_selector or {})
        ns_sel["gpu-type"] = gpu_type
        dep.node_selector = ns_sel
    if ov.get("node_selector") is not None:
        dep.node_selector = dict(ov["node_selector"])

    return dep


def ephemeral_manifests(dep: CustomModelDeployment) -> list[dict]:
    """Deployment + Service only (no Ingress) for a temp serving."""
    return [build_deployment(dep), build_service(dep)]


def serving_target_url(name: str, namespace: str) -> str:
    """In-cluster base URL for the temp serving's Service (port 80)."""
    svc = k8s_resource_names(_NameOnly(name))["service"]
    return f"http://{svc}.{namespace}.svc.cluster.local"


def serving_resource_names(name: str) -> dict[str, str]:
    return k8s_resource_names(_NameOnly(name))


def _arg_value(args: list, flag: str) -> str | None:
    """Value of ``--flag value`` or ``--flag=value`` in a CLI args list."""
    for i, a in enumerate(args):
        if a == flag and i + 1 < len(args):
            return str(args[i + 1])
        if isinstance(a, str) and a.startswith(flag + "="):
            return a.split("=", 1)[1]
    return None


def external_bench_facts(spec: dict) -> dict:
    """What the bench job needs to know about an external serving's clone.

    served_model is what `vllm bench serve --model` must send (the name the
    server reports): --served-model-name wins, else the --model value. The
    tokenizer is always the --model value. When the model path is backed by a
    PVC-mounted volume, expose it so the bench Job mounts the same weights for
    tokenizer loading.
    """
    args = spec["container"]["args"]
    model_arg = _arg_value(args, "--model")
    served = _arg_value(args, "--served-model-name") or model_arg
    if not served:
        raise ValueError("no --model/--served-model-name found in serving args")

    pvc_name = pvc_mount = None
    if model_arg:
        for m in spec["container"].get("volume_mounts") or []:
            mount_path = m.get("mountPath") or m.get("mount_path") or ""
            if mount_path and model_arg.startswith(mount_path.rstrip("/") + "/"):
                vol = next(
                    (v for v in (spec.get("volumes") or []) if v.get("name") == m.get("name")), None
                )
                claim = ((vol or {}).get("persistentVolumeClaim") or (vol or {}).get("persistent_volume_claim") or {})
                if claim.get("claimName") or claim.get("claim_name"):
                    pvc_name = claim.get("claimName") or claim.get("claim_name")
                    pvc_mount = mount_path
                break
    return {
        "served_model": served,
        "tokenizer": model_arg or served,
        "model_arg": model_arg or "",
        "pvc_name": pvc_name,
        "pvc_mount_path": pvc_mount,
    }


def _clone_target_port(container: dict) -> int:
    port = _arg_value(container.get("args") or [], "--port")
    if port and str(port).isdigit():
        return int(port)
    for p in container.get("ports") or []:
        cp = p.get("containerPort") or p.get("container_port")
        if cp:
            return int(cp)
    return 8000


def build_external_clone(spec: dict, *, name: str, overrides: dict | None = None) -> list[dict]:
    """Deployment + Service for a throwaway clone of a live external serving.

    Faithful copy of the first container and pod-level scheduling/volume fields;
    replicas forced to 1; names/Service port chosen so the existing ephemeral
    reconciler (serving_resource_names + serving_target_url on port 80) drives
    it unchanged. ``overrides`` may replace ``resources`` and/or ``image``.
    """
    names = serving_resource_names(name)
    labels = {"app": "llmops-bench-serving", "bench-serving": name}
    src = spec["container"]
    ov = overrides or {}
    container: dict = {
        "name": src.get("name") or "server",
        "image": ov.get("image") or src["image"],
        "args": list(src.get("args") or []),
        "env": list(src.get("env_raw") or []),
        "resources": ov.get("resources") or src.get("resources") or {},
        "volumeMounts": list(src.get("volume_mounts") or []),
    }
    if src.get("command"):
        container["command"] = list(src["command"])
    target_port = _clone_target_port(src)
    container["ports"] = [{"containerPort": target_port}]

    pod_spec: dict = {"containers": [container]}
    if spec.get("volumes"):
        pod_spec["volumes"] = spec["volumes"]
    if spec.get("node_selector"):
        pod_spec["nodeSelector"] = spec["node_selector"]
    if spec.get("tolerations"):
        pod_spec["tolerations"] = spec["tolerations"]

    deployment = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": names["deployment"], "labels": labels},
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": labels},
            "template": {"metadata": {"labels": labels}, "spec": pod_spec},
        },
    }
    service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": names["service"], "labels": labels},
        "spec": {
            "selector": labels,
            "ports": [{"port": 80, "targetPort": target_port}],
        },
    }
    return [deployment, service]


class _NameOnly:
    """Minimal stand-in so k8s_resource_names (which only reads model_name) works
    without constructing a full deployment."""

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
