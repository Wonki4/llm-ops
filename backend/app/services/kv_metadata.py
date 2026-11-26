"""Read model metadata in the deployment namespace, using the resolved read-only storage bindings."""

import asyncio
import hashlib
import json
import re
import time
import uuid
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path, PurePosixPath

from kubernetes_asyncio.client.exceptions import ApiException

from app.services.benchmark_credentials import BenchmarkCredentials
from app.services.clusters import k8s_for_cluster
from app.services.kv_capacity import final_flags
from app.services.kv_metadata_reader import HUB_ENV

_CACHE = OrderedDict()


@lru_cache(maxsize=1)
def _reference_models():
    path = Path(__file__).with_name("data") / "kv_reference_models.json"
    return json.loads(path.read_text())


def uses_public_hub(pod, container):
    if container.get("envFrom") or any(e.get("name") in HUB_ENV for e in container.get("env", [])):
        return False
    mounted = {mount["name"] for mount in container.get("volumeMounts", [])}
    return not any(
        v["name"] in mounted and ("persistentVolumeClaim" in v or "nfs" in v or "hostPath" in v)
        for v in pod.get("volumes", [])
    )


def pinned_reference(pod, container, model, revision):
    """Use bundled public checkpoint metadata only when it identifies these exact weights."""
    if not re.fullmatch(r"[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+", model or "") or not re.fullmatch(
        r"[0-9a-f]{40}", revision or ""
    ) or not uses_public_hub(pod, container):
        return None
    record = _reference_models().get(model, {}).get(revision)
    if record:
        return {**record, "source": "pinned_reference", "revision": revision}
    return None


def _resolved_weight_components(pod, container, model, data):
    # The metadata job resolves a floating public revision to the commit actually read.
    # Reuse only its verified tensor sizes; keep the engine version and live config.
    if data.get("source") == "hub" and data.get("config_digest") and "weight_components" not in data:
        ref = pinned_reference(pod, container, model, data.get("revision"))
        if (ref and ref.get("weight_components") and data["config_digest"] == ref.get("config_digest")
                and data.get("weight_bytes") == ref.get("weight_bytes")):
            return {**data, "weight_components": ref["weight_components"]}
    return data


def _hub_env(engine):
    # Kubernetes envFrom merges in list order and explicit env wins. Import
    # only Hub keys; forwarding every entry would also inject engine credentials.
    env = [e for e in engine.get("env", []) if e["name"] in HUB_ENV]
    explicit = {e["name"] for e in env}
    sources = engine.get("envFrom", [])
    for index, source in enumerate(sources):
        prefix = source.get("prefix", "")
        ref = source.get("secretRef") or source.get("configMapRef")
        if not isinstance(ref, dict) or not ref.get("name"):
            raise ValueError("invalid metadata environment")
        kind = "secretKeyRef" if "secretRef" in source else "configMapKeyRef"
        for key in sorted(HUB_ENV - explicit):
            name = key.removeprefix(prefix)
            if not key.startswith(prefix) or not name:
                continue
            env.append({"name": f"KV_HUB_{index}_{key}", "valueFrom": {kind: {
                "name": ref["name"], "key": name, "optional": True,
            }}})
    return env


def metadata_job(dep, pod, engine, model, revision):
    volumes = []
    mounts = []
    file_env = {e["value"] for e in engine.get("env", []) if e.get("name") in {
        "HF_TOKEN_PATH", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE",
    } and e.get("value", "").startswith("/")}
    for mount in engine.get("volumeMounts", []):
        volume = next((v for v in pod.get("volumes", []) if v["name"] == mount["name"]), {})
        file_mount = any(PurePosixPath(file).is_relative_to(mount["mountPath"]) for file in file_env)
        if not (any(key in volume for key in ("persistentVolumeClaim", "nfs"))
                or file_mount and any(key in volume for key in ("configMap", "secret"))):
            continue
        if "<unbound:" in str(volume):
            raise ValueError("unresolved storage binding")
        volumes.append(volume)
        mounts.append({**mount, "readOnly": True})
    if model.startswith("/") and not any(PurePosixPath(model).is_relative_to(m["mountPath"]) for m in mounts):
        raise ValueError("model path needs a resolved storage mount")
    env = _hub_env(engine)
    flags = final_flags(engine.get("args") or [])
    fmt = flags.get("config-format", "auto") if model == flags.get("model", model) else "auto"
    placement = {k: pod[k] for k in ("nodeSelector", "affinity", "tolerations", "imagePullSecrets") if k in pod}
    source = Path(__file__).with_name("kv_metadata_reader.py").read_text()
    identity = [
        model,
        revision,
        str(dep.cluster_id),
        dep.namespace,
        fmt,
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
        {"name": "KV_CONFIG_FORMAT", "value": fmt},
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
            # Retry anonymous denial after credentials change at the same Secret name.
            # Successful immutable reads remain cached in the portal for a day.
            "ttlSecondsAfterFinished": min(ttl, 120),
            "template": {"metadata": {"labels": labels}, "spec": pod_spec},
        },
    }
    return name, manifest, credentials, ttl


async def metadata_for(db, dep, pod, container, model, revision, *, start):
    try:
        reference = pinned_reference(pod, container, model, revision)
        if reference:
            return reference
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
                data = _resolved_weight_components(pod, container, model, data)
                _CACHE[name] = (now + (min(ttl, 120) if data.get("error") else ttl), data)
                if len(_CACHE) > 256:
                    _CACHE.popitem(last=False)
                return data
        return {"error": "metadata job returned no readable config"}
    except ValueError as exc:
        if str(exc) in {"unresolved storage binding", "model path needs a resolved storage mount"}:
            return {"error": "mount_unavailable", "metadata_issue": "mount_unavailable"}
        return {"error": "invalid model metadata", "metadata_issue": "metadata_job_unavailable"}
    except Exception as exc:
        # Capacity is optional. Keep unrelated YAML previews and serving reconciliation available.
        return {"error": "metadata unavailable (" + type(exc).__name__ + ")"}
