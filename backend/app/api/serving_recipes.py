"""Serving recipe CRUD (Super User only). Reusable vLLM/SGLang serving templates."""

import re
import uuid

import yaml
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.model_deployments import _apply_pool_profiles, pvc_warnings
from app.auth.deps import require_super_user
from app.config import settings
from app.db.models.custom_k8s_cluster import CustomK8sCluster
from app.db.models.custom_model_deployment import CustomModelDeployment
from app.db.models.custom_serving_recipe import CustomServingRecipe
from app.db.models.custom_user import CustomUser
from app.db.session import get_db
from app.services import aggregated_router, llmd_stacks, pd_router
from app.services.aggregated_router import RouterOption
from app.services.clusters import default_namespace, validate_namespace
from app.services.common_labels import load_deployment_copy
from app.services.gpu_profile_store import UnknownGpuTypeError, resolve_gpu_type
from app.services.gpu_profiles import apply_profile, validate_profile_name
from app.services.kv_capacity import Scenario
from app.services.manifest_diff import PlainDumper
from app.services.model_deployment_manifests import build_all
from app.services.pd_serving import ServingMode, check_kv_events, is_pd, validate_pd_config, validate_runtime
from app.services.recipe_capacity import deployment_estimate
from app.services.recipe_throughput_history import matching_recipe_benchmark
from app.services.serving_engines import ServingEngine, validate_engine_args, validate_port_flag
from app.services.serving_probes import validate_probes
from app.services.storage_fields import recipe_warnings, reject_legacy_pvc
from app.services.storage_mounts import all_entries, validate_entries
from app.services.storage_store import UnknownStorageError, check_known, resolve_entries, resolve_pool_storages

router = APIRouter(prefix="/api/admin/serving-recipes", tags=["serving-recipes"])

_REPO_SEGMENT = r"[A-Za-z0-9_][A-Za-z0-9._-]*"
_REPO_ID = re.compile(rf"{_REPO_SEGMENT}/{_REPO_SEGMENT}")


def repo_id_from_path(path: str) -> str | None:
    return path if len(path) <= 256 and _REPO_ID.fullmatch(path) else None


class RecipeBody(BaseModel):
    name: str
    description: str | None = None
    hf_repo_id: str | None = Field(None, max_length=256, description="Model repository in owner/model format")
    model_path: str
    image: str
    gpu_count: int = Field(1, ge=0)
    gpu_resource_key: str = "nvidia.com/gpu"
    cpu_request: str | None = None
    cpu_limit: str | None = None
    memory_request: str | None = None
    memory_limit: str | None = None
    node_selector: dict | None = None
    tolerations: list | None = None
    storages: list[dict] = Field(default_factory=list)
    vllm_extra_args: list[str] | None = None
    env: dict[str, str] | None = None
    engine: ServingEngine = "vllm"
    engine_args: dict[str, str | int | float | bool] | None = None
    probes: dict | None = None
    gpu_type: str | None = None  # GPU profile name, resolved per cluster at deploy time
    serving_mode: ServingMode = "aggregated"
    aggregated_router: RouterOption = None
    pd_config: dict | None = None  # per-role overrides etc. (pd only; see pd_serving)
    runtime: dict | None = None  # shm / hostIPC / privileged / extra resources

    @field_validator("hf_repo_id")
    @classmethod
    def _check_repo_id(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        if value and not repo_id_from_path(value):
            raise ValueError("Use a Hugging Face repository ID in owner/model format")
        return value or None

    @model_validator(mode="before")
    @classmethod
    def _no_legacy_pvc(cls, data):
        return reject_legacy_pvc(data)

    @field_validator("storages")
    @classmethod
    def _check_storages(cls, v: list[dict]) -> list[dict]:
        return validate_entries(v)

    @field_validator("gpu_type")
    @classmethod
    def _check_gpu_type(cls, v: str | None) -> str | None:
        return validate_profile_name(v) if v and v.strip() else None

    @field_validator("runtime")
    @classmethod
    def _check_runtime(cls, v: dict | None) -> dict | None:
        return validate_runtime(v)

    @model_validator(mode="after")
    def _check_pd(self):
        aggregated_router.validate_config(self.aggregated_router, self.serving_mode)
        if self.serving_mode == "pd":
            self.pd_config = validate_pd_config(
                self.pd_config, engine=self.engine, base_extra_args=self.vllm_extra_args
            )
        else:
            self.pd_config = None
        return self

    @model_validator(mode="after")
    def _check_kv_events(self):
        check_kv_events(self)
        return self

    @field_validator("vllm_extra_args")
    @classmethod
    def _check_port_flag(cls, v: list[str] | None) -> list[str] | None:
        return validate_port_flag(v)

    @field_validator("engine_args")
    @classmethod
    def _check_engine_args(cls, v: dict | None) -> dict | None:
        return validate_engine_args(v)

    @field_validator("probes")
    @classmethod
    def _check_probes(cls, v: dict | None) -> dict | None:
        return validate_probes(v)


class CreateRecipeBody(RecipeBody):
    # Deployment this recipe was captured from. When set, the deployment gets
    # recipe_id = the new recipe unless it already points at one (a deployment
    # launched from recipe A keeps A even if someone snapshots it into B).
    source_deployment_id: str | None = None


class CapacityScenario(BaseModel):
    context_tokens: int = Field(32768, ge=1, le=100_000_000)
    requests: int = Field(1, ge=1, le=1_000_000)
    shared_prefix_tokens: int = Field(0, ge=0)

    @model_validator(mode="after")
    def check_prefix(self):
        if self.shared_prefix_tokens > self.context_tokens:
            raise ValueError("shared prefix exceeds request length")
        return self

    def scenario(self) -> Scenario:
        return Scenario(self.context_tokens, self.requests, self.shared_prefix_tokens)


class RecipeTarget(BaseModel):
    cluster_id: uuid.UUID | None = None  # None = portal default cluster
    namespace: str | None = None  # None/blank = the cluster's default namespace

    @field_validator("cluster_id", mode="before")
    @classmethod
    def _default_cluster(cls, value):
        return None if value in ("", "default") else value

    @field_validator("namespace")
    @classmethod
    def _check_namespace(cls, v: str | None) -> str | None:
        return validate_namespace(v)


class PreviewRecipeBody(RecipeBody, RecipeTarget):
    """An unsaved recipe plus the deploy target the preview renders it for."""

    name: str = ""
    capacity_scenario: CapacityScenario | None = None
    model_name: str | None = None  # None = the recipe name


class SavedRecipeCapacityBody(RecipeTarget):
    model_config = {"extra": "forbid"}
    capacity_scenario: CapacityScenario = Field(default_factory=CapacityScenario)


class SavedRecipeThroughputBody(RecipeTarget):
    model_config = {"extra": "forbid"}
    input_tokens: int = Field(100_000, ge=1, le=10_000_000, strict=True)
    output_tokens: int = Field(1_000, ge=1, le=1_000_000, strict=True)
    concurrency: int = Field(8, ge=1, le=1_000_000, strict=True)
    shared_prefix_percent: int = Field(90, ge=0, le=100, strict=True)


PREVIEW_INGRESS_HOST = "<ingress-host>"


def _serialize(r: CustomServingRecipe) -> dict:
    return {
        "id": str(r.id),
        "name": r.name,
        "description": r.description,
        "hf_repo_id": getattr(r, "hf_repo_id", None),
        "model_path": r.model_path,
        "image": r.image,
        "gpu_count": r.gpu_count,
        "gpu_resource_key": r.gpu_resource_key,
        "cpu_request": r.cpu_request,
        "cpu_limit": r.cpu_limit,
        "memory_request": r.memory_request,
        "memory_limit": r.memory_limit,
        "node_selector": r.node_selector,
        "tolerations": r.tolerations,
        "storages": list(r.storages or []),
        "vllm_extra_args": r.vllm_extra_args,
        "env": r.env,
        "engine": r.engine or "vllm",
        "engine_args": r.engine_args,
        "probes": getattr(r, "probes", None),
        "gpu_type": getattr(r, "gpu_type", None),
        "serving_mode": getattr(r, "serving_mode", None) or "aggregated",
        "aggregated_router": getattr(r, "aggregated_router", None),
        "pd_config": getattr(r, "pd_config", None),
        "runtime": getattr(r, "runtime", None),
        "created_by": r.created_by,
        "updated_by": r.updated_by,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


async def _check_known_storages(db: AsyncSession, body: RecipeBody) -> None:
    """Every storage the recipe lists (base + P/D pools) must exist."""
    try:
        await check_known(db, all_entries(body))
    except UnknownStorageError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


async def _by_name(db: AsyncSession, name: str) -> CustomServingRecipe | None:
    return (await db.execute(select(CustomServingRecipe).where(CustomServingRecipe.name == name))).scalar_one_or_none()


async def _by_id(db: AsyncSession, recipe_id: str) -> CustomServingRecipe | None:
    return (
        await db.execute(select(CustomServingRecipe).where(CustomServingRecipe.id == uuid.UUID(recipe_id)))
    ).scalar_one_or_none()


@router.get("")
async def list_recipes(
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    rows = (
        (await db.execute(select(CustomServingRecipe).order_by(CustomServingRecipe.created_at.desc()))).scalars().all()
    )
    return {"recipes": [_serialize(r) for r in rows]}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_recipe(
    body: CreateRecipeBody,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if await _by_name(db, body.name):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A recipe with this name already exists")
    await _check_known_storages(db, body)
    fields = body.model_dump(exclude={"source_deployment_id"})
    fields["hf_repo_id"] = body.hf_repo_id or repo_id_from_path(body.model_path)
    recipe = CustomServingRecipe(id=uuid.uuid4(), created_by=user.user_id, updated_by=user.user_id, **fields)
    db.add(recipe)
    await db.flush()
    linked = False
    if body.source_deployment_id:
        dep = (
            await db.execute(
                select(CustomModelDeployment).where(CustomModelDeployment.id == uuid.UUID(body.source_deployment_id))
            )
        ).scalar_one_or_none()
        if dep is not None and dep.recipe_id is None:
            dep.recipe_id = recipe.id
            linked = True
    warnings = recipe_warnings(recipe.model_path, recipe.storages, recipe.pd_config)
    return {**_serialize(recipe), "linked_deployment": linked, "warnings": warnings}


async def _render_recipe_target(db: AsyncSession, body: PreviewRecipeBody):
    """Resolve the deployment that both manifest preview and KV sizing use."""
    cluster_uuid = body.cluster_id
    if cluster_uuid is not None and await db.get(CustomK8sCluster, cluster_uuid) is None:
        raise HTTPException(404, "Cluster not found")
    try:
        gpu_profile, gpu_label_key = await resolve_gpu_type(db, cluster_uuid, body.gpu_type)
    except UnknownGpuTypeError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    fields = body.model_dump(
        exclude={
            "name", "description", "hf_repo_id", "cluster_id", "namespace", "model_name",
            "storages", "capacity_scenario",
        }
    )
    dep = CustomModelDeployment(
        id=uuid.uuid4(),
        model_name=(body.model_name or body.name or "preview").strip(),
        cluster_id=cluster_uuid,
        namespace=body.namespace or await default_namespace(db, cluster_uuid),
        replicas=1,
        ingress_host=PREVIEW_INGRESS_HOST,
        ingress_path="/",
        ingress_class="nginx",
        status="Pending",
        **await load_deployment_copy(db),
        **fields,
    )
    unresolved: list[str] = []
    try:
        dep.storages = await resolve_entries(db, body.storages, cluster_uuid, dep.namespace, unresolved_out=unresolved)
        if is_pd(dep):
            dep.pd_config = await resolve_pool_storages(
                db, dep.pd_config, cluster_uuid, dep.namespace, unresolved_out=unresolved
            )
    except ValueError as e:  # unknown storage, reserved or duplicate volume name
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if is_pd(dep):
        await _apply_pool_profiles(db, dep, default_gpu_type=body.gpu_type)
    apply_profile(dep, gpu_profile, gpu_label_key)
    try:
        manifests = build_all(dep, sidecar_image=settings.llmd_sidecar_image)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return dep, gpu_profile, manifests, unresolved


@router.post("/preview")
async def preview_recipe(
    body: PreviewRecipeBody,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Render the K8s manifests a deploy of this (unsaved) recipe would apply."""
    dep, gpu_profile, manifests, unresolved = await _render_recipe_target(db, body)
    if aggregated_router.enabled(dep):
        try:
            stack = aggregated_router.stack_for(dep)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        manifests.append(llmd_stacks.ingress_for(stack))
    warnings = recipe_warnings(dep.model_path, dep.storages or [], dep.pd_config)
    warnings += [f"storage_unresolved:{n}" for n in unresolved]
    warnings += await pvc_warnings(db, dep)
    out = [
        {
            "kind": m.get("kind"),
            "name": (m.get("metadata") or {}).get("name"),
            "yaml": yaml.dump(m, Dumper=PlainDumper, sort_keys=False, default_flow_style=False),
        }
        for m in manifests
    ]
    router_images = None
    if aggregated_router.enabled(dep):
        repo, chart, version = llmd_stacks.chart_source(stack)
        values = pd_router.dump_values(stack.values_snapshot)
        out.append({"kind": "values.yaml", "name": stack.name, "yaml": f"# chart: {repo}/{chart} {version}\n{values}"})
        router = stack.values_snapshot["router"]
        image = router["epp"]["image"]
        proxy = router["proxy"]
        router_images = {
            "epp": "{registry}/{repository}:{tag}".format(**image),
            "envoy": proxy.get("image") or proxy["presets"]["envoy"]["image"],
        }
    if is_pd(dep):  # the router stack's values (ArgoCD renders the chart from them)
        repo, chart, version = pd_router.router_chart(dep)
        values = pd_router.dump_values(pd_router.rendered_router_values(dep))
        out.append({
            "kind": "values.yaml",
            "name": pd_router.stack_name(dep),
            "yaml": f"# chart: {repo}/{chart} {version}\n{values}",
        })
    capacity = {}
    if request := body.capacity_scenario:
        capacity["kv_capacity"] = await deployment_estimate(
            db, dep, manifests=manifests, base_profile=gpu_profile, start_metadata=True,
            scenario=request.scenario(),
        )
    if router_images:
        capacity["router_images"] = router_images
    return {"manifests": out, "warnings": warnings, **capacity}


async def _saved_recipe_target(db, recipe_id, body: RecipeTarget):
    recipe = await _by_id(db, str(recipe_id))
    if not recipe:
        raise HTTPException(404, "Recipe not found")
    target = PreviewRecipeBody(**_serialize(recipe), cluster_id=body.cluster_id, namespace=body.namespace)
    return await _render_recipe_target(db, target)


@router.post("/{recipe_id}/kv-capacity")
async def saved_recipe_capacity(
    recipe_id: uuid.UUID,
    body: SavedRecipeCapacityBody,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Size the persisted recipe on a target; clients supply no engine or model overrides."""
    dep, profile, manifests, unresolved = await _saved_recipe_target(db, recipe_id, body)
    result = await deployment_estimate(
        db, dep, manifests=manifests, base_profile=profile, start_metadata=True,
        scenario=body.capacity_scenario.scenario(), saved_recipe_id=recipe_id,
    )
    return {
        **result,
        "recipe_id": str(recipe_id),
        "cluster_id": str(dep.cluster_id) if dep.cluster_id else None,
        "namespace": dep.namespace,
        "warnings": [f"storage_unresolved:{name}" for name in dict.fromkeys(unresolved)],
    }


@router.post("/{recipe_id}/throughput")
async def saved_recipe_throughput(
    recipe_id: uuid.UUID,
    body: SavedRecipeThroughputBody,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Estimate a workload on the saved recipe without starting inference or a benchmark."""
    dep, profile, manifests, _ = await _saved_recipe_target(db, recipe_id, body)
    workload = body.model_dump(exclude={"cluster_id", "namespace"})
    kv_scenario = Scenario(body.input_tokens + body.output_tokens, body.concurrency,
                           body.input_tokens * body.shared_prefix_percent // 100)
    sized = await deployment_estimate(
        db, dep, manifests=manifests, scenario=kv_scenario, throughput_scenario=workload,
        start_metadata=True, base_profile=profile, saved_recipe_id=recipe_id,
    )
    forecast = sized["throughput"]
    if forecast["status"] != "infeasible":
        historical = await matching_recipe_benchmark(db, recipe_id, dep, sized["pools"], workload, manifests,
                                                     forecast=forecast)
        if historical:
            forecast = historical
    return {
        **forecast, "scenario": workload, "recipe_id": str(recipe_id),
        "cluster_id": str(dep.cluster_id) if dep.cluster_id else None, "namespace": dep.namespace,
        "pending": forecast["status"] == "unavailable" and forecast["reason"] in {"model_unknown", "draft_unknown"}
        and any(p.get("pending") for p in sized["pools"]),
        "pools": [{k: p.get(k) for k in ("role", "gpu_type", "gpu_count", "replicas", "model_path", "engine",
                                           "max_model_len", "gpu_vram_gb", "weight_bytes", "metadata_issue",
                                           "draft_method", "draft_tokens", "tp", "pp", "dcp")}
                  for p in sized["pools"]],
    }


class RouterDefaultsBody(BaseModel):
    serving_mode: ServingMode = "pd"
    router: dict | None = None  # the recipe's current structured router fields (converting an older recipe)


@router.post("/router-defaults")
async def router_defaults(body: RouterDefaultsBody, user: CustomUser = Depends(require_super_user)) -> dict:
    """Return the recipe editor's starter Helm values and chart source for this serving mode."""
    if body.serving_mode == "aggregated":
        repo, chart, version = aggregated_router.chart_source({})
        return {
            "chart_repo": repo, "chart_name": chart, "chart_version": version,
            "values_yaml": pd_router.dump_values(aggregated_router.defaults()),
        }
    return {
        "values_yaml": pd_router.default_values_yaml(body.router),
        "chart_repo": settings.llmd_chart_repo,
        "chart_name": settings.llmd_chart_name,
        "chart_version": settings.llmd_chart_version,
    }


@router.get("/{recipe_id}")
async def get_recipe(
    recipe_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    recipe = await _by_id(db, recipe_id)
    if not recipe:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recipe not found")
    return _serialize(recipe)


@router.put("/{recipe_id}")
async def update_recipe(
    recipe_id: str,
    body: RecipeBody,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    recipe = await _by_id(db, recipe_id)
    if not recipe:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recipe not found")
    return await save_recipe_update(recipe, body, user, db)


async def save_recipe_update(
    recipe: CustomServingRecipe, body: RecipeBody, user: CustomUser, db: AsyncSession, *, fields: set[str] | None = None
) -> dict:
    clash = await _by_name(db, body.name)
    if clash and clash.id != recipe.id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A recipe with this name already exists")
    await _check_known_storages(db, body)
    updates = body.model_dump(include=fields)
    if fields is None and "hf_repo_id" not in body.model_fields_set:
        updates.pop("hf_repo_id")  # Preserve the saved ID for older REST clients.
    if "hf_repo_id" in updates or ("model_path" in updates and not getattr(recipe, "hf_repo_id", None)):
        updates["hf_repo_id"] = body.hf_repo_id or repo_id_from_path(body.model_path)
    for k, v in updates.items():
        setattr(recipe, k, v)
    recipe.updated_by = user.user_id
    await db.flush()
    await db.refresh(recipe)  # updated_at is server-generated on update; load it before serialising
    return {**_serialize(recipe), "warnings": recipe_warnings(recipe.model_path, recipe.storages, recipe.pd_config)}


@router.delete("/{recipe_id}")
async def delete_recipe(
    recipe_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    recipe = await _by_id(db, recipe_id)
    if not recipe:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recipe not found")
    await db.delete(recipe)
    await db.flush()
    return {"deleted": True, "id": recipe_id}
