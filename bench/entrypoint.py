"""Benchmark runner: drives the requested tool, emits `<<<RESULT>>>{json}`.

Inputs (env, all required unless noted):
- BENCH_TOOL: vllm_serving | sglang_serving | lm_eval
- BENCH_KIND: performance | accuracy
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

import asyncio
import json
import os
import statistics
import subprocess
import sys
import time
from typing import Any

import httpx

RESULT_MARKER = "<<<RESULT>>>"


def env(name: str, default: str | None = None, *, required: bool = True) -> str:
    v = os.environ.get(name, default)
    if required and not v:
        raise SystemExit(f"missing env: {name}")
    return v or ""


def emit_result(payload: dict[str, Any]) -> None:
    print(f"{RESULT_MARKER}{json.dumps(payload, ensure_ascii=False)}", flush=True)


# ─── Performance runner ─────────────────────────────────────────────


async def _one_request(
    client: httpx.AsyncClient,
    *,
    model: str,
    prompt: str,
    max_tokens: int,
    temperature: float,
) -> dict[str, float]:
    """Single streamed chat-completion call. Returns timing metrics."""
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
    }
    started = time.perf_counter()
    ttft: float | None = None
    output_tokens = 0
    async with client.stream("POST", "/v1/chat/completions", json=body) as r:
        r.raise_for_status()
        async for line in r.aiter_lines():
            if not line or not line.startswith("data:"):
                continue
            chunk = line[len("data:"):].strip()
            if chunk == "[DONE]":
                break
            if ttft is None:
                ttft = time.perf_counter() - started
            try:
                parsed = json.loads(chunk)
            except json.JSONDecodeError:
                continue
            for choice in parsed.get("choices", []):
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    output_tokens += 1  # crude — chunk-as-token
    total = time.perf_counter() - started
    return {
        "ttft_s": ttft if ttft is not None else total,
        "total_s": total,
        "output_tokens": output_tokens,
        "tpot_s": (total - (ttft or 0)) / max(output_tokens - 1, 1) if output_tokens > 1 else 0.0,
    }


async def run_performance(*, model: str, base_url: str, api_key: str, params: dict) -> dict:
    """Concurrent-load benchmark — same shape as vllm/sglang benchmark_serving."""
    num_prompts = int(params.get("num_prompts", 32))
    concurrency = int(params.get("concurrency", 8))
    max_tokens = int(params.get("max_tokens", 128))
    temperature = float(params.get("temperature", 0.0))
    prompt = params.get(
        "prompt",
        "Write a short paragraph explaining the difference between TCP and UDP.",
    )

    sem = asyncio.Semaphore(concurrency)
    samples: list[dict[str, float]] = []
    errors = 0

    async def worker():
        nonlocal errors
        async with sem:
            try:
                m = await _one_request(
                    client,
                    model=model,
                    prompt=prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                samples.append(m)
            except Exception as e:  # noqa: BLE001
                errors += 1
                print(f"request error: {e}", file=sys.stderr)

    timeout = httpx.Timeout(60.0, connect=10.0)
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=timeout) as client:
        wall_start = time.perf_counter()
        await asyncio.gather(*(worker() for _ in range(num_prompts)))
        wall_total = time.perf_counter() - wall_start

    def pct(xs: list[float], p: float) -> float:
        if not xs:
            return 0.0
        xs_sorted = sorted(xs)
        k = max(0, min(len(xs_sorted) - 1, int(round(p / 100 * (len(xs_sorted) - 1)))))
        return xs_sorted[k]

    ttfts = [s["ttft_s"] for s in samples]
    tpots = [s["tpot_s"] for s in samples if s["tpot_s"] > 0]
    totals = [s["total_s"] for s in samples]
    out_toks = sum(s["output_tokens"] for s in samples)

    return {
        "num_prompts": num_prompts,
        "concurrency": concurrency,
        "successful": len(samples),
        "errors": errors,
        "wall_time_s": wall_total,
        "throughput_req_per_s": len(samples) / wall_total if wall_total > 0 else 0.0,
        "total_output_tokens": out_toks,
        "throughput_output_tok_per_s": out_toks / wall_total if wall_total > 0 else 0.0,
        "ttft_s": {
            "mean": statistics.fmean(ttfts) if ttfts else 0.0,
            "p50": pct(ttfts, 50),
            "p90": pct(ttfts, 90),
            "p99": pct(ttfts, 99),
        },
        "tpot_s": {
            "mean": statistics.fmean(tpots) if tpots else 0.0,
            "p50": pct(tpots, 50),
            "p90": pct(tpots, 90),
            "p99": pct(tpots, 99),
        },
        "total_s": {
            "mean": statistics.fmean(totals) if totals else 0.0,
            "p50": pct(totals, 50),
            "p90": pct(totals, 90),
            "p99": pct(totals, 99),
        },
    }


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

    if kind == "performance":
        result = asyncio.run(
            run_performance(model=model, base_url=base_url, api_key=api_key, params=params)
        )
    elif kind == "accuracy":
        result = run_accuracy(model=model, base_url=base_url, api_key=api_key, params=params)
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
