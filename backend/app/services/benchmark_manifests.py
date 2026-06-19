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
        "--seed", "0",
        "--save-result", "--result-dir", "/tmp", "--result-filename", "r.json",
    ]
    if p.get("request_rate") not in (None, ""):
        args += ["--request-rate", str(float(p["request_rate"]))]
    if p.get("max_concurrency") not in (None, ""):
        args += ["--max-concurrency", str(int(p["max_concurrency"]))]
    if p.get("ignore_eos"):
        args += ["--ignore-eos"]

    bench_cmd = " ".join(shlex.quote(a) for a in args)
    # vllm bench serve prints a summary table + writes /tmp/r.json. We collapse
    # that JSON to one line and wrap it in the RESULT marker the reconciler
    # harvests (it scans for the last `<<<RESULT>>>{json}` line).
    emit = 'echo "<<<RESULT>>>{\\"metrics\\": $(tr -d \'\\n\' < /tmp/r.json)}"'
    script = f"set -e\n{bench_cmd}\n{emit}\n"

    volumes = []
    volume_mounts = []
    if pvc_name and pvc_mount_path:
        volumes.append({"name": "model-weights", "persistentVolumeClaim": {"claimName": pvc_name, "readOnly": True}})
        volume_mounts.append({"name": "model-weights", "mountPath": pvc_mount_path, "readOnly": True})

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
