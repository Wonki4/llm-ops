"""Serving home: one row per self-served model showing where it sits in the
recipe → deployment → benchmark → catalog pipeline (Super User only)."""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_super_user
from app.clients.k8s import K8sClient
from app.db.models.custom_benchmark_run import CustomBenchmarkRun
from app.db.models.custom_external_serving import CustomExternalServing
from app.db.models.custom_k8s_cluster import CustomK8sCluster
from app.db.models.custom_llmd_stack import CustomLlmdStack
from app.db.models.custom_model_catalog import CustomModelCatalog
from app.db.models.custom_model_deployment import CustomModelDeployment
from app.db.models.custom_serving_recipe import CustomServingRecipe
from app.db.models.custom_user import CustomUser
from app.db.session import get_db
from app.services.clusters import k8s_for_cluster
from app.services.external_servings import scan_clusters
from app.services.llmd_links import external_server, link_stacks, portal_server
from app.services.serving_engines import engine_of

logger = logging.getLogger(__name__)

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
                        "status": d.status,
                        "ready_replicas": d.ready_replicas,
                        "replicas": d.replicas,
                        "engine": engine_of(d),
                        "litellm_model_id": d.litellm_model_id,
                        "recipe_id": str(d.recipe_id) if getattr(d, "recipe_id", None) else None,
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


async def _scan_external(db: AsyncSession) -> list[dict]:
    """Best-effort cluster scan for non-portal servings; [] when nothing is reachable."""
    try:
        targets: list = [(None, "default", K8sClient())]
        for row in (await db.execute(select(CustomK8sCluster))).scalars().all():
            targets.append((str(row.id), row.name, await k8s_for_cluster(db, row.id)))
        servings, _errors = await scan_clusters(targets)
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
    external = await _scan_external(db)
    # ArgoCD sync/health per stack (best-effort, one read each).
    from app.api.llmd import _live_status  # local import: avoids a circular import at module load

    status = {str(st.id): await _live_status(db, st) for st in stacks}
    return build_overview(deployments, stacks, runs, recipes, catalog, external, registrations, status)
