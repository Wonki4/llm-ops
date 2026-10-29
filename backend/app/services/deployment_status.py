"""Coarse-grained K8s Deployment status classification.

Shared by the reconciler (portal-managed deployments) and the external
serving discovery endpoint so both report identical status strings.
"""


def classify(observed: dict, desired_replicas: int) -> tuple[str, str | None]:
    """Return (status, message) from a K8s deployment status payload.

    ``observed`` = {"ready": int, "available": int, "conditions": [...]} as
    produced by K8sClient.read_deployment_status / list_deployments. When
    the rollout fields are present too (``updated``, ``total``, ``generation``,
    ``observed_generation``) a rollout in progress is "Updating" even while
    the old pods keep the deployment fully Ready — the same checks as
    ``kubectl rollout status``.
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

    generation, observed_generation = observed.get("generation"), observed.get("observed_generation")
    if generation is not None and observed_generation is not None and observed_generation < generation:
        return "Updating", "Waiting for the rollout to start"
    updated, total = observed.get("updated"), observed.get("total")
    if updated is not None:
        if updated < desired_replicas:
            return "Updating", f"{updated}/{desired_replicas} pods updated"
        if total is not None and total > updated:
            old = total - updated
            return "Updating", f"{old} old pod{'s' if old != 1 else ''} still running"

    if ready >= desired_replicas and available >= desired_replicas:
        return "Ready", None
    if ready == 0:
        return "Pending", "No ready pods yet"
    return "Updating", f"{ready}/{desired_replicas} pods ready"
