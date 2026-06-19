"""K8s Job manifest builder for benchmark runs.

The runner image (built from `bench/Dockerfile`) reads tool/params from env
and writes a `<<<RESULT>>>{...}` line to stdout that the reconciler harvests.
"""

from __future__ import annotations

import json
import uuid

from app.db.models.custom_benchmark_run import CustomBenchmarkRun


def job_name_for(run_id: uuid.UUID) -> str:
    """Deterministic Job name from a run id. K8s names ≤ 63 chars, lowercase."""
    return f"bench-{str(run_id)[:32]}"


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
