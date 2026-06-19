# Accuracy benchmark runner image for llmops portal.
# Drives lm-eval-harness accuracy runs against a LiteLLM/OpenAI endpoint.
# (Performance runs use the official `vllm bench serve` on a vLLM image —
# see services/benchmark_manifests.py::build_vllm_bench_job.)
# Spawned as a K8s Job by the portal backend; entrypoint reads BENCH_* env vars.
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
