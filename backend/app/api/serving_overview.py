"""Serving home: one row per self-served model showing where it sits in the
recipe → deployment → benchmark → catalog pipeline (Super User only)."""

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_super_user
from app.clients.litellm import LiteLLMClient, get_litellm_client
from app.db.models.custom_benchmark_run import CustomBenchmarkRun
from app.db.models.custom_external_serving import CustomExternalServing
from app.db.models.custom_llmd_stack import CustomLlmdStack
from app.db.models.custom_model_catalog import CustomModelCatalog
from app.db.models.custom_model_deployment import CustomModelDeployment
from app.db.models.custom_serving_recipe import CustomServingRecipe
from app.db.models.custom_user import CustomUser
from app.db.session import get_db
from app.services import llmd_stacks
from app.services.clusters import NamespaceScope, namespace_scopes, scan_scopes
from app.services.external_servings import scan_clusters
from app.services.litellm_registration import litellm_name
from app.services.llmd_links import external_server, link_stacks, portal_server
from app.services.llmd_manifests import llmd_service_name
from app.services.pd_serving import pd_status_summary
from app.services.serving_engines import engine_of
from app.services.serving_topology import (
    NamespaceSnapshots,
    TopologyGraph,
    add_service,
    build_topology,
    read_routes,
    read_workloads,
)

logger = logging.getLogger(__name__)
route_snapshots = NamespaceSnapshots(read_routes)
workload_snapshots = NamespaceSnapshots(read_workloads)

router = APIRouter(prefix="/api/admin/serving", tags=["serving-overview"])

_PERF_KEYS = ("output_throughput", "request_throughput", "mean_ttft_ms", "p99_ttft_ms", "mean_tpot_ms")


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _perf_headline(result) -> dict:
    """Unwrap `metrics` (runner output) and keep the headline numbers."""
    r = result.get("metrics") if isinstance(result, dict) and isinstance(result.get("metrics"), dict) else result
    if not isinstance(r, dict):
        return {}
    return {k: _num(r.get(k)) for k in _PERF_KEYS if _num(r.get(k)) is not None}


def _accuracy_headline(result) -> dict | None:
    """First numeric metric of an accuracy result, preferring an `accuracy` key."""
    if not isinstance(result, dict):
        return None
    if _num(result.get("accuracy")) is not None:
        return {"name": "accuracy", "value": _num(result["accuracy"])}
    for k, v in result.items():
        if _num(v) is not None:
            return {"name": k, "value": _num(v)}
    return None


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def build_overview(
    deployments: list,
    stacks: list,
    runs: list,
    recipes: list,
    catalog: list,
    external_servings: list[dict] | None = None,
    external_registrations: list | None = None,
    stack_status: dict[str, dict] | None = None,
) -> dict:
    """Pure aggregation; `runs` must be succeeded runs sorted newest first.

    llm-d stacks attach to a model row only through their label selector
    (see app.services.llmd_links), never by target name. Stacks whose
    selector picks no known server are returned separately as
    ``unlinked_stacks`` so an operator can see a router pointing at nothing.
    """
    stack_status = stack_status or {}
    reg_by_key = {(r.namespace, r.deployment_name): r.model_name for r in (external_registrations or [])}
    servers = [portal_server(d) for d in deployments] + [
        external_server(sv, reg_by_key.get((sv.get("namespace"), sv.get("deployment_name"))))
        for sv in (external_servings or [])
    ]
    links = link_stacks(stacks, servers)
    stacks_by_model: dict[str, list[dict]] = {}
    unlinked: list[dict] = []
    for st in stacks:
        info = links[str(st.id)]
        entry = {
            "id": str(st.id),
            "name": st.name,
            "cluster_id": str(st.cluster_id) if getattr(st, "cluster_id", None) else None,
            "namespace": getattr(st, "namespace", None),
            "selector": info["selector"],
            "servers": [{"kind": x["kind"], "name": x["name"], "namespace": x["namespace"]} for x in info["servers"]],
            **stack_status.get(str(st.id), {}),
        }
        if not info["servers"]:
            unlinked.append({**entry, "target_model_name": st.target_model_name})
            continue
        for x in info["servers"]:
            if x["model_name"]:
                stacks_by_model.setdefault(x["model_name"], []).append(entry)

    names: set[str] = set()
    names.update(d.model_name for d in deployments)
    names.update(stacks_by_model)
    names.update(r.model_name for r in runs)
    catalog_by_name = {c.model_name: c for c in catalog}
    recipes_by_path: dict[str, list] = {}
    for rc in recipes:
        recipes_by_path.setdefault(rc.model_path, []).append(rc)

    rows = []
    for name in sorted(names):
        deps = [d for d in deployments if d.model_name == name]
        model_paths = {d.model_path for d in deps}
        matched_recipes = [rc for p in model_paths for rc in recipes_by_path.get(p, [])]
        # Recipes a deployment explicitly points at (launched from / captured into)
        # join the row even when their model_path has since diverged.
        linked_ids = {getattr(d, "recipe_id", None) for d in deps} - {None}
        seen = {rc.id for rc in matched_recipes}
        matched_recipes += [rc for rc in recipes if rc.id in linked_ids and rc.id not in seen]
        perf = next((r for r in runs if r.model_name == name and r.kind == "performance"), None)
        acc = next((r for r in runs if r.model_name == name and r.kind == "accuracy"), None)
        cat = catalog_by_name.get(name)
        rows.append(
            {
                "model_name": name,
                "recipes": [{"id": str(rc.id), "name": rc.name, "engine": engine_of(rc)} for rc in matched_recipes],
                "deployments": [
                    {
                        "id": str(d.id),
                        "cluster_id": str(d.cluster_id) if getattr(d, "cluster_id", None) else None,
                        "namespace": getattr(d, "namespace", None),
                        "status": d.status,
                        "ready_replicas": d.ready_replicas,
                        "replicas": d.replicas,
                        "engine": engine_of(d),
                        "litellm_model_id": d.litellm_model_id,
                        "recipe_id": str(d.recipe_id) if getattr(d, "recipe_id", None) else None,
                        "gpu_type": getattr(d, "gpu_type", None),
                        "serving_mode": getattr(d, "serving_mode", None) or "aggregated",
                        "pd_summary": pd_status_summary(getattr(d, "pd_status", None)),
                        "router_stack_id": (str(d.router_stack_id) if getattr(d, "router_stack_id", None) else None),
                    }
                    for d in deps
                ],
                "llmd_stacks": stacks_by_model.get(name, []),
                "performance": (
                    {
                        "run_id": str(perf.id),
                        "tool": perf.tool,
                        "finished_at": _iso(perf.finished_at),
                        **_perf_headline(perf.result),
                    }
                    if perf
                    else None
                ),
                "accuracy": (
                    {
                        "run_id": str(acc.id),
                        "tool": acc.tool,
                        "finished_at": _iso(acc.finished_at),
                        "metric": _accuracy_headline(acc.result),
                    }
                    if acc
                    else None
                ),
                "catalog": (
                    {"id": str(cat.id), "display_name": cat.display_name, "status": cat.status, "visible": cat.visible}
                    if cat
                    else None
                ),
                "litellm_registered": any(d.litellm_model_id for d in deps),
            }
        )
    return {"models": rows, "unlinked_stacks": unlinked}


async def _scan_external(db: AsyncSession, registrations: list, stacks: list) -> list[dict]:
    """Best-effort scan for non-portal servings; [] when nothing is reachable.

    The representative cluster's namespace plus the namespaces registrations and
    llm-d stacks live in (see ``clusters.scan_scopes``).
    """
    try:
        extra = [(r.cluster_id, r.namespace) for r in registrations] + [(s.cluster_id, s.namespace) for s in stacks]
        servings, _errors = await scan_clusters(await scan_scopes(db, extra))
        return servings
    except Exception as e:  # noqa: BLE001 — the board must render without a cluster
        logger.info("serving overview: external scan skipped: %s", e)
        return []


@router.get("/overview")
async def serving_overview(
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    deployments = list((await db.execute(select(CustomModelDeployment))).scalars().all())
    stacks = list((await db.execute(select(CustomLlmdStack))).scalars().all())
    runs = list(
        (
            await db.execute(
                select(CustomBenchmarkRun)
                .where(CustomBenchmarkRun.status == "succeeded")
                .order_by(CustomBenchmarkRun.finished_at.desc())
            )
        )
        .scalars()
        .all()
    )
    recipes = list((await db.execute(select(CustomServingRecipe))).scalars().all())
    catalog = list((await db.execute(select(CustomModelCatalog))).scalars().all())
    registrations = list((await db.execute(select(CustomExternalServing))).scalars().all())
    external = await _scan_external(db, registrations, stacks)
    # ArgoCD sync/health per stack (best-effort, one read each).
    status = {str(st.id): await llmd_stacks.live_status(db, st) for st in stacks}
    return build_overview(deployments, stacks, runs, recipes, catalog, external, registrations, status)


@router.get("/topology")
async def serving_topology(
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
    litellm: LiteLLMClient = Depends(get_litellm_client),
    cluster: str = "",
    ns: str = "",
    summary: bool = False,
) -> dict:
    return await load_topology(db, litellm, cluster, ns, summary)


async def load_topology(db, litellm, cluster, ns, summary, *, permitted: NamespaceScope | None = None):
    async def rows(model):
        query = select(model)
        if permitted:
            query = query.where(model.cluster_id == (permitted.cluster.id if permitted.cluster else None),
                                model.namespace == permitted.namespace)
        return (await db.execute(query)).scalars().all()

    catalog = {} if permitted else dict(
        (await db.execute(select(CustomModelCatalog.model_name, CustomModelCatalog.display_name))).all()
    )
    deps = await rows(CustomModelDeployment)
    external = await rows(CustomExternalServing)
    stacks = await rows(CustomLlmdStack)
    extra = [(r.cluster_id, r.namespace) for r in [*deps, *external, *stacks]]
    owners = [
        {"model_id": d.litellm_model_id, "name": litellm_name(d), "deployment": d.model_name, "ref": str(d.id),
         "cluster_id": str(d.cluster_id) if d.cluster_id else None, "namespace": d.namespace}
        for d in deps
    ] + [
        {"model_id": r.litellm_model_id, "name": r.model_name, "deployment": r.deployment_name,
         "ref": None, "owner_id": str(r.id), "cluster_id": str(r.cluster_id) if r.cluster_id else None,
         "namespace": r.namespace}
        for r in external
    ]
    errors = []
    snapshots = {}

    async def scan(scope):
        try:
            data = await route_snapshots.read(scope)
            if not summary:
                data = {**data, **await workload_snapshots.read(scope)}
            snapshots[scope.cluster_id, scope.namespace] = {**data, "cluster": scope.name}
        except Exception:
            errors.append(f"{scope.name} / {scope.namespace}")

    try:
        scopes = [permitted] if permitted else await namespace_scopes(db, extra, include_registered=True)
    except Exception:
        scopes = []
        errors.append("Kubernetes")
    selected = [s for s in scopes if (not cluster or (s.cluster_id or "default") == cluster)
                and (not ns or s.namespace == ns)]
    live, _ = await asyncio.gather(
        litellm.get_model_info(), asyncio.gather(*(scan(scope) for scope in selected)), return_exceptions=True
    )
    available = not isinstance(live, BaseException)
    if available and permitted:
        owned_ids = {r["model_id"] for r in owners if r["model_id"]}
        live = [r for r in live if (r.get("model_info") or {}).get("id") in owned_ids]
    graph = build_topology(live if available else [], catalog, owners, stacks, snapshots, litellm_available=available)
    return {
        **graph, "litellm_available": available, "unavailable_scopes": sorted(errors),
        "partial_coverage": len(selected) < len(scopes) or bool(errors),
        "scopes": [{"cluster_id": s.cluster_id, "namespace": s.namespace} for s in scopes],
    }


@router.get("/topology/workloads")
async def topology_workloads(
    cluster: str, ns: str, kind: Literal["ingress", "service"], name: str,
    user: CustomUser = Depends(require_super_user), db: AsyncSession = Depends(get_db),
) -> dict:
    # Only serve namespaces the same admin can see in the overview. Resolve the
    # entry from Kubernetes, so callers cannot supply arbitrary pod selectors.
    extra = []
    for model in (CustomModelDeployment, CustomExternalServing, CustomLlmdStack):
        extra.extend((await db.execute(select(model.cluster_id, model.namespace)
                                       .where(model.namespace == ns).distinct())).all())
    scopes = await namespace_scopes(db, extra, include_registered=True)
    scope = next((s for s in scopes if (s.cluster_id or "default") == cluster and s.namespace == ns), None)
    if not scope:
        raise HTTPException(404, "Unknown topology scope")
    return await load_topology_workloads(db, scope, kind, name)


async def load_topology_workloads(db, scope: NamespaceScope, kind, name):
    ns = scope.namespace
    try:
        routes = await route_snapshots.read(scope)
        if kind == "ingress":
            services = {svc for ingress, _, _, _, svc in routes["paths"] if ingress == name}
        else:
            services = {name} if name in routes["services"] else set()
        if not services:
            raise HTTPException(404, "Entry point no longer present")
        workloads = await workload_snapshots.read(scope)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "Kubernetes workload lookup unavailable") from exc
    stacks = (await db.scalars(select(CustomLlmdStack).where(
        CustomLlmdStack.cluster_id == (scope.cluster.id if scope.cluster else None),
        CustomLlmdStack.namespace == ns,
    ))).all()
    routers = {llmd_service_name(st): st for st in stacks}
    graph = TopologyGraph()
    data = {**routes, **workloads, "cluster": scope.name}
    expanded = set()
    for svc in sorted(services):
        add_service(graph, scope.cluster_id, ns, svc, routers.get(svc), data, expanded)
    return graph.result()
