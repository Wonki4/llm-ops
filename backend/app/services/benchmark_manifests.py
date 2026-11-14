"""K8s Job manifest builder for benchmark runs.

The runner image (built from `bench/Dockerfile`) reads tool/params from env
and writes a `<<<RESULT>>>{...}` line to stdout that the reconciler harvests.
"""

from __future__ import annotations

import json
import shlex
import uuid
from pathlib import Path

from app.services.benchmark_workload import performance_workload
from app.db.models.custom_benchmark_run import CustomBenchmarkRun


def job_name_for(run_id: uuid.UUID) -> str:
    """Deterministic Job name from a run id. K8s names ≤ 63 chars, lowercase."""
    return f"bench-{str(run_id)[:32]}"


def nfs_fields_incomplete(
    server: str | None, path: str | None, mount_path: str | None
) -> bool:
    """True when some — but not all — of the NFS fields are set.

    An NFS mount needs server + export path + mount path together; callers reject
    a partial set with a 400.
    """
    flags = (bool(server), bool(path), bool(mount_path))
    return any(flags) and not all(flags)


def _runner_source():
    return Path(__file__).with_name("portal_bench_runner.py").read_text()


def _runner_env(run, base, model, tokenizer, api_key):
    params = run.params or {}
    workload = params if params.get("experiment") == "prefix_cache" else performance_workload(params)
    values = {"BENCH_URL": base, "BENCH_MODEL": model, "BENCH_TOKENIZER": tokenizer or model,
              "BENCH_WORKLOAD": json.dumps(workload), "OPENAI_API_KEY": api_key, "BENCH_RUN_ID": str(run.id)}
    return [{"name": k, "value": v} for k, v in values.items()]


def build_performance_job(
    run: CustomBenchmarkRun,
    *,
    image: str,
    target_base_url: str,
    api_key: str,
    served_model: str,
    tokenizer: str | None = None,
    volumes: list | None = None,
    volume_mounts: list | None = None,
    backoff_limit: int = 0,
    ttl_seconds_after_finished: int = 7 * 24 * 3600,
) -> dict:
    """Run the portal streaming client against an existing OpenAI-compatible target."""
    name = job_name_for(run.id)

    # Model-weights mount, prebuilt by the caller: a deployment target's own
    # storages (read-only) or, for a raw model_name, the cluster's bench storage.
    volumes = list(volumes or [])
    volume_mounts = list(volume_mounts or [])

    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "namespace": run.k8s_namespace,
            "labels": {"app": "llmops-benchmark", "bench-tool": run.tool, "bench-kind": run.kind},
        },
        "spec": {
            "backoffLimit": backoff_limit,
            "ttlSecondsAfterFinished": ttl_seconds_after_finished,
            "template": {
                "metadata": {"labels": {"app": "llmops-benchmark", "job-name": name}},
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "runner",
                            "image": image,
                            "imagePullPolicy": "IfNotPresent",
                            "command": ["python", "-c", _runner_source()],
                            "env": _runner_env(run, target_base_url, served_model, tokenizer, api_key),
                            "volumeMounts": volume_mounts,
                        }
                    ],
                    "volumes": volumes,
                },
            },
        },
    }


_HEALTH_TRIES = 900
_HEALTH_INTERVAL = 2


def build_self_serving_bench_job(
    run: CustomBenchmarkRun,
    *,
    serving_deployment: dict,
    serve_argv: list[str],
    port: int,
    api_key: str,
    served_model: str,
    tokenizer: str | None = None,
    backoff_limit: int = 0,
    ttl_seconds_after_finished: int = 7 * 24 * 3600,
) -> dict:
    """One Job that serves the model and benchmarks it in the same pod.

    Reuses the serving container's image/GPU/mounts/env and pod-level
    volumes/scheduling from ``serving_deployment`` (a Deployment manifest as
    built by build_deployment / build_external_clone), but replaces the run
    command with: background ``serve_argv``, poll ``/health`` on localhost until
    ready, run the portal runner against ``http://localhost:<port>``, emit the
    RESULT marker, stop the server.
    """
    name = job_name_for(run.id)
    src_pod = serving_deployment["spec"]["template"]["spec"]
    src_c = src_pod["containers"][0]

    base_url = f"http://localhost:{port}"
    serve_cmd = " ".join(shlex.quote(a) for a in serve_argv)
    benchmark = f"python - <<'PORTAL_RUNNER'\n{_runner_source()}\nPORTAL_RUNNER"
    wait = (
        "python - \"$SRV\" <<'PY'\n"
        "import os, urllib.request, time, sys\n"
        f"for _ in range({_HEALTH_TRIES}):\n"
        "    os.kill(int(sys.argv[1]), 0)\n"
        "    try:\n"
        f"        urllib.request.urlopen('{base_url}/health', timeout=3); sys.exit(0)\n"
        "    except Exception:\n"
        f"        time.sleep({_HEALTH_INTERVAL})\n"
        "sys.exit(1)\n"
        "PY"
    )
    script = (
        f"{serve_cmd} &\n"
        "SRV=$!\n"
        'trap \'kill "$SRV" 2>/dev/null || true\' EXIT\n'
        f"if ! {wait}\n"
        "then\n"
        "  echo 'benchmark aborted: serving did not become ready within the startup window' >&2\n"
        "  kill $SRV 2>/dev/null || true\n"
        "  exit 1\n"
        "fi\n"
        "set -e\n"
        f"{benchmark}\n"
    )

    runner_env = _runner_env(run, base_url, served_model, tokenizer, api_key)
    reserved = {item["name"] for item in runner_env}
    env = [e for e in src_c.get("env", []) if e["name"] not in reserved] + runner_env

    container = {
        "name": "serve-bench",
        "image": src_c["image"],
        "imagePullPolicy": "IfNotPresent",
        "command": ["sh", "-c", script],
        "env": env,
        "resources": src_c.get("resources") or {},
        "volumeMounts": list(src_c.get("volumeMounts") or []),
    }
    if src_c.get("securityContext"):
        container["securityContext"] = src_c["securityContext"]
    pod_spec: dict = {"restartPolicy": "Never", "containers": [container]}
    for key in ("hostIPC", "initContainers", "imagePullSecrets"):
        if src_pod.get(key):
            pod_spec[key] = src_pod[key]
    if src_pod.get("volumes"):
        pod_spec["volumes"] = src_pod["volumes"]
    if src_pod.get("nodeSelector"):
        pod_spec["nodeSelector"] = src_pod["nodeSelector"]
    if src_pod.get("tolerations"):
        pod_spec["tolerations"] = src_pod["tolerations"]

    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "namespace": run.k8s_namespace,
            "labels": {"app": "llmops-benchmark", "bench-tool": run.tool, "bench-kind": run.kind},
        },
        "spec": {
            "backoffLimit": backoff_limit,
            "ttlSecondsAfterFinished": ttl_seconds_after_finished,
            "template": {
                "metadata": {"labels": {"app": "llmops-benchmark", "job-name": name}},
                "spec": pod_spec,
            },
        },
    }


def build_job_manifest(
    run: CustomBenchmarkRun,
    *,
    image: str,
    target_base_url: str,
    api_key: str,
    bench_model: str | None = None,
    tokenizer: str | None = None,
    volumes: list | None = None,
    volume_mounts: list | None = None,
    backoff_limit: int = 0,
    ttl_seconds_after_finished: int = 7 * 24 * 3600,
) -> dict:
    """Return a K8s Job manifest that runs one benchmark.

    The runner pulls everything it needs from env:
    - BENCH_TOOL: `vllm_serving` | `sglang_serving` | `lm_eval`
    - BENCH_KIND: `performance` | `accuracy`
    - BENCH_MODEL: LiteLLM-registered alias
    - BENCH_TARGET_URL: LiteLLM proxy base URL (e.g. http://litellm:4000)
    - BENCH_API_KEY: portal master key
    - BENCH_PARAMS_JSON: user-supplied params blob, verbatim
    """
    name = job_name_for(run.id)
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "namespace": run.k8s_namespace,
            "labels": {
                "app": "llmops-benchmark",
                "bench-tool": run.tool,
                "bench-kind": run.kind,
            },
        },
        "spec": {
            "backoffLimit": backoff_limit,
            "ttlSecondsAfterFinished": ttl_seconds_after_finished,
            "template": {
                "metadata": {
                    "labels": {
                        "app": "llmops-benchmark",
                        "job-name": name,
                    },
                },
                "spec": {
                    "restartPolicy": "Never",
                    "volumes": volumes or [],
                    "containers": [
                        {
                            "name": "runner",
                            "image": image,
                            "imagePullPolicy": "IfNotPresent",
                            "volumeMounts": volume_mounts or [],
                            "env": [
                                {"name": "BENCH_TOKENIZER", "value": (run.params or {}).get("tokenizer") or tokenizer or bench_model or run.model_name},
                                {"name": "BENCH_TOOL", "value": run.tool},
                                {"name": "BENCH_KIND", "value": run.kind},
                                {"name": "BENCH_MODEL", "value": bench_model or run.model_name},
                                {"name": "BENCH_TARGET_URL", "value": target_base_url},
                                {"name": "BENCH_API_KEY", "value": api_key},
                                {"name": "BENCH_PARAMS_JSON", "value": json.dumps(run.params)},
                                {"name": "BENCH_RUN_ID", "value": str(run.id)},
                            ],
                        }
                    ],
                },
            },
        },
    }
