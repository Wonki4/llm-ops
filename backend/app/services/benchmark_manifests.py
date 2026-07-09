"""K8s Job manifest builder for benchmark runs.

The runner image (built from `bench/Dockerfile`) reads tool/params from env
and writes a `<<<RESULT>>>{...}` line to stdout that the reconciler harvests.
"""

from __future__ import annotations

import json
import shlex
import uuid

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


def resolve_bench_nfs(
    params: dict | None,
    *,
    default_server: str | None = None,
    default_path: str | None = None,
    default_mount_path: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Pick the NFS mount (server, export path, mount path) for a benchmark
    against a raw model_name.

    Precedence: a per-run override in ``params`` (``nfs_server`` / ``nfs_path`` /
    ``nfs_mount_path``) → the cluster's default NFS → none. Empty strings are
    normalised to None. (Deployment targets mount their own PVC, handled
    separately.)
    """
    p = params or {}
    server = (p.get("nfs_server") or default_server) or None
    path = (p.get("nfs_path") or default_path) or None
    mount = (p.get("nfs_mount_path") or default_mount_path) or None
    return server, path, mount


# Params consumed explicitly above or used only by the portal (not CLI flags).
# Everything else in `params` is passed through to `vllm bench serve`.
_NON_CLI_PARAMS = frozenset(
    {
        "random_input_len",
        "random_output_len",
        "num_prompts",
        "request_rate",
        "max_concurrency",
        "ignore_eos",
        "tokenizer",
        "seed",
        "goodput",
        "nfs_server",
        "nfs_path",
        "nfs_mount_path",
        "extra_args",
    }
)


def _vllm_bench_argv(
    run: CustomBenchmarkRun,
    *,
    target_base_url: str,
    served_model: str,
    tokenizer: str | None,
) -> list[str]:
    """The `vllm bench serve` argv (no shell). Shared by the URL-target bench
    Job and the self-serving bench Job."""
    p = run.params or {}
    args = [
        "vllm", "bench", "serve",
        "--backend", "openai-chat",
        "--base-url", target_base_url,
        "--endpoint", "/v1/chat/completions",
        "--model", served_model,
        "--tokenizer", tokenizer or served_model,
        "--dataset-name", "random",
        "--random-input-len", str(int(p.get("random_input_len", 1024))),
        "--random-output-len", str(int(p.get("random_output_len", 128))),
        "--num-prompts", str(int(p.get("num_prompts", 200))),
        "--percentile-metrics", "ttft,tpot,itl,e2el",
        "--metric-percentiles", "90,99",
        "--seed", str(int(p.get("seed", 0))),
        "--save-result", "--result-dir", "/tmp", "--result-filename", "r.json",
    ]
    if p.get("request_rate") not in (None, ""):
        args += ["--request-rate", str(float(p["request_rate"]))]
    if p.get("max_concurrency") not in (None, ""):
        args += ["--max-concurrency", str(int(p["max_concurrency"]))]
    if p.get("goodput") not in (None, ""):
        args += ["--goodput", *str(p["goodput"]).split()]
    if p.get("ignore_eos"):
        args += ["--ignore-eos"]
    used_flags = {a for a in args if a.startswith("--")}
    for key, val in p.items():
        if key in _NON_CLI_PARAMS or val in (None, ""):
            continue
        flag = "--" + key.replace("_", "-")
        if flag in used_flags:
            continue
        if isinstance(val, bool):
            if val:
                args.append(flag)
        else:
            args += [flag, str(val)]
    extra_args = p.get("extra_args")
    if isinstance(extra_args, str) and extra_args.strip():
        args += shlex.split(extra_args)
    return args


def build_vllm_bench_job(
    run: CustomBenchmarkRun,
    *,
    image: str,
    target_base_url: str,
    api_key: str,
    served_model: str,
    tokenizer: str | None = None,
    pvc_name: str | None = None,
    pvc_mount_path: str | None = None,
    nfs_server: str | None = None,
    nfs_path: str | None = None,
    nfs_mount_path: str | None = None,
    backoff_limit: int = 0,
    ttl_seconds_after_finished: int = 7 * 24 * 3600,
) -> dict:
    """K8s Job that runs the official `vllm bench serve` against an
    OpenAI-compatible endpoint and emits the result.json as a RESULT marker.

    Runs on a vLLM image (the CLI ships with vllm). The OpenAI-chat backend
    authenticates via the OPENAI_API_KEY env var, so portal/LiteLLM-protected
    targets work. The synthetic `random` dataset avoids any runtime download
    (air-gap safe); a tokenizer is still needed — defaults to `served_model`,
    or a PVC-mounted path when the serving deployment provides one.
    """
    name = job_name_for(run.id)

    args = _vllm_bench_argv(run, target_base_url=target_base_url, served_model=served_model, tokenizer=tokenizer)

    bench_cmd = " ".join(shlex.quote(a) for a in args)
    # vllm bench serve prints a summary table + writes /tmp/r.json. We collapse
    # that JSON to one line and wrap it in the RESULT marker the reconciler
    # harvests (it scans for the last `<<<RESULT>>>{json}` line).
    emit = 'echo "<<<RESULT>>>{\\"metrics\\": $(tr -d \'\\n\' < /tmp/r.json)}"'
    script = f"set -e\n{bench_cmd}\n{emit}\n"

    # Model-weights volume: a deployment target reuses its own PVC; a raw
    # model_name target attaches an NFS export directly (no pre-created PVC).
    volumes = []
    volume_mounts = []
    if pvc_name and pvc_mount_path:
        volumes.append({"name": "model-weights", "persistentVolumeClaim": {"claimName": pvc_name, "readOnly": True}})
        volume_mounts.append({"name": "model-weights", "mountPath": pvc_mount_path, "readOnly": True})
    elif nfs_server and nfs_path and nfs_mount_path:
        volumes.append({"name": "model-weights", "nfs": {"server": nfs_server, "path": nfs_path, "readOnly": True}})
        volume_mounts.append({"name": "model-weights", "mountPath": nfs_mount_path, "readOnly": True})

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
                            "command": ["sh", "-c", script],
                            "env": [
                                {"name": "OPENAI_API_KEY", "value": api_key},
                                {"name": "BENCH_RUN_ID", "value": str(run.id)},
                            ],
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
    ready, run ``vllm bench serve`` against ``http://localhost:<port>``, emit the
    RESULT marker, stop the server.
    """
    name = job_name_for(run.id)
    src_pod = serving_deployment["spec"]["template"]["spec"]
    src_c = src_pod["containers"][0]

    base_url = f"http://localhost:{port}"
    serve_cmd = " ".join(shlex.quote(a) for a in serve_argv)
    bench_cmd = " ".join(
        shlex.quote(a)
        for a in _vllm_bench_argv(run, target_base_url=base_url, served_model=served_model, tokenizer=tokenizer)
    )
    emit = 'echo "<<<RESULT>>>{\\"metrics\\": $(tr -d \'\\n\' < /tmp/r.json)}"'
    wait = (
        "python - <<'PY'\n"
        "import urllib.request, time, sys\n"
        f"for _ in range({_HEALTH_TRIES}):\n"
        "    try:\n"
        f"        urllib.request.urlopen('{base_url}/health', timeout=3); sys.exit(0)\n"
        "    except Exception:\n"
        f"        time.sleep({_HEALTH_INTERVAL})\n"
        "sys.exit(1)\n"
        "PY"
    )
    script = (
        "set -m\n"
        f"{serve_cmd} &\n"
        "SRV=$!\n"
        f"if ! {wait}\n"
        "then\n"
        "  echo 'benchmark aborted: serving did not become ready within the startup window' >&2\n"
        "  kill $SRV 2>/dev/null || true\n"
        "  exit 1\n"
        "fi\n"
        "set -e\n"
        f"{bench_cmd}\n"
        f"{emit}\n"
        "kill $SRV 2>/dev/null || true\n"
    )

    env = list(src_c.get("env") or [])
    env = [e for e in env if e.get("name") not in ("OPENAI_API_KEY", "BENCH_RUN_ID")]
    env += [
        {"name": "OPENAI_API_KEY", "value": api_key},
        {"name": "BENCH_RUN_ID", "value": str(run.id)},
    ]

    container = {
        "name": "serve-bench",
        "image": src_c["image"],
        "imagePullPolicy": "IfNotPresent",
        "command": ["sh", "-c", script],
        "env": env,
        "resources": src_c.get("resources") or {},
        "volumeMounts": list(src_c.get("volumeMounts") or []),
    }
    pod_spec: dict = {"restartPolicy": "Never", "containers": [container]}
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
                    "containers": [
                        {
                            "name": "runner",
                            "image": image,
                            "imagePullPolicy": "IfNotPresent",
                            "env": [
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
