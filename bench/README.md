The portal launches all benchmarks as Kubernetes Jobs. Performance and prefix-cache Jobs embed the Python client from the backend; the client requires aiohttp. General performance also needs transformers and the selected tokenizer (mounted weights, a cached tokenizer or a reachable Hugging Face ID). The target must return streaming OpenAI-compatible chat completions with usage. Cache experiments use vLLM completions with token IDs and read cache counters from the isolated server. No benchmark CLI is required.

Accuracy uses a separate image. Build it from this directory and make it available to the Job's cluster:

    docker build -t llmops-benchmark:portal-v1 .

For a private registry, publish the image there and set APP_BENCHMARK_ACCURACY_IMAGE on both the backend and the worker (Helm: backend.env.APP_BENCHMARK_ACCURACY_IMAGE). For local testing, load the built image into minikube. Use a new image tag when changing the runner.

The accuracy runner submits inference requests itself; lm-evaluation-harness 0.4.4 supplies task definitions, token boundaries, aggregation and scoring. The default MMLU evaluation needs a matching tokenizer and a completions endpoint that returns echoed token log probabilities. For generation-only tasks on chat-only endpoints, enable chat mode. Datasets must be accessible to the Job. The result includes the scorer version and effective sample counts. Historical CLI results can be opened unchanged; runner versions and measurement methods can differ.

Benchmark credentials are passed through Kubernetes Secrets in the run namespace. Apply the updated `deploy/rbac/portal-k8s-rbac.yaml` (or grant `create` on core `secrets` in benchmark namespaces) before upgrading the backend and worker together. The Secret is owned by the runner Job or the temporary accuracy Deployment and is collected when that owner is deleted.

Migration `061_benchmark_credentials` scrubs historical serving snapshots. For pre-upgrade runs still waiting for a serving, it encrypts only the runner credential with the existing portal encryption configuration. Run migrations with the same encryption settings as the worker. The worker discards this temporary field on handoff or termination. Existing Kubernetes workloads and backups have their own retention periods.
