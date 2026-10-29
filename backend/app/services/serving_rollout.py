"""Rollout and graceful-termination settings for serving Deployments.

Recipes and deployments carry them inside the ``runtime`` JSON blob (P/D pools
inherit the base through ``pd_serving.pool_row``)::

    {"rollout": "surge" | "replace",      # unset = surge
     "termination_grace_seconds": 120,    # unset = 120
     "prestop_sleep_seconds": 10}         # unset = 10; 0 = no preStop hook

``surge`` starts the new pod first (maxSurge 1, maxUnavailable 0): zero downtime
and no capacity loss, but one replica's worth of GPUs must be free during the
rollout. ``replace`` stops an old pod first (maxSurge 0, maxUnavailable 1): no
extra GPUs, capacity drops by one replica meanwhile, and it needs at least two
replicas or the rollout is an outage, which ``check_rollout`` refuses.

The preStop sleep covers the gap between the pod leaving the Service endpoints
and SIGTERM, so in-flight requests are not routed to a pod that is already
shutting down. Pure functions, no I/O.
"""

from __future__ import annotations

from app.services import pd_serving

SURGE = "surge"
REPLACE = "replace"
DEFAULT_TERMINATION_GRACE_SECONDS = 120
DEFAULT_PRESTOP_SLEEP_SECONDS = 10


def mode(runtime: dict | None) -> str:
    return (runtime or {}).get("rollout") or SURGE


def strategy(runtime: dict | None) -> dict:
    """The Deployment ``spec.strategy``: one pod at a time, never ``Recreate``."""
    if mode(runtime) == REPLACE:
        rolling = {"maxSurge": 0, "maxUnavailable": 1}
    else:
        rolling = {"maxSurge": 1, "maxUnavailable": 0}
    return {"type": "RollingUpdate", "rollingUpdate": rolling}


def termination_grace_seconds(runtime: dict | None) -> int:
    value = (runtime or {}).get("termination_grace_seconds")
    return int(value) if value is not None else DEFAULT_TERMINATION_GRACE_SECONDS


def prestop_sleep_seconds(runtime: dict | None) -> int:
    value = (runtime or {}).get("prestop_sleep_seconds")
    return int(value) if value is not None else DEFAULT_PRESTOP_SLEEP_SECONDS


def lifecycle(runtime: dict | None) -> dict | None:
    """The serving container's ``lifecycle`` (a preStop sleep), None when the sleep is 0.

    ``exec sleep`` rather than the ``sleep`` action so clusters before K8s 1.30 work too.
    """
    seconds = prestop_sleep_seconds(runtime)
    if seconds <= 0:
        return None
    return {"preStop": {"exec": {"command": ["sh", "-c", f"sleep {seconds}"]}}}


def _pools(dep) -> list[tuple[str, dict | None, int, int]]:
    """``(what, runtime, replicas, gpu_count)`` per rolled Deployment."""
    if pd_serving.is_pd(dep):
        out = []
        for role in pd_serving.ROLES:
            spec = pd_serving.role_view(dep, role)
            out.append((role, getattr(spec.pool, "runtime", None), spec.replicas, spec.gpu_count))
        return out
    return [("replicas", getattr(dep, "runtime", None), int(dep.replicas or 0), int(dep.gpu_count or 0))]


def check_rollout(dep) -> None:
    """Refuse a ``replace`` rollout that would be an outage (exactly one replica)."""
    for what, runtime, replicas, _ in _pools(dep):
        if mode(runtime) == REPLACE and replicas == 1:
            raise ValueError(f"{what}: rollout 'replace' needs at least 2 replicas (got 1)")


def surge_gpus(dep) -> int:
    """GPUs needed on top of the running set while a rollout is in progress."""
    return sum(gpus for _, runtime, _, gpus in _pools(dep) if mode(runtime) == SURGE)
