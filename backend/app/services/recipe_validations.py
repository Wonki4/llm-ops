"""Shared portal/MCP validation operations; no GPU work in HTTP requests."""

import json
import uuid
from copy import deepcopy
from datetime import UTC, datetime

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from app.db.models.custom_k8s_cluster import CustomK8sCluster
from app.db.models.custom_recipe_validation import (
    CustomRecipeValidation,
    CustomValidationAttempt,
    CustomValidationCandidate,
)
from app.db.models.custom_serving_recipe import CustomServingRecipe
from app.services import aggregated_router, crypto, pd_router, pd_serving
from app.services.benchmark_credentials import public_snapshot
from app.services.clusters import default_namespace
from app.services.model_deployment_manifests import build_all
from app.services.serving_engines import engine_flags
from app.services.validation_protocol import digest, effective_flag

TERMINAL = {"succeeded", "cancelled", "failed"}


def unpack(candidate):
    return json.loads(crypto.decrypt(candidate.snapshot_encrypted))


def merge_patch(source, patch):
    if not isinstance(patch, dict):
        return deepcopy(patch)
    out = deepcopy(source) if isinstance(source, dict) else {}
    for key, value in patch.items():
        if value is None:
            out.pop(key, None)
        else:
            out[key] = merge_patch(out.get(key), value)
    return out


async def get_job(db, job_id, *, lock=False):
    try:
        query = select(CustomRecipeValidation).where(CustomRecipeValidation.id == uuid.UUID(str(job_id)))
    except ValueError:
        raise HTTPException(404, "Validation job not found")
    if lock:
        query = query.with_for_update()
    job = (await db.execute(query)).scalar_one_or_none()
    if not job:
        raise HTTPException(404, "Validation job not found")
    return job


async def scope(db, cluster_id, namespace):
    cid = uuid.UUID(cluster_id) if cluster_id else None
    if cid and not await db.get(CustomK8sCluster, cid):
        raise HTTPException(404, "Selected cluster no longer exists")
    return cid, namespace or await default_namespace(db, cid)


async def snapshot(db, recipe_id, conditions, cluster_id, namespace, user, *, recipe=None):
    from app.api import model_deployments, serving_recipes
    from app.api.recipe_deployment import DeployTarget, deploy_body

    source = await db.get(CustomServingRecipe, uuid.UUID(str(recipe_id))) if recipe_id else None
    if recipe is None:
        if not source:
            raise HTTPException(404, "Recipe no longer exists")
        recipe = serving_recipes._serialize(source)
    elif set(recipe) - set(serving_recipes.RecipeBody.model_fields):
        raise HTTPException(400, "Unknown recipe fields")
    try:
        recipe = serving_recipes.RecipeBody.model_validate(recipe).model_dump(mode="json")
    except ValidationError as exc:
        raise HTTPException(422, exc.errors(include_input=False, include_context=False, include_url=False)) from exc
    if conditions.search:
        identity = recipe.get("hf_repo_id") or recipe["model_path"]
        allowed = [conditions.search.target_model, *conditions.search.alternative_models]
        if identity not in allowed:
            raise HTTPException(400, "Weights are outside the fixed search scope; start a new job to change models")
    # Freeze defaults and resolve profile/storage bindings in the selected cluster now.
    target = DeployTarget(
        model_name=f"validation-{uuid.uuid4().hex[:20]}",
        cluster_id=cluster_id,
        namespace=namespace,
        ingress_host=conditions.ingress_host,
        ingress_class=conditions.ingress_class,
        gpu_type=conditions.gpu_type,
        replicas=1 if recipe["serving_mode"] != "pd" else None,
    )
    dep = await model_deployments.prepare_deployment(
        db,
        deploy_body({**recipe, "id": str(source.id) if source else None}, target),
        user,
        persist=False,
    )
    pools = [pd_serving.pool_row(dep, role) for role in pd_serving.ROLES] if pd_serving.is_pd(dep) else [dep]
    gpu_count = sum(pool.gpu_count * pool.replicas for pool in pools)
    if gpu_count < 1 or gpu_count > conditions.gpu_limit:
        raise HTTPException(400, f"{recipe['name']}: requires {gpu_count} GPUs; limit is {conditions.gpu_limit}")
    for pool in pools:
        flags = engine_flags(pool)
        if conditions.search:
            context_flag = "--max-model-len" if pool.engine == "vllm" else "--context-length"
            try:
                context = int(effective_flag(flags, context_flag) or "0")
            except ValueError:
                context = 0
            minimum = max(
                conditions.search.min_context_length,
                conditions.performance.random_input_len + conditions.performance.random_output_len,
            )
            if context < minimum:
                raise HTTPException(
                    400, f"Set {context_flag} to at least {minimum}; search cannot lower context requirements"
                )
        if any(effective_flag(flags, name) is not None for name in ("--model", "--model-path")):
            raise HTTPException(400, "Select weights using the recipe model path; model flags cannot override it")
        extra_resources = (pool.runtime or {}).get("extra_resources") or {}
        if pool.gpu_resource_key in extra_resources or any("gpu" in k.lower() for k in extra_resources):
            raise HTTPException(400, "Set GPU allocation using the recipe GPU fields, not extra_resources")
        if effective_flag(flags, "--nnodes") not in (None, "1") or effective_flag(
            flags, "--distributed-executor-backend"
        ) in (
            "external_launcher",
            "ray",
        ):
            raise HTTPException(400, "Multi-node model sharding is not supported; use single-node pools")
    if conditions.prefix_len and (pd_serving.is_pd(dep) or dep.engine != "vllm" or dep.replicas != 1):
        raise HTTPException(400, "Cache counter comparison requires one vLLM Pod; disable it for P/D candidates")
    excluded = {
        "id",
        "created_at",
        "updated_at",
        "last_synced_at",
        "status",
        "status_message",
        "pd_status",
        "router_stack_id",
        "router_stack_created",
        "router_apply_error",
        "litellm_model_id",
        "validation_id",
        "validation_attempt_id",
        "validation_state",
        "service_cluster_ip",
        "ready_replicas",
    }
    fields = {c.name: getattr(dep, c.name) for c in dep.__table__.columns if c.name not in excluded}
    fields = json.loads(json.dumps(fields, default=str))
    if pd_serving.is_pd(dep):
        repo, chart, version = pd_router.router_chart(dep)
        router = fields["pd_config"].setdefault("router", {})
        router.update(chart_repo=repo, chart_name=chart, chart_version=version)
        image = pd_router.epp_image(router)
        router.update(epp_registry=image["registry"], epp_repository=image["repository"], epp_tag=image["tag"])
    if aggregated_router.enabled(dep):
        import yaml

        from app.services.llmd_manifests import deep_merge

        cfg = fields["aggregated_router"]
        repo, chart, version = aggregated_router.chart_source(cfg)
        values = deep_merge(aggregated_router.defaults(), yaml.safe_load(cfg.get("values_yaml") or "{}"))
        fields["aggregated_router"] = {
            "chart_repo": repo,
            "chart_name": chart,
            "chart_version": version,
            "values_yaml": yaml.safe_dump(values),
        }
    if conditions.airgap:
        fields["env"] = {
            **(fields.get("env") or {}),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_DATASETS_OFFLINE": "1",
        }
        if fields.get("pd_config"):
            for role in pd_serving.ROLES:
                pool = fields["pd_config"].get(role) or {}
                pool["env"] = {
                    **(pool.get("env") or {}),
                    "HF_HUB_OFFLINE": "1",
                    "TRANSFORMERS_OFFLINE": "1",
                    "HF_DATASETS_OFFLINE": "1",
                }
                fields["pd_config"][role] = pool
    if fields.get("pd_config"):
        from app.config import settings

        fields["pd_config"]["sidecar_image"] = fields["pd_config"].get("sidecar_image") or settings.llmd_sidecar_image
    manifests = build_all(dep)
    # This is a static plan; actual files/images are checked by CPU-only preflight Jobs.
    checks = [
        {"kind": "placement", "status": "ready", "message": f"{namespace}: {gpu_count} GPUs, sequential candidates"},
        {
            "kind": "assets",
            "status": "pending",
            "message": "Image digest, weights, tokenizer and evaluation assets are checked before GPU allocation",
        },
    ]
    return {
        "recipe": recipe,
        "deployment": fields,
        "gpu_count": gpu_count,
        "checks": checks,
        "manifests": public_snapshot(manifests),
    }


def check_comparability(snapshots, conditions):
    weights = {s["recipe"].get("hf_repo_id") or s["recipe"]["model_path"] for s in snapshots}
    if len(weights) > 1 and not conditions.quality_gates:
        raise HTTPException(400, "Alternative weights require predefined absolute quality floors")
    if conditions.selection_metric in ("output_throughput", "throughput_per_gpu"):
        if conditions.selection_metric == "output_throughput" and len({s["gpu_count"] for s in snapshots}) > 1:
            raise HTTPException(400, "Different GPU counts require an explicit normalized or latency selection metric")
        if len(weights) > 1 and not conditions.tokenizer:
            raise HTTPException(
                400, "Set a shared tokenizer or select request throughput/latency for alternative weights"
            )


def new_candidate(job, snap, position, key, *, recipe_id=None, parent_id=None):
    return CustomValidationCandidate(
        id=uuid.uuid4(),
        validation_id=job.id,
        recipe_id=recipe_id,
        parent_candidate_id=parent_id,
        position=position,
        request_key=key,
        name=snap["recipe"]["name"],
        weights=snap["recipe"].get("hf_repo_id") or snap["recipe"]["model_path"],
        gpu_count=snap["gpu_count"],
        snapshot_encrypted=crypto.encrypt(json.dumps(snap)),
        snapshot_hash=digest(snap),
        status="queued",
    )


async def save_search_winner(db, job, candidate, deployment):
    """Save only the revalidated winner, without overwriting its source recipe."""
    from app.api.serving_recipes import RecipeBody

    recipe_id = uuid.uuid5(job.id, "validated-recipe")
    if not await db.get(CustomServingRecipe, recipe_id):
        recipe = deepcopy(unpack(candidate)["recipe"])
        # Runtime restored these image/revision pins from the measured asset snapshot.
        for field in ("image", "engine_args", "vllm_extra_args", "pd_config", "env", "aggregated_router"):
            recipe[field] = getattr(deployment, field)
        recipe["name"] = f"{candidate.name[:205]} · {job.id}"
        recipe = RecipeBody.model_validate(recipe).model_dump(mode="json")
        db.add(CustomServingRecipe(id=recipe_id, created_by=job.created_by, updated_by=job.created_by, **recipe))
        await db.flush()
    deployment.recipe_id = recipe_id
    job.search_state = {**(job.search_state or {}), "saved_recipe_id": str(recipe_id)}


def serialize_job(job):
    fields = (
        "id",
        "name",
        "model_name",
        "cluster_id",
        "namespace",
        "conditions",
        "search_state",
        "status",
        "phase",
        "message",
        "selected_candidate_id",
        "created_by",
        "created_at",
        "started_at",
        "finished_at",
        "updated_at",
    )
    return {k: _json(getattr(job, k)) for k in fields}


def _json(value):
    if isinstance(value, datetime) and value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return str(value) if isinstance(value, uuid.UUID) else value.isoformat() if hasattr(value, "isoformat") else value


async def detail(db, job):
    from app.api.benchmarks import _serialize
    from app.db.models.custom_benchmark_run import CustomBenchmarkRun

    candidates = list(
        (
            await db.scalars(
                select(CustomValidationCandidate)
                .where(CustomValidationCandidate.validation_id == job.id)
                .order_by(CustomValidationCandidate.position)
            )
        ).all()
    )
    attempts = list(
        (
            await db.scalars(
                select(CustomValidationAttempt)
                .where(CustomValidationAttempt.validation_id == job.id)
                .order_by(CustomValidationAttempt.started_at)
            )
        ).all()
    )
    runs = list(
        (
            await db.scalars(
                select(CustomBenchmarkRun)
                .where(CustomBenchmarkRun.validation_id == job.id)
                .order_by(CustomBenchmarkRun.created_at)
            )
        ).all()
    )
    result = serialize_job(job)
    from app.services.validation_search import progress

    result["search_progress"] = progress(job, candidates)
    result["candidates"] = [
        {
            **{
                k: _json(getattr(c, k))
                for k in (
                    "id",
                    "recipe_id",
                    "parent_candidate_id",
                    "position",
                    "name",
                    "weights",
                    "gpu_count",
                    "status",
                    "assessment",
                    "snapshot_hash",
                )
            },
            "snapshot": public_snapshot(unpack(c)),
            "attempts": [
                {
                    **{
                        k: _json(getattr(a, k))
                        for k in (
                            "id",
                            "number",
                            "purpose",
                            "status",
                            "stage_started_at",
                            "deployment_id",
                            "diagnostics",
                            "analysis",
                            "error_code",
                            "error_message",
                            "started_at",
                            "finished_at",
                            "state",
                        )
                    },
                    "benchmarks": [_serialize(r) for r in runs if r.validation_attempt_id == a.id],
                }
                for a in attempts
                if a.candidate_id == c.id
            ],
        }
        for c in candidates
    ]
    return result
