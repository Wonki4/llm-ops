"""Serving recipe CRUD (Super User only). Reusable vLLM/SGLang serving templates."""

import uuid

import yaml
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.model_deployments import _apply_pool_profiles, pvc_warnings
from app.auth.deps import require_super_user
from app.config import settings
from app.db.models.custom_model_deployment import CustomModelDeployment
from app.db.models.custom_serving_recipe import CustomServingRecipe
from app.db.models.custom_user import CustomUser
from app.db.session import get_db
from app.services import pd_router
from app.services.clusters import default_namespace, validate_namespace
from app.services.gpu_profile_store import UnknownGpuTypeError, resolve_gpu_type
from app.services.gpu_profiles import apply_profile, validate_profile_name
from app.services.manifest_diff import PlainDumper
from app.services.model_deployment_manifests import build_all
from app.services.pd_serving import ServingMode, check_kv_events, is_pd, validate_pd_config, validate_runtime
from app.services.serving_engines import ServingEngine, validate_engine_args, validate_port_flag
from app.services.serving_probes import validate_probes
from app.services.storage_fields import recipe_warnings, reject_legacy_pvc
from app.services.storage_mounts import all_entries, validate_entries
from app.services.storage_store import UnknownStorageError, check_known, resolve_entries, resolve_pool_storages

router = APIRouter(prefix="/api/admin/serving-recipes", tags=["serving-recipes"])


class RecipeBody(BaseModel):
    name: str
    description: str | None = None
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
    pd_config: dict | None = None  # per-role overrides etc. (pd only; see pd_serving)
    runtime: dict | None = None  # shm / hostIPC / privileged / extra resources

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


class PreviewRecipeBody(RecipeBody):
    """An unsaved recipe plus the deploy target the preview renders it for."""

    name: str = ""
    cluster_id: str | None = None  # None = portal default cluster
    namespace: str | None = None  # None/blank = the cluster's default namespace
    model_name: str | None = None  # None = the recipe name

    @field_validator("namespace")
    @classmethod
    def _check_namespace(cls, v: str | None) -> str | None:
        return validate_namespace(v)


PREVIEW_INGRESS_HOST = "<ingress-host>"


def _serialize(r: CustomServingRecipe) -> dict:
    return {
        "id": str(r.id),
        "name": r.name,
        "description": r.description,
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


@router.post("/preview")
async def preview_recipe(
    body: PreviewRecipeBody,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Render the K8s manifests a deploy of this (unsaved) recipe would apply.

    Runs the deploy path — storage resolution, GPU profiles, ``build_all`` — on
    a transient row that is never added to the session. Unbound storages render
    as placeholder claims and come back as ``storage_unresolved:<name>``
    warnings; the P/D router (an llm-d stack) is not part of these manifests.
    """
    cluster_uuid = uuid.UUID(body.cluster_id) if body.cluster_id else None
    try:
        gpu_profile, gpu_label_key = await resolve_gpu_type(db, cluster_uuid, body.gpu_type)
    except UnknownGpuTypeError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    fields = body.model_dump(exclude={"name", "description", "cluster_id", "namespace", "model_name", "storages"})
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
    if is_pd(dep):  # the router stack's values (ArgoCD renders the chart from them)
        repo, chart, version = pd_router.router_chart(dep)
        values = pd_router.dump_values(pd_router.rendered_router_values(dep))
        out.append({
            "kind": "values.yaml",
            "name": f"{dep.model_name}-router",
            "yaml": f"# chart: {repo}/{chart} {version}\n{values}",
        })
    return {"manifests": out, "warnings": warnings}


class RouterDefaultsBody(BaseModel):
    router: dict | None = None  # the recipe's current structured router fields (converting an older recipe)


@router.post("/router-defaults")
async def router_defaults(body: RouterDefaultsBody, user: CustomUser = Depends(require_super_user)) -> dict:
    """What a P/D recipe's router editor starts with: a starter values.yaml (from the recipe's
    structured router fields when converting one, else defaults) and the portal's chart."""
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
    clash = await _by_name(db, body.name)
    if clash and clash.id != recipe.id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A recipe with this name already exists")
    await _check_known_storages(db, body)
    for k, v in body.model_dump().items():
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
