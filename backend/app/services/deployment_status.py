"""Coarse-grained K8s Deployment status classification.

Shared by the reconciler (portal-managed deployments) and the external
serving discovery endpoint so both report identical status strings.
"""


def classify(observed: dict, desired_replicas: int) -> tuple[str, str | None]:
    """Return (status, message) from a K8s deployment status payload.

    ``observed`` = {"ready": int, "available": int, "conditions": [...]} as
    produced by K8sClient.read_deployment_status / list_deployments_all.
    """
    ready = observed.get("ready", 0)
    available = observed.get("available", 0)
    conditions = observed.get("conditions", [])

    progressing_failed = any(
        c.get("type") == "Progressing"
        and c.get("status") == "False"
        and c.get("reason") in ("ProgressDeadlineExceeded",)
        for c in conditions
    )
    if progressing_failed:
        return "Failed", "Deployment progress deadline exceeded"

    replica_failure = any(c.get("type") == "ReplicaFailure" and c.get("status") == "True" for c in conditions)
    if replica_failure:
        msg = next((c.get("message") for c in conditions if c.get("type") == "ReplicaFailure"), None)
        return "Unhealthy", msg or "ReplicaFailure condition true"

    if desired_replicas == 0:
        return "Stopped", "replicas set to 0"

    if ready >= desired_replicas and available >= desired_replicas:
        return "Ready", None
    if ready == 0:
        return "Pending", "No ready pods yet"
    return "Updating", f"{ready}/{desired_replicas} pods ready"
