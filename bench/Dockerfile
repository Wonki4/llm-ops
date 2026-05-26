# Benchmark runner image for llmops portal.
# Drives vllm/sglang-style perf tests + lm-eval-harness accuracy runs against
# a LiteLLM proxy endpoint. Spawned as a K8s Job by the portal backend;
# entrypoint reads BENCH_* env vars (see services/benchmark_manifests.py).
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# openai client + lm-evaluation-harness with API-model support.
# Keeping torch/transformers out — accuracy runs hit the LiteLLM proxy,
# not local weights.
RUN pip install --no-cache-dir \
        openai>=1.30 \
        httpx>=0.27 \
        numpy>=1.26 \
        "lm-eval==0.4.4"

COPY entrypoint.py /app/entrypoint.py

ENTRYPOINT ["python", "/app/entrypoint.py"]
