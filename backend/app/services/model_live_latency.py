"""Resolve catalog names to serving Pods before querying per-cluster Prometheus."""

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import load_only

from app.db.models.custom_external_serving import CustomExternalServing
from app.db.models.custom_llmd_stack import CustomLlmdStack
from app.db.models.custom_model_deployment import CustomModelDeployment
from app.services import pd_serving
from app.services.cluster_prometheus import PrometheusConfigError, resolve_prometheus_connection
from app.services.clusters import namespace_scopes
from app.services.litellm_registration import ingress_api_base, with_v1
from app.services.llmd_links import selector_matches
from app.services.llmd_manifests import llmd_service_name, stack_selector
from app.services.model_deployment_manifests import k8s_resource_names
from app.services.prometheus_latency import (
    HISTORY_SECONDS,
    HISTORY_STEP_SECONDS,
    METRICS,
    WINDOW_SECONDS,
    read_latency,
    read_latency_history,
)
from app.services.serving_topology import (
    _cloud_provider,
    _endpoint,
    _route_indexes,
    _route_targets,
    route_snapshots,
    workload_snapshots,
)


@dataclass(frozen=True)
class Target:
    scope: tuple[str | None, str]
    name: str
    kind: str  # Kubernetes Service (including llm-d), or registered Deployment


def _scope(row):
    return (str(row.cluster_id) if row.cluster_id else None, row.namespace)


def _base(raw):
    url = _endpoint(raw)
    return url.geturl().rstrip("/") if url else None


def _registered_targets(managed, external):
    by_id = defaultdict(list)
    engines = {}
    for dep in managed:
        resources = k8s_resource_names(dep)
        saved_base = _endpoint(dep.litellm_api_base)
        for key, name in resources.items():
            if not key.endswith("deployment"):
                continue
            target = Target(_scope(dep), name, "deployment")
            engines[target] = dep.engine
            if not dep.litellm_model_id:
                continue
            # Only pin a registration at the physical ingress or its own Service.
            # A free-form API-base override can point at a different cluster.
            if not dep.router_stack_id:
                by_id[dep.litellm_model_id].append((_base(with_v1(ingress_api_base(dep))), target))
            service = resources.get(key.replace("deployment", "service"))
            if (
                service
                and saved_base
                and saved_base.hostname.rstrip(".")
                in (
                    f"{service}.{dep.namespace}.svc",
                    f"{service}.{dep.namespace}.svc.cluster.local",
                )
            ):
                by_id[dep.litellm_model_id].append((_base(dep.litellm_api_base), target))
    for ext in external:
        by_id[ext.litellm_model_id].append(
            (_base(ext.api_base), Target(_scope(ext), ext.deployment_name, "deployment"))
        )
    return by_id, engines


def _map_registrations(registrations, routes, owners, *, incomplete=False):
    by_host, by_service = _route_indexes(routes)
    targets = defaultdict(set)
    unmapped = defaultdict(int)
    for row in registrations:
        alias = row.get("model_name")
        params, info = row.get("litellm_params") or {}, row.get("model_info") or {}
        url = _endpoint(params.get("api_base"))
        mapped = set()
        if alias and url:
            # Use the live upstream (including out-of-band LiteLLM edits).
            matches = _route_targets(url, by_host, by_service)
            services = {Target((cid, ns), svc, "service") for cid, ns, _, svc, _ in matches}
            recorded = {t for base, t in owners.get(info.get("id"), []) if base == _base(url.geturl())}
            locations = {t.scope for t in recorded}
            if len(locations) == 1:
                # A registered ID at its verified URL pins the cluster even if a
                # different cluster advertises exactly the same service or ingress.
                matching = {s for s in services if s.scope in locations}
                if len(matching) == 1:
                    mapped = matching
                elif not matching and (
                    not services or any(scope not in routes or routes[scope].get("unavailable") for scope in locations)
                ):
                    mapped = recorded
            elif not locations and len(services) == 1 and not incomplete:
                mapped = services
        targets[alias].update(mapped)
        if not mapped:
            unmapped[alias] += 1
    return targets, unmapped


async def _snapshot(reader, scope):
    try:
        return await reader.read(scope)
    except Exception:
        return None


def _pods(target, workloads, routes, stacks, engines):
    scope = target.scope
    spec = routes.get(scope, {})
    selector = spec.get("services", {}).get(target.name, {})
    if target in stacks:
        selector = stack_selector(stacks[target].values_snapshot or stacks[target].helm_values or {})
    for pod, labels, phase, _, terminating in workloads.get("pods", ()):
        if terminating or phase != "Running":
            continue
        owner = (workloads["pod_owners"].get(pod) or {}).get("deployment")
        deployment = owner[1] if owner else None
        if target.kind == "deployment":
            if deployment != target.name:
                continue
        elif not selector_matches(selector, labels):
            continue
        engine = workloads["pod_engines"].get(pod) or engines.get(Target(scope, deployment, "deployment"))
        role = labels.get(pd_serving.LABEL_PD_ROLE) or labels.get(pd_serving.LABEL_ROLE)
        yield pod, engine or "unknown", role if role in pd_serving.ROLES else None, deployment or target.name


def _blank(status, history):
    if history:
        return {"history": [], "unavailable_metrics": list(METRICS), "status": status}
    return {
        "status": status,
        "last_scraped_at": None,
        "metrics": {key: {"status": status, "p50_ms": None} for key in METRICS},
    }


def _row(scope, deployments, namespaces, engine, pool, shared):
    return {
        "cluster_id": scope.cluster_id,
        "cluster_name": scope.name,
        "namespace": ", ".join(sorted(namespaces)),
        "deployment_name": ", ".join(sorted(deployments)),
        "shared": shared,
        "engine": engine,
        "pool": pool,
    }


def _history_row(series):
    points = {}
    unavailable = []
    for metric in METRICS:
        if series[metric] is None:
            unavailable.append(metric)
            continue
        for timestamp, value, *bound in series[metric]:
            point = points.setdefault(timestamp, dict.fromkeys(METRICS))
            point[metric] = value
            if bound:
                point.setdefault("lower_bounds_ms", {})[metric] = bound[0]
    return {
        "unavailable_metrics": unavailable,
        "history": [
            {"at": datetime.fromtimestamp(t, UTC).isoformat(), **values}
            for t, values in sorted(points.items())
        ],
    }


async def model_live_latency(db, litellm, name, *, history=False):
    now = time.time()
    response = {"rows": [], "unmapped_count": 0, "window_seconds": WINDOW_SECONDS}
    if history:
        end = int(now) // HISTORY_STEP_SECONDS * HISTORY_STEP_SECONDS
        response.update(
            end_at=datetime.fromtimestamp(end, UTC).isoformat(),
            step_seconds=HISTORY_STEP_SECONDS,
            history_seconds=HISTORY_SECONDS,
        )
    else:
        response.update(evaluated_at=datetime.fromtimestamp(now, UTC).isoformat(), refresh_seconds=15)
    registrations = await litellm.get_model_info()
    selected = [r for r in registrations if r.get("model_name") == name]
    # Public provider domains and provider-only registrations cannot identify a
    # cluster. An API entered via the external form can still be a known Ingress.
    if not selected or all(
        _cloud_provider(r.get("litellm_params") or {}, {}, _endpoint((r.get("litellm_params") or {}).get("api_base")))
        for r in selected
    ):
        return {**response, "unmapped_count": len(selected)}
    dep = CustomModelDeployment
    managed = (
        await db.scalars(
            select(dep)
            .options(
                load_only(
                    dep.cluster_id,
                    dep.namespace,
                    dep.model_name,
                    dep.engine,
                    dep.serving_mode,
                    dep.aggregated_router,
                    dep.litellm_model_id,
                    dep.litellm_api_base,
                    dep.router_stack_id,
                    dep.ingress_host,
                    dep.ingress_path,
                )
            )
            .where(dep.validation_state.is_distinct_from("retired"))
        )
    ).all()
    external = (await db.scalars(select(CustomExternalServing))).all()
    stack_rows = (await db.scalars(select(CustomLlmdStack))).all()
    scopes = await namespace_scopes(
        db, {_scope(row) for row in [*managed, *external, *stack_rows]}, include_registered=True
    )
    by_scope = {(s.cluster_id, s.namespace): s for s in scopes}
    route_results = await asyncio.gather(*(_snapshot(route_snapshots, s) for s in scopes))
    routes = {key: data for key, data in zip(by_scope, route_results) if data}
    owners, engines = _registered_targets(managed, external)
    incomplete = len(routes) != len(scopes) or any(r.get("unavailable") for r in routes.values())
    aliases, unmapped = _map_registrations(registrations, routes, owners, incomplete=incomplete)
    response["unmapped_count"] = unmapped[name]
    targets = aliases[name]
    connections = {}
    for cid in {t.scope[0] for t in targets if t.scope in by_scope}:
        try:
            cluster = next(s.cluster for s in scopes if s.cluster_id == cid)
            connections[cid] = await resolve_prometheus_connection(db, cluster.id if cluster else None)
        except PrometheusConfigError:
            connections[cid] = "misconfigured"

    enabled = sorted(
        {t.scope for t in targets if t.scope in by_scope and connections[t.scope[0]] not in (None, "misconfigured")},
        key=str,
    )
    observations = dict(
        zip(enabled, await asyncio.gather(*(_snapshot(workload_snapshots, by_scope[key]) for key in enabled)))
    )
    stacks = {Target(_scope(s), llmd_service_name(s), "service"): s for s in stack_rows}
    selected_aliases = defaultdict(set)
    for alias, linked in aliases.items():
        for target in linked:
            if data := observations.get(target.scope):
                for pod, *_ in _pods(target, data, routes, stacks, engines):
                    selected_aliases[target.scope, pod].add(alias)

    groups = defaultdict(lambda: defaultdict(set))
    deployments = defaultdict(set)
    for target in sorted(targets, key=str):
        if target.scope not in by_scope:
            response["unmapped_count"] += 1
            continue
        cid, ns = target.scope
        scope = by_scope[target.scope]
        conn, data = connections[cid], observations.get(target.scope)
        if conn is None or conn == "misconfigured" or data is None:
            status = "unconfigured" if conn is None else ("misconfigured" if conn == "misconfigured" else "error")
            response["rows"].append({**_row(scope, [target.name], [ns], None, None, False), **_blank(status, history)})
            continue
        pods = list(_pods(target, data, routes, stacks, engines))
        if not pods:
            response["rows"].append(
                {**_row(scope, [target.name], [ns], None, None, False), **_blank("missing", history)}
            )
        for pod, engine, role, deployment in pods:
            key = (cid, engine, role)
            groups[key][ns].add(pod)
            deployments[key].add(deployment)
    read_metrics = read_latency_history if history else read_latency
    async with httpx.AsyncClient() as client:

        async def measure(key):
            cid, engine, role = key
            conn = connections[cid]
            namespaces = groups[key]
            ns, *others = sorted(namespaces)
            shared = any(len(selected_aliases[(cid, n), p]) > 1 for n, pods in namespaces.items() for p in pods)
            row = _row(by_scope[cid, ns], deployments[key], namespaces, engine, role, shared)
            if engine not in ("vllm", "sglang"):
                return {**row, **_blank("missing", history)}
            metrics = await read_metrics(
                client,
                conn.url,
                namespace=ns,
                pods=sorted(namespaces[ns]),
                engine=engine,
                cluster_label=conn.cluster_label,
                bearer_token=conn.token,
                now=now,
                additional_targets=[(n, sorted(namespaces[n])) for n in others],
                allow_model_aliases_across_pods=True,
            )
            return {**row, **(_history_row(metrics) if history else metrics)}

        response["rows"].extend(await asyncio.gather(*(measure(key) for key in groups)))
    response["rows"].sort(key=lambda r: (r["cluster_name"], r["namespace"], r["deployment_name"], r["pool"] or ""))
    return response
