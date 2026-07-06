"""Accuracy benchmark runner: drives lm-evaluation-harness, emits `<<<RESULT>>>{json}`.

Performance benchmarks no longer run here — they use the official
`vllm bench serve` CLI on a vLLM image (see
backend/app/services/benchmark_manifests.py::build_vllm_bench_job). This slim
image only handles accuracy (lm-eval) runs against a LiteLLM/OpenAI endpoint.

Inputs (env, all required unless noted):
- BENCH_TOOL: lm_eval
- BENCH_KIND: accuracy
- BENCH_MODEL: LiteLLM-registered model alias
- BENCH_TARGET_URL: LiteLLM base URL (e.g. http://litellm:4000)
- BENCH_API_KEY: portal master key
- BENCH_PARAMS_JSON: tool-specific params (verbatim user input)
- BENCH_RUN_ID: opaque id (for logs)

Output:
- Last line on stdout is `<<<RESULT>>>{json}` — the portal reconciler parses
  this and stores it in custom_benchmark_run.result.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

RESULT_MARKER = "<<<RESULT>>>"


def env(name: str, default: str | None = None, *, required: bool = True) -> str:
    v = os.environ.get(name, default)
    if required and not v:
        raise SystemExit(f"missing env: {name}")
    return v or ""


def emit_result(payload: dict[str, Any]) -> None:
    print(f"{RESULT_MARKER}{json.dumps(payload, ensure_ascii=False)}", flush=True)


# ─── Accuracy runner (lm-evaluation-harness) ────────────────────────


def run_accuracy(*, model: str, base_url: str, api_key: str, params: dict) -> dict:
    """Shell out to `lm_eval` against the LiteLLM proxy as the API model."""
    tasks = params.get("tasks") or ["mmlu"]
    if isinstance(tasks, str):
        tasks = [tasks]
    num_fewshot = params.get("num_fewshot")
    limit = params.get("limit")
    batch_size = str(params.get("batch_size", 8))

    # lm-eval's `local-chat-completions` model speaks OpenAI chat-completions.
    model_args = ",".join(
        [
            f"model={model}",
            f"base_url={base_url.rstrip('/')}/v1/chat/completions",
            "num_concurrent=" + str(params.get("num_concurrent", 4)),
            "max_retries=2",
            "tokenized_requests=False",
        ]
    )
    env_copy = os.environ.copy()
    env_copy["OPENAI_API_KEY"] = api_key

    cmd = [
        "lm_eval",
        "--model", "local-chat-completions",
        "--model_args", model_args,
        "--tasks", ",".join(tasks),
        "--batch_size", batch_size,
        "--output_path", "/tmp/lm-eval-out",
    ]
    if num_fewshot is not None:
        cmd += ["--num_fewshot", str(num_fewshot)]
    if limit is not None:
        cmd += ["--limit", str(limit)]
    # Wrap prompts with the model's chat template before sending (chat models).
    if params.get("apply_chat_template"):
        cmd += ["--apply_chat_template"]
    # Generation controls, e.g. "temperature=0,max_gen_toks=256".
    gen_kwargs = params.get("gen_kwargs")
    if gen_kwargs:
        cmd += ["--gen_kwargs", str(gen_kwargs)]

    print(f"$ {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, env=env_copy, capture_output=True, text=True)
    print(proc.stdout, flush=True)
    if proc.returncode != 0:
        print(proc.stderr, file=sys.stderr, flush=True)
        return {
            "tasks": tasks,
            "ok": False,
            "exit_code": proc.returncode,
            "stderr_tail": proc.stderr[-2000:],
        }

    # lm-eval emits a JSON summary in stdout; grab the last JSON object printed.
    metrics: dict | None = None
    for line in reversed(proc.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                metrics = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    return {
        "tasks": tasks,
        "ok": True,
        "metrics": metrics or {"raw_stdout_tail": proc.stdout[-2000:]},
    }


# ─── Dispatch ───────────────────────────────────────────────────────


def main() -> None:
    tool = env("BENCH_TOOL")
    kind = env("BENCH_KIND")
    model = env("BENCH_MODEL")
    base_url = env("BENCH_TARGET_URL")
    api_key = env("BENCH_API_KEY")
    raw_params = env("BENCH_PARAMS_JSON", default="{}", required=False)
    run_id = env("BENCH_RUN_ID", default="", required=False)
    try:
        params = json.loads(raw_params) if raw_params else {}
    except json.JSONDecodeError as e:
        raise SystemExit(f"BENCH_PARAMS_JSON invalid: {e}")

    print(
        f"benchmark start: id={run_id} tool={tool} kind={kind} model={model} "
        f"target={base_url} params={raw_params}",
        flush=True,
    )

    if kind == "accuracy":
        result = run_accuracy(model=model, base_url=base_url, api_key=api_key, params=params)
    elif kind == "performance":
        raise SystemExit(
            "performance benchmarks run via `vllm bench serve`, not this runner image"
        )
    else:
        raise SystemExit(f"unknown BENCH_KIND: {kind}")

    payload = {
        "tool": tool,
        "kind": kind,
        "model": model,
        "params": params,
        "metrics": result,
    }
    emit_result(payload)


if __name__ == "__main__":
    main()
