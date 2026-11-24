"""Read model metadata in the deployment namespace, using the resolved read-only storage bindings."""

import asyncio
import hashlib
import json
import re
import time
import uuid
from collections import OrderedDict
from pathlib import Path, PurePosixPath

from kubernetes_asyncio.client.exceptions import ApiException

from app.services.benchmark_credentials import BenchmarkCredentials
from app.services.clusters import k8s_for_cluster

_CACHE = OrderedDict()
_HUB_ENV = {
    "HF_ENDPOINT",
    "HF_HOME",
    "HF_HUB_CACHE",
    "HUGGINGFACE_HUB_CACHE",
    "HF_HUB_OFFLINE",
    "TRANSFORMERS_OFFLINE",
    "HF_TOKEN",
    "HUGGING_FACE_HUB_TOKEN",
    "REQUESTS_CA_BUNDLE",
    "SSL_CERT_FILE",
}


def metadata_job(dep, pod, engine, model, revision):
    volumes = []
    mounts = []
    for mount in engine.get("volumeMounts", []):
        volume = next((v for v in pod.get("volumes", []) if v["name"] == mount["name"]), {})
        if not any(key in volume for key in ("persistentVolumeClaim", "nfs")):
            continue
        if "<unbound:" in str(volume):
            raise ValueError("unresolved storage binding")
        volumes.append(volume)
        mounts.append({**mount, "readOnly": True})
    if model.startswith("/") and not any(PurePosixPath(model).is_relative_to(m["mountPath"]) for m in mounts):
        raise ValueError("model path needs a resolved storage mount")
    env = [e for e in engine.get("env", []) if e["name"] in _HUB_ENV]
    placement = {k: pod[k] for k in ("nodeSelector", "affinity", "tolerations", "imagePullSecrets") if k in pod}
    source = Path(__file__).with_name("kv_metadata_reader.py").read_text()
    identity = [
        model,
        revision,
        str(dep.cluster_id),
        dep.namespace,
        mounts,
        volumes,
        engine["image"],
        env,
        placement,
        hashlib.sha256(source.encode()).hexdigest(),
    ]
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    credentials = BenchmarkCredentials(uuid.UUID(digest[:32]))
    name = "kv-metadata-" + digest[:32]
    env = [
        *env,
        {"name": "KV_MODEL", "value": model},
        {"name": "KV_REVISION", "value": revision or ""},
        {"name": "HF_HUB_DISABLE_TELEMETRY", "value": "1"},
    ]
    # Protect only inherited credentials. Code, model and revision can stay as normal argv/env.
    private_env = [e for e in env if e["name"] in {"HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HF_ENDPOINT"}]
    public_env = [e for e in env if e not in private_env]
    container = {
        "name": "metadata",
        "image": engine["image"],
        "command": ["python3", "-c", source],
        "env": public_env + credentials.protect_env(private_env),
        "volumeMounts": mounts,
        "resources": {"requests": {"cpu": "100m", "memory": "128Mi"}, "limits": {"cpu": "1", "memory": "512Mi"}},
        "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
    }
    pod_spec = {
        **placement,
        "restartPolicy": "Never",
        "automountServiceAccountToken": False,
        "volumes": volumes,
        "containers": [container],
    }
    immutable = bool(re.fullmatch(r"[a-fA-F0-9]{40,64}", revision or "") and not model.startswith("/"))
    ttl = 86400 if immutable else 600
    labels = {"bench-run": str(credentials.run_id), "app.kubernetes.io/component": "model-metadata"}
    manifest = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name, "labels": labels},
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": 180,
            "ttlSecondsAfterFinished": ttl,
            "template": {"metadata": {"labels": labels}, "spec": pod_spec},
        },
    }
    return name, manifest, credentials, ttl


async def metadata_for(db, dep, pod, container, model, revision, *, start):
    try:
        name, manifest, credentials, ttl = metadata_job(dep, pod, container, model, revision)
        now = time.monotonic()
        cached = _CACHE.get(name)
        if cached and cached[0] > now:
            _CACHE.move_to_end(name)
            return cached[1]
        async with asyncio.timeout(6):
            api = await k8s_for_cluster(db, dep.cluster_id)
            try:
                status = await api.read_job_status(dep.namespace, name)
            except ApiException as exc:
                if exc.status != 404:
                    raise
                if not start:
                    return {"error": "metadata not collected"}
                try:
                    await api.create_job(dep.namespace, manifest, secret=credentials.manifest())
                except ApiException as collision:
                    if collision.status != 409:
                        raise
                return {"pending": True}
            if status["run_id"] != str(credentials.run_id):
                return {"error": "metadata job identity mismatch"}
            if credentials.values and not (status["succeeded"] or status["failed"]):
                await api.ensure_benchmark_job_secret(dep.namespace, manifest, credentials.manifest())
            if status["failed"]:
                return {"error": "metadata job failed; check the selected image and storage"}
            if not status["succeeded"]:
                return {"pending": True}
            log = await api.read_job_pod_logs(dep.namespace, name, tail_lines=3)
        for line in reversed(log.splitlines()):
            if line.startswith("KV_METADATA=") and len(line) <= 1 << 18:
                data = json.loads(line.removeprefix("KV_METADATA="))
                if not isinstance(data, dict) or not (data.get("config") or data.get("error")):
                    break
                _CACHE[name] = (now + ttl, data)
                if len(_CACHE) > 256:
                    _CACHE.popitem(last=False)
                return data
        return {"error": "metadata job returned no readable config"}
    except ValueError as exc:
        return {"error": str(exc)[:140] if type(exc) is ValueError else "invalid model metadata"}
    except Exception as exc:
        # Capacity is optional. Keep unrelated YAML previews and serving reconciliation available.
        return {"error": "metadata unavailable (" + type(exc).__name__ + ")"}
