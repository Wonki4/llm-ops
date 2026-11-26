"""Match a measured validation deployment against the current serving configuration."""

import hmac
import json

from app.config import settings

# Only fields that can affect compute, workload identity or routing. Publication
# status and LiteLLM IDs change when a verified private deployment is promoted.
_FIELDS = (
    "id",
    "cluster_id",
    "namespace",
    "model_name",
    "model_path",
    "image",
    "engine",
    "engine_args",
    "vllm_extra_args",
    "env",
    "runtime",
    "serving_mode",
    "gpu_type",
    "gpu_count",
    "gpu_resource_key",
    "replicas",
    "cpu_request",
    "cpu_limit",
    "memory_request",
    "memory_limit",
    "node_selector",
    "tolerations",
    "storages",
    "probes",
    "pd_config",
    "aggregated_router",
    "router_stack_id",
    "common_labels",
    "common_annotations",
    "ingress_host",
    "ingress_path",
    "ingress_class",
)


def deployment_identity(dep) -> str:
    fields = {key: getattr(dep, key, None) for key in _FIELDS}
    payload = json.dumps(fields, sort_keys=True, separators=(",", ":"), default=str).encode()
    secret = (settings.encryption_key or settings.session_secret_key).encode()
    # The input can include secret-backed configuration; a published hash must
    # not allow guessing credentials offline. Key rotation invalidates evidence.
    return hmac.digest(secret, b"llmops-capacity-deployment-v1\0" + payload, "sha256").hex()
