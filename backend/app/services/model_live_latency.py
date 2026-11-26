"""Resolve catalog names to serving Pods before querying per-cluster Prometheus."""

import asyncio
import hashlib
import json
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import load_only

from app.config import settings
from app.db.models.custom_external_serving import CustomExternalServing
from app.db.models.custom_kv_observation import CustomKVObservation
from app.db.models.custom_llmd_stack import CustomLlmdStack
from app.db.models.custom_model_deployment import CustomModelDeployment
from app.services import pd_serving
from app.services.cluster_prometheus import PrometheusConfigError, resolve_prometheus_connection
from app.services.clusters import namespace_scopes
from app.services.latency_cache import cached_latency, latency_tick
from app.services.latency_inventory import inventory_source, read_latency_inventory
from app.services.litellm_registration import ingress_api_base, with_v1
from app.services.llmd_links import selector_matches
from app.services.llmd_manifests import llmd_service_name, stack_selector
from app.services.model_deployment_manifests import (
    LABEL_LLMD_MODEL,
    LABEL_MODEL,
    LABEL_OWNER,
    PORTAL_OWNERS,
    k8s_resource_names,
)
from app.services.prometheus_latency import (
    HISTORY_SECONDS,
    HISTORY_STEP_SECONDS,
    METRICS,
    WINDOW_SECONDS,
    read_latency,
    read_latency_history,
)
from app.services.serving_topology import (
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


@dataclass(frozen=True)
class ManagedWorkload:
    engine: str
    deployment_id: uuid.UUID
    role: str


@dataclass
class Pool:
    pods: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    deployments: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    # Only verified, whole Deployments may contribute deleted replicas to history.
    owners: dict[tuple[str, str], str] = field(default_factory=dict)
    has_unverified_pods: bool = False


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
            role = key.removesuffix("_deployment") if key != "deployment" else "aggregated"
            engines[target] = ManagedWorkload(dep.engine, dep.id, role)
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


def _selector(target, routes, stacks):
    if target in stacks:
        return stack_selector(stacks[target].values_snapshot or stacks[target].helm_values or {})
    return routes.get(target.scope, {}).get("services", {}).get(target.name, {})


def _pod_role(labels):
    role = labels.get(pd_serving.LABEL_PD_ROLE) or labels.get(pd_serving.LABEL_ROLE)
    return role if role in pd_serving.ROLES else None


def _pods(target, workloads, routes, stacks, engines):
    selector = _selector(target, routes, stacks)
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
        managed = engines.get(Target(target.scope, deployment, "deployment"))
        engine = workloads["pod_engines"].get(pod) or (managed.engine if managed else "unknown")
        yield pod, engine, _pod_role(labels), deployment or target.name


def _whole_deployments(target, data, routes, stacks, managed):
    """Find whole workloads even at zero replicas. A service selecting one revision stays Pod-scoped."""
    selector = _selector(target, routes, stacks)
    for name, revisions in data.get("deployment_revisions", {}).items():
        owner = managed.get(Target(target.scope, name, "deployment"))
        if target.kind == "deployment" and name != target.name:
            continue
        identities = set()
        for revision in revisions:
            labels = dict(revision["selector"])
            if owner and labels.get(LABEL_OWNER) in PORTAL_OWNERS and labels.get(LABEL_MODEL):
                # Our renderer stamps the immutable model identity under llm-d's routing label as well.
                labels[LABEL_LLMD_MODEL] = labels[LABEL_MODEL]
                if owner.role in pd_serving.ROLES and labels.get(pd_serving.LABEL_PD_ROLE) == owner.role:
                    labels[pd_serving.LABEL_ROLE] = owner.role
            if target.kind == "service" and not (
                selector_matches(selector, labels) and selector_matches(selector, revision["labels"])
            ):
                break
            engine = revision["engine"] or (owner.engine if owner else None)
            identities.add((engine, _pod_role(revision["labels"]), revision["uid"]))
        else:
            if len(identities) == 1:
                engine, role, uid = identities.pop()
                if engine in ("sglang", "vllm"):
                    yield name, engine, role, uid


async def _observed_pod_names(db, groups, managed, now):
    """Re-use this portal's worker-verified Pod → ReplicaSet → Deployment associations."""
    wanted = {}
    for (cid, engine, role), group in groups.items():
        if group.has_unverified_pods:
            continue
        for ns, name in group.owners:
            saved = managed.get(Target((cid, ns), name, "deployment"))
            if saved and saved.engine == engine and saved.role == (role or "aggregated"):
                wanted[saved.deployment_id, saved.role, engine] = (cid, ns, name)
    result = defaultdict(set)
    if wanted:
        row = CustomKVObservation
        records = await db.execute(select(row.deployment_id, row.role, row.engine, row.pod_name).where(
            row.deployment_id.in_({key[0] for key in wanted}),
            row.last_seen >= datetime.fromtimestamp(now - HISTORY_SECONDS - WINDOW_SECONDS, UTC),
        ).distinct())
        for dep_id, role, engine, pod in records:
            if location := wanted.get((dep_id, role, engine)):
                result[location].add(pod)
    return result


def _history_targets(group, cid, managed, known):
    if group.has_unverified_pods or not group.owners:
        return {}
    options = {"deployments": {ns: sorted(names) for ns, names in group.deployments.items()}}
    if all(Target((cid, ns), name, "deployment") in managed for ns, name in group.owners):
        observed = {ns: set(group.pods.get(ns, ())) for ns in group.deployments}
        for ns, name in group.owners:
            observed[ns].update(known.get((cid, ns, name), ()))
        if all(observed.values()):
            options["observed_pods"] = {ns: sorted(names) for ns, names in observed.items()}
    return options


def _blank(status, history):
    if history:
        return {"history": [], "unavailable_metrics": list(METRICS), "status": status}
    return {
        "status": status,
        "last_scraped_at": None,
        "metrics": {key: {"status": status, "p50_ms": None} for key in METRICS},
    }


def _row(cluster_id, cluster_name, deployments, namespaces, engine, pool, shared):
    return {
        "cluster_id": cluster_id,
        "cluster_name": cluster_name,
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
        "scope_source": series.get("scope_source", "current_pods"),
        "unavailable_metrics": unavailable,
        "history": [
            {"at": datetime.fromtimestamp(t, UTC).isoformat(), **values}
            for t, values in sorted(points.items())
        ],
    }


def _connection_digest(url, label, token):
    # Only this fingerprint crosses workers; the actual credential is resolved on a cache miss.
    return hashlib.sha256(json.dumps([url, label, token]).encode()).hexdigest()


async def _resolve_targets(db, litellm, name, now):
    registrations = await read_latency_inventory(db, litellm, now=now)
    selected = [r for r in registrations if r.get("model_name") == name]
    plan = {"targets": [], "unmapped_count": 0}
    if not selected or all(r["is_cloud"] for r in selected):
        plan["unmapped_count"] = len(selected)
        return plan
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
    cluster_names = {s.cluster_id: s.name for s in scopes}
    route_results = await asyncio.gather(*(_snapshot(route_snapshots, s) for s in scopes))
    routes = {key: data for key, data in zip(by_scope, route_results) if data}
    owners, engines = _registered_targets(managed, external)
    incomplete = len(routes) != len(scopes) or any(r.get("unavailable") for r in routes.values())
    aliases, unmapped = _map_registrations(registrations, routes, owners, incomplete=incomplete)
    plan["unmapped_count"] = unmapped[name]
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

    groups = defaultdict(Pool)
    for target in sorted(targets, key=str):
        if target.scope not in by_scope:
            plan["unmapped_count"] += 1
            continue
        cid, ns = target.scope
        conn, data = connections[cid], observations.get(target.scope)
        if conn is None or conn == "misconfigured" or data is None:
            status = "unconfigured" if conn is None else ("misconfigured" if conn == "misconfigured" else "error")
            row = _row(cid, cluster_names[cid], [target.name], [ns], None, None, False)
            plan["targets"].append({"row": row, "status": status})
            continue
        pods = list(_pods(target, data, routes, stacks, engines))
        whole = set(_whole_deployments(target, data, routes, stacks, engines))
        for deployment, engine, role, uid in whole:
            group = groups[cid, engine, role]
            group.owners[ns, deployment] = uid
            group.deployments[ns].add(deployment)
        if not pods and not whole:
            row = _row(cid, cluster_names[cid], [target.name], [ns], None, None, False)
            plan["targets"].append({"row": row, "status": "missing"})
        for pod, engine, role, deployment in pods:
            group = groups[cid, engine, role]
            group.pods[ns].add(pod)
            group.deployments[ns].add(deployment)
            owner = (data["pod_owners"].get(pod) or {}).get("deployment")
            if not owner or (deployment, engine, role, owner[0]) not in whole:
                group.has_unverified_pods = True
    known = await _observed_pod_names(db, groups, engines, now)
    for (cid, engine, role), group in groups.items():
        shared = any(len(selected_aliases[(cid, ns), p]) > 1 for ns, pods in group.pods.items() for p in pods)
        names = set.union(*group.deployments.values())
        row = _row(cid, cluster_names[cid], names, group.deployments, engine, role, shared)
        conn = connections[cid]
        plan["targets"].append({
            "row": row,
            "connection": _connection_digest(conn.url, conn.cluster_label, conn.token),
            # Zero-replica namespaces still contribute to the historical membership query.
            "namespaces": sorted(group.pods or group.deployments),
            "pods": {ns: sorted(pods) for ns, pods in group.pods.items()},
            "history": _history_targets(group, cid, engines, known),
        })
    await db.commit()
    return plan


async def _read_plan(db, plan, history, now):
    response = {"rows": [], "unmapped_count": plan["unmapped_count"], "window_seconds": WINDOW_SECONDS}
    if history:
        response.update(
            end_at=datetime.fromtimestamp(now, UTC).isoformat(),
            step_seconds=HISTORY_STEP_SECONDS,
            history_seconds=HISTORY_SECONDS,
        )
    else:
        response.update(evaluated_at=datetime.fromtimestamp(now, UTC).isoformat(), refresh_seconds=15)

    connections = {}
    queries = []
    for target in plan["targets"]:
        row = target["row"]
        cid = row["cluster_id"]
        status = target.get("status")
        if not status and (row["engine"] not in ("vllm", "sglang") or (not target["pods"] and not history)):
            status = "missing"
        if not status:
            if cid not in connections:
                try:
                    connections[cid] = await resolve_prometheus_connection(db, cid)
                except PrometheusConfigError:
                    connections[cid] = None
            conn = connections[cid]
            # A concurrent config edit can occur between discovering the pool and preparing a query.
            if conn and _connection_digest(conn.url, conn.cluster_label, conn.token) == target["connection"]:
                queries.append((target, conn))
                continue
            status = "misconfigured"
        response["rows"].append({**row, **_blank(status, history)})

    # Do not hold the portal connection while waiting for Prometheus or publishing a shared result.
    await db.commit()
    if queries:
        read_metrics = read_latency_history if history else read_latency
        async with httpx.AsyncClient() as client:
            async def measure(target, conn):
                row = target["row"]
                ns, *others = target["namespaces"]
                pods = target["pods"]
                metrics = await read_metrics(
                    client, conn.url, namespace=ns, pods=pods.get(ns, []), engine=row["engine"],
                    cluster_label=conn.cluster_label, bearer_token=conn.token, now=now,
                    additional_targets=[(n, pods.get(n, [])) for n in others],
                    allow_model_aliases_across_pods=True, **(target["history"] if history else {}),
                )
                return {**row, **(_history_row(metrics) if history else metrics)}

            response["rows"].extend(await asyncio.gather(*(measure(target, conn) for target, conn in queries)))
    response["rows"].sort(key=lambda r: (r["cluster_name"], r["namespace"], r["deployment_name"], r["pool"] or ""))
    return response


async def model_live_latency(db, litellm, name, *, history=False, config_epoch=None):
    # The endpoint already checked this user's access. Release its transaction before sharing work.
    await db.commit()
    evaluated = time.time()
    routing_tick = latency_tick(evaluated, history=False)
    now = latency_tick(evaluated, history=history)
    scope = {
        "model": name, "config": config_epoch, "inventory": inventory_source(litellm),
        "portal_scope": [settings.kubeconfig_path, settings.k8s_default_namespace],
        "prometheus": _connection_digest(
            settings.prometheus_url,
            [settings.prometheus_shared, settings.prometheus_cluster_label, settings.prometheus_cluster_value],
            settings.prometheus_bearer_token.get_secret_value(),
        ),
    }

    async def load_result():
        plan = await cached_latency(
            db, scope={**scope, "kind": "deployment-pools"}, history=False, now=routing_tick,
            load=lambda: _resolve_targets(db, litellm, name, routing_tick),
        )
        return await _read_plan(db, plan, history, now)

    return await cached_latency(
        db, scope={**scope, "kind": "model-latency"},
        history=history, now=now, load=load_result,
    )
