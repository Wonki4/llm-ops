"""Match pool configurations across rendered manifests and admitted Pods."""

import hashlib
import json


def pool_fingerprint(cluster_id, namespace, role, gpu_type, container, pod_spec) -> str:
    # Kubernetes injects service-account mounts and defaults fieldRef.apiVersion.
    mounts = [
        {key: value for key, value in mount.items() if value}
        for mount in container.get("volumeMounts", [])
        if not mount["name"].startswith("kube-api-access-")
    ]
    volume_names = {mount["name"] for mount in mounts}
    volumes = [v for v in pod_spec.get("volumes", []) if v["name"] in volume_names]
    env = json.loads(json.dumps(container.get("env") or []))
    for variable in env:
        field = variable.get("valueFrom", {}).get("fieldRef", {})
        if field.get("apiVersion") == "v1":
            field.pop("apiVersion")
    # Names and access credentials are routing-only. Preserve all engine flags,
    # including their order, so last-value overrides cannot silently match.
    args = []
    skipping = False
    for arg in container.get("args") or []:
        if arg.startswith("--"):
            skipping = arg.split("=", 1)[0] in {"--api-key", "--served-model-name"}
            if skipping:
                skipping = "=" not in arg
                continue
        if not skipping:
            args.append(arg)
    config = [
        str(cluster_id or ""), namespace, role, gpu_type, container.get("image"),
        container.get("command") or [], args, env, mounts, volumes,
        {k: str(v) for k, v in (container.get("resources", {}).get("limits") or {}).items()},
        pod_spec.get("nodeSelector") or {}, pod_spec.get("runtimeClassName"),
    ]
    return hashlib.sha256(json.dumps(config, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
