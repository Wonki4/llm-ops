The portal launches all benchmarks as Kubernetes Jobs. Performance and prefix-cache Jobs embed the Python client from the backend; the client requires aiohttp. General performance also needs transformers and the selected tokenizer (mounted weights, a cached tokenizer or a reachable Hugging Face ID). The target must return streaming OpenAI-compatible chat completions with usage. Cache experiments use vLLM completions with token IDs and read cache counters from the isolated server. No benchmark CLI is required.

Accuracy uses a separate image. Build it from this directory and make it available to the Job's cluster:

    docker build -t llmops-benchmark:portal-v1 .

For a private registry, publish the image there and set APP_BENCHMARK_ACCURACY_IMAGE on both the backend and the worker (Helm: backend.env.APP_BENCHMARK_ACCURACY_IMAGE). For local testing, load the built image into minikube. Use a new image tag when changing the runner.

The accuracy runner submits inference requests itself; lm-evaluation-harness 0.4.4 supplies task definitions, token boundaries, aggregation and scoring. The default MMLU evaluation needs a matching tokenizer and a completions endpoint that returns echoed token log probabilities. For generation-only tasks on chat-only endpoints, enable chat mode. Datasets must be accessible to the Job. The result includes the scorer version and effective sample counts. Historical CLI results can be opened unchanged; runner versions and measurement methods can differ.
