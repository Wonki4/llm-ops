"""Bounded Kubernetes diagnostics for one portal-managed workload or router Service."""

from kubernetes_asyncio import client
from kubernetes_asyncio.client.exceptions import ApiException

from app.clients.k8s import K8sClient


async def _events(core, namespace, uid):
    try:
        events = await core.list_namespaced_event(
            namespace,
            field_selector=f"involvedObject.uid={uid}",
            limit=30,
            _request_timeout=5,
        )
    except ApiException as exc:
        return {"error": f"Kubernetes events unavailable (HTTP {exc.status})"}
    return [{"reason": e.reason, "type": e.type, "message": (e.message or "")[:1000]} for e in events.items]


async def _container(core, namespace, pod_name, status, tail_lines):
    state = status.state
    reason = state.waiting or state.terminated
    previous = status.last_state and status.last_state.terminated
    result = {
        "name": status.name,
        "ready": status.ready,
        "restarts": status.restart_count,
        "reason": reason.reason if reason else "Running",
        "message": reason.message if reason else None,
        "last_exit_reason": previous.reason if previous else None,
        "last_exit_code": previous.exit_code if previous else None,
    }
    for prior in [False, True] if status.restart_count else [False]:
        field = "previous_log" if prior else "current_log"
        try:
            result[field] = await core.read_namespaced_pod_log(
                pod_name,
                namespace,
                container=status.name,
                previous=prior,
                tail_lines=tail_lines,
                limit_bytes=4096,
                _request_timeout=5,
            )
        except ApiException as exc:
            result[field] = {"error": f"Log unavailable (HTTP {exc.status})"}
    return result


async def workload_diagnostics(k8s: K8sClient, namespace: str, name: str, *, router: bool, tail_lines: int):
    async with await k8s._api_client() as api:
        core = client.CoreV1Api(api)
        if router:
            workload = await core.read_namespaced_service(name, namespace)
            labels = workload.spec.selector
        else:
            workload = await client.AppsV1Api(api).read_namespaced_deployment(name, namespace)
            labels = workload.spec.selector.match_labels
        result = {
            "name": name,
            "namespace": namespace,
            "events": await _events(core, namespace, workload.metadata.uid),
            "pods": [],
        }
        if not labels:
            return result
        selector = ",".join(f"{key}={value}" for key, value in labels.items())
        pods = await core.list_namespaced_pod(namespace, label_selector=selector, _request_timeout=10)
        # Prefer failing pods from the newest rollout over healthy or terminating old replicas.
        ordered = sorted(
            pods.items,
            key=lambda p: (
                p.metadata.deletion_timestamp is None,
                not any(c.type == "Ready" and c.status == "True" for c in (p.status.conditions or [])),
                p.metadata.creation_timestamp,
            ),
            reverse=True,
        )
        result["pod_count"] = len(ordered)
        for pod in ordered[:3]:
            states = (pod.status.init_container_statuses or []) + (pod.status.container_statuses or [])
            result["pods"].append(
                {
                    "name": pod.metadata.name,
                    "phase": pod.status.phase,
                    "conditions": [
                        {"type": c.type, "reason": c.reason, "message": c.message}
                        for c in (pod.status.conditions or [])
                        if c.status != "True"
                    ],
                    "events": await _events(core, namespace, pod.metadata.uid),
                    "containers": [await _container(core, namespace, pod.metadata.name, s, tail_lines) for s in states],
                }
            )
        return result
