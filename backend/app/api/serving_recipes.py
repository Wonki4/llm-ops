"""Serving recipe CRUD (Super User only). Reusable vLLM serving templates."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_super_user
from app.db.models.custom_serving_recipe import CustomServingRecipe
from app.db.models.custom_user import CustomUser
from app.db.session import get_db

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
        "created_by": r.created_by,
        "updated_by": r.updated_by,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


async def _by_name(db: AsyncSession, name: str) -> CustomServingRecipe | None:
    return (
        await db.execute(select(CustomServingRecipe).where(CustomServingRecipe.name == name))
    ).scalar_one_or_none()


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
        await db.execute(select(CustomServingRecipe).order_by(CustomServingRecipe.created_at.desc()))
    ).scalars().all()
    return {"recipes": [_serialize(r) for r in rows]}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_recipe(
    body: RecipeBody,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if await _by_name(db, body.name):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A recipe with this name already exists")
    recipe = CustomServingRecipe(id=uuid.uuid4(), created_by=user.user_id, updated_by=user.user_id, **body.model_dump())
    db.add(recipe)
    await db.flush()
    return _serialize(recipe)


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
