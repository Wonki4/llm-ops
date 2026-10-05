"""Serving recipe CRUD (Super User only). Reusable vLLM/SGLang serving templates."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_super_user
from app.db.models.custom_model_deployment import CustomModelDeployment
from app.db.models.custom_serving_recipe import CustomServingRecipe
from app.db.models.custom_user import CustomUser
from app.db.session import get_db
from app.services.gpu_profiles import validate_profile_name
from app.services.pd_serving import ServingMode, validate_pd_config, validate_runtime
from app.services.serving_engines import ServingEngine, validate_engine_args
from app.services.serving_probes import validate_probes

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
    pvc_name: str | None = None
    pvc_mount_path: str | None = None
    vllm_extra_args: list[str] | None = None
    env: dict[str, str] | None = None
    engine: ServingEngine = "vllm"
    engine_args: dict[str, str | int | float | bool] | None = None
    probes: dict | None = None
    gpu_type: str | None = None  # GPU profile name, resolved per cluster at deploy time
    serving_mode: ServingMode = "aggregated"
    pd_config: dict | None = None  # per-role overrides etc. (pd only; see pd_serving)
    runtime: dict | None = None  # shm / hostIPC / privileged / extra resources

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
        "pvc_name": r.pvc_name,
        "pvc_mount_path": r.pvc_mount_path,
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
    return {**_serialize(recipe), "linked_deployment": linked}


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
    for k, v in body.model_dump().items():
        setattr(recipe, k, v)
    recipe.updated_by = user.user_id
    await db.flush()
    await db.refresh(recipe)  # updated_at is server-generated on update; load it before serialising
    return _serialize(recipe)


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
