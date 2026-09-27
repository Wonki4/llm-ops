"""Serving home: one row per self-served model showing where it sits in the
recipe → deployment → benchmark → catalog pipeline (Super User only)."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_super_user
from app.db.models.custom_benchmark_run import CustomBenchmarkRun
from app.db.models.custom_llmd_stack import CustomLlmdStack
from app.db.models.custom_model_catalog import CustomModelCatalog
from app.db.models.custom_model_deployment import CustomModelDeployment
from app.db.models.custom_serving_recipe import CustomServingRecipe
from app.db.models.custom_user import CustomUser
from app.db.session import get_db
from app.services.serving_engines import engine_of

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
) -> list[dict]:
    """Pure aggregation; `runs` must be succeeded runs sorted newest first."""
    names: set[str] = set()
    names.update(d.model_name for d in deployments)
    names.update(s.target_model_name for s in stacks)
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
                    }
                    for d in deps
                ],
                "llmd_stacks": [{"id": str(s.id), "name": s.name} for s in stacks if s.target_model_name == name],
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
    return rows


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
    return {"models": build_overview(deployments, stacks, runs, recipes, catalog)}
