# vLLM Serving Recipes (v1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an admin save named, reusable vLLM serving recipes and launch a model deployment from one via a focused "Deploy from recipe" dialog.

**Architecture:** New `custom_serving_recipe` table + super-user CRUD API (`/api/admin/serving-recipes`), mirroring the existing admin-router pattern. Frontend: a recipes admin page (CRUD) and a Deploy dialog that collects per-instance fields and POSTs the **existing** `/api/model-deployments` create endpoint with the recipe's serving fields merged in. No change to the deployment backend.

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic (backend); Next.js app-router + react-query + next-intl (frontend).

## Global Constraints

- **Super-user only** for every recipe endpoint (`require_super_user`), like `budgets.py`/`model_deployments.py`.
- **Recipe column types mirror `custom_model_deployment`** exactly (String/Integer/JSONB lengths and defaults) so recipe→deployment mapping is 1:1.
- **Migration** `044_serving_recipe.py`, `down_revision = "043_llmd_stack_ingress_overrides"`.
- **No change to the deployment backend** — reuse `POST /api/model-deployments`. v1 adds the missing frontend `useCreateDeployment` mutation.
- **Backend tests use the `mock_db` (AsyncMock) pattern** from `tests/conftest.py` + `tests/test_llmd.py` — there is no real test DB. Gate: **0 new failures vs the `origin/main` baseline** (the suite has pre-existing failures; compare the set/count).
- **Frontend gates:** `npx tsc --noEmit` exit 0 and `npm run lint` with **0 new** problems (baseline: 4 errors / 13 warnings), run from `frontend/`.
- **en/ko i18n parity** — every new key added to both `messages/en.json` and `messages/ko.json`, equal counts.
- Work on branch `feat/serving-recipes` (off `origin/main`). Never stage the `litellm` submodule.

---

## File Structure

- **Create** `backend/app/db/models/custom_serving_recipe.py` — the ORM model.
- **Create** `backend/migrations/versions/044_serving_recipe.py` — create-table migration.
- **Create** `backend/app/api/serving_recipes.py` — super-user CRUD router.
- **Modify** `backend/app/main.py` — import + `include_router`.
- **Create** `backend/tests/test_serving_recipes.py` — API tests (mock_db).
- **Modify** `frontend/src/types/index.ts` — `ServingRecipe`, `ServingRecipeInput`, `CreateDeploymentBody`.
- **Modify** `frontend/src/hooks/use-api.ts` — recipe hooks + `useCreateDeployment`.
- **Create** `frontend/src/app/(app)/admin/recipes/page.tsx` — recipes admin page (CRUD).
- **Create** `frontend/src/components/deploy-from-recipe-dialog.tsx` — Deploy dialog.
- **Modify** `frontend/src/components/app-sidebar.tsx` — nav entry.
- **Modify** `frontend/messages/en.json` + `frontend/messages/ko.json` — `servingRecipes` block + `sidebar.adminRecipes`.

---

## Task 1: DB model + migration

**Files:**
- Create: `backend/app/db/models/custom_serving_recipe.py`
- Create: `backend/migrations/versions/044_serving_recipe.py`

**Interfaces — Produces:** `CustomServingRecipe` ORM model with columns:
`id, name(unique), description, model_path, image, gpu_count, gpu_resource_key, cpu_request, cpu_limit, memory_request, memory_limit, node_selector, tolerations, pvc_name, pvc_mount_path, vllm_extra_args, env, created_by, updated_by, created_at, updated_at`.

- [ ] **Step 1: Write the model**

Create `backend/app/db/models/custom_serving_recipe.py`:

```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import CustomBase


class CustomServingRecipe(CustomBase):
    """A named, reusable vLLM serving configuration.

    Captures the reusable subset of a model deployment's serving spec (model
    weights, image, compute, vLLM flags, env, placement hints) minus the
    per-instance fields (name, namespace, cluster, ingress, replicas). Applied by
    launching a deployment from it via the Deploy dialog.
    """

    __tablename__ = "custom_serving_recipe"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(256), nullable=False, unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_path: Mapped[str] = mapped_column(String(512), nullable=False)
    image: Mapped[str] = mapped_column(String(512), nullable=False)
    gpu_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    gpu_resource_key: Mapped[str] = mapped_column(
        String(128), nullable=False, default="nvidia.com/gpu", server_default="nvidia.com/gpu"
    )
    cpu_request: Mapped[str | None] = mapped_column(String(32), nullable=True)
    cpu_limit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    memory_request: Mapped[str | None] = mapped_column(String(32), nullable=True)
    memory_limit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    node_selector: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    tolerations: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    pvc_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    pvc_mount_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    vllm_extra_args: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    env: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
```

- [ ] **Step 2: Register the model for metadata/autogenerate**

Confirm models are imported where the others are (search): `grep -rn "custom_model_deployment" backend/app/db backend/migrations/env.py`. Add an import of `app.db.models.custom_serving_recipe` in the SAME place the other `custom_*` models are imported (usually `backend/app/db/models/__init__.py` or `migrations/env.py`). Match the existing style exactly.

- [ ] **Step 3: Write the migration**

Create `backend/migrations/versions/044_serving_recipe.py`:

```python
"""Create custom_serving_recipe (reusable vLLM serving templates).

Revision ID: 044_serving_recipe
Revises: 043_llmd_stack_ingress_overrides
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "044_serving_recipe"
down_revision = "043_llmd_stack_ingress_overrides"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_serving_recipe",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("model_path", sa.String(512), nullable=False),
        sa.Column("image", sa.String(512), nullable=False),
        sa.Column("gpu_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("gpu_resource_key", sa.String(128), nullable=False, server_default="nvidia.com/gpu"),
        sa.Column("cpu_request", sa.String(32), nullable=True),
        sa.Column("cpu_limit", sa.String(32), nullable=True),
        sa.Column("memory_request", sa.String(32), nullable=True),
        sa.Column("memory_limit", sa.String(32), nullable=True),
        sa.Column("node_selector", JSONB(), nullable=True),
        sa.Column("tolerations", JSONB(), nullable=True),
        sa.Column("pvc_name", sa.String(256), nullable=True),
        sa.Column("pvc_mount_path", sa.String(512), nullable=True),
        sa.Column("vllm_extra_args", JSONB(), nullable=True),
        sa.Column("env", JSONB(), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("updated_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        op.f("ix_custom_serving_recipe_name"), "custom_serving_recipe", ["name"], unique=True
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_custom_serving_recipe_name"), table_name="custom_serving_recipe")
    op.drop_table("custom_serving_recipe")
```

- [ ] **Step 4: Verify import + revision chain**

Run (from `backend/`):
```bash
python -c "from app.db.models.custom_serving_recipe import CustomServingRecipe; print(CustomServingRecipe.__tablename__)"
python -c "import ast; s=open('migrations/versions/044_serving_recipe.py').read(); assert 'down_revision = \"043_llmd_stack_ingress_overrides\"' in s; print('chain ok')"
ruff check app/db/models/custom_serving_recipe.py migrations/versions/044_serving_recipe.py
```
Expected: prints `custom_serving_recipe`, `chain ok`, ruff clean for these files.

- [ ] **Step 5: Commit**

```bash
git add backend/app/db/models/custom_serving_recipe.py backend/migrations/versions/044_serving_recipe.py backend/app/db/models/__init__.py backend/migrations/env.py
git commit -m "feat(recipes): custom_serving_recipe model + migration 044"
```
(Only add `__init__.py`/`env.py` if you edited them in Step 2.)

---

## Task 2: Backend CRUD API + registration + tests

**Files:**
- Create: `backend/app/api/serving_recipes.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_serving_recipes.py`

**Interfaces:**
- Consumes (Task 1): `CustomServingRecipe`.
- Produces: router at `/api/admin/serving-recipes` with `GET ""`, `POST ""`, `GET /{id}`, `PUT /{id}`, `DELETE /{id}`; `_serialize(recipe) -> dict`.

- [ ] **Step 1: Write the router**

Create `backend/app/api/serving_recipes.py`:

```python
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
```

- [ ] **Step 2: Register the router in `main.py`**

Add `serving_recipes` to the `from app.api import ( ... )` block and add, next to the other admin includes:
```python
app.include_router(serving_recipes.router)
```

- [ ] **Step 3: Write the tests**

Create `backend/tests/test_serving_recipes.py`:

```python
"""Serving recipe CRUD API — mock_db pattern (mirrors test_llmd.py)."""

import uuid
from unittest.mock import AsyncMock, MagicMock

from app.api.serving_recipes import _serialize
import types


def _result(scalar=None, all_rows=None):
    r = MagicMock()
    r.scalar_one_or_none.return_value = scalar
    r.scalars.return_value.all.return_value = all_rows or []
    return r


def _recipe(**kw):
    base = dict(
        id=uuid.uuid4(), name="r1", description=None, model_path="/w/llama", image="vllm:latest",
        gpu_count=1, gpu_resource_key="nvidia.com/gpu", cpu_request=None, cpu_limit=None,
        memory_request=None, memory_limit=None, node_selector=None, tolerations=None,
        pvc_name=None, pvc_mount_path=None, vllm_extra_args=["--tensor-parallel-size", "2"],
        env=None, created_by=None, updated_by=None, created_at=None, updated_at=None,
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


_BODY = {"name": "r1", "model_path": "/w/llama", "image": "vllm:latest",
         "vllm_extra_args": ["--tensor-parallel-size", "2"]}


def test_serialize_round_trips_fields():
    out = _serialize(_recipe(name="x", gpu_count=4))
    assert out["name"] == "x" and out["gpu_count"] == 4
    assert out["vllm_extra_args"] == ["--tensor-parallel-size", "2"]


async def test_create_recipe_201(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(scalar=None))  # name is free
    async with client_for_user(super_user) as client:
        resp = await client.post("/api/admin/serving-recipes", json=_BODY)
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "r1" and body["model_path"] == "/w/llama"
    mock_db.add.assert_called_once()


async def test_create_duplicate_name_409(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(scalar=_recipe()))  # name taken
    async with client_for_user(super_user) as client:
        resp = await client.post("/api/admin/serving-recipes", json=_BODY)
    assert resp.status_code == 409


async def test_create_missing_required_422(client_for_user, super_user, mock_db):
    async with client_for_user(super_user) as client:
        resp = await client.post("/api/admin/serving-recipes", json={"name": "r1"})  # no model_path/image
    assert resp.status_code == 422


async def test_create_bad_env_type_422(client_for_user, super_user, mock_db):
    async with client_for_user(super_user) as client:
        resp = await client.post(
            "/api/admin/serving-recipes",
            json={**_BODY, "env": ["not", "a", "dict"]},
        )
    assert resp.status_code == 422


async def test_list_recipes(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(all_rows=[_recipe(name="a"), _recipe(name="b")]))
    async with client_for_user(super_user) as client:
        resp = await client.get("/api/admin/serving-recipes")
    assert resp.status_code == 200
    assert [r["name"] for r in resp.json()["recipes"]] == ["a", "b"]


async def test_get_missing_404(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(scalar=None))
    async with client_for_user(super_user) as client:
        resp = await client.get(f"/api/admin/serving-recipes/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_delete_recipe(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(scalar=_recipe()))
    mock_db.delete = AsyncMock()
    async with client_for_user(super_user) as client:
        resp = await client.delete(f"/api/admin/serving-recipes/{uuid.uuid4()}")
    assert resp.status_code == 200
    mock_db.delete.assert_awaited_once()
```

- [ ] **Step 4: Run the tests + establish they pass**

Run (from `backend/`):
```bash
python -m pytest tests/test_serving_recipes.py -q
ruff check app/api/serving_recipes.py tests/test_serving_recipes.py
```
Expected: all pass; ruff clean. If `mock_db.delete`/`mock_db.add` isn't preconfigured by conftest, set it in the test as shown (`AsyncMock`/it's a MagicMock by default).

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/serving_recipes.py backend/app/main.py backend/tests/test_serving_recipes.py
git commit -m "feat(recipes): serving-recipes CRUD API + tests"
```

---

## Task 3: Frontend types + hooks

**Files:**
- Modify: `frontend/src/types/index.ts`
- Modify: `frontend/src/hooks/use-api.ts`

**Interfaces — Produces:** `ServingRecipe`, `ServingRecipeInput`, `CreateDeploymentBody` types; hooks `useServingRecipes`, `useCreateServingRecipe`, `useUpdateServingRecipe`, `useDeleteServingRecipe`, `useCreateDeployment`.

- [ ] **Step 1: Add types**

Append to `frontend/src/types/index.ts`:

```ts
export interface ServingRecipe {
  id: string;
  name: string;
  description: string | null;
  model_path: string;
  image: string;
  gpu_count: number;
  gpu_resource_key: string;
  cpu_request: string | null;
  cpu_limit: string | null;
  memory_request: string | null;
  memory_limit: string | null;
  node_selector: Record<string, string> | null;
  tolerations: unknown[] | null;
  pvc_name: string | null;
  pvc_mount_path: string | null;
  vllm_extra_args: string[] | null;
  env: Record<string, string> | null;
  created_by: string | null;
  updated_by: string | null;
  created_at: string | null;
  updated_at: string | null;
}

// Editable fields for create/update (server sets id/audit/timestamps).
export type ServingRecipeInput = Omit<
  ServingRecipe,
  "id" | "created_by" | "updated_by" | "created_at" | "updated_at"
>;

// Body for POST /api/model-deployments (recipe serving fields + instance fields).
export interface CreateDeploymentBody {
  model_name: string;
  cluster_id: string | null;
  namespace: string;
  image: string;
  replicas: number;
  gpu_count: number;
  gpu_resource_key: string;
  cpu_request: string | null;
  cpu_limit: string | null;
  memory_request: string | null;
  memory_limit: string | null;
  node_selector: Record<string, string> | null;
  tolerations: unknown[] | null;
  pvc_name: string | null;
  pvc_mount_path: string | null;
  model_path: string;
  vllm_extra_args: string[] | null;
  env: Record<string, string> | null;
  ingress_host: string;
  ingress_path: string;
  ingress_class: string;
}
```

- [ ] **Step 2: Add hooks**

In `frontend/src/hooks/use-api.ts`, add the `ServingRecipe`/`ServingRecipeInput`/`CreateDeploymentBody`/`ModelDeployment` names to the existing `import type { ... } from "@/types"` block, then append:

```ts
export function useServingRecipes() {
  return useQuery({
    queryKey: ["serving-recipes"],
    queryFn: () =>
      apiFetch<{ recipes: ServingRecipe[] }>("/api/admin/serving-recipes").then((r) => r.recipes),
  });
}

export function useCreateServingRecipe() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ServingRecipeInput) =>
      apiFetch<ServingRecipe>("/api/admin/serving-recipes", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["serving-recipes"] }),
  });
}

export function useUpdateServingRecipe() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: ServingRecipeInput }) =>
      apiFetch<ServingRecipe>(`/api/admin/serving-recipes/${id}`, {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["serving-recipes"] }),
  });
}

export function useDeleteServingRecipe() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      apiFetch<{ deleted: boolean }>(`/api/admin/serving-recipes/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["serving-recipes"] }),
  });
}

// Create a model deployment via the existing backend endpoint (no create UI
// existed before; the Deploy-from-recipe dialog is the first caller).
export function useCreateDeployment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateDeploymentBody) =>
      apiFetch<ModelDeployment>("/api/model-deployments", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["model-deployments"] }),
  });
}
```

- [ ] **Step 3: Type-check**

Run (from `frontend/`): `npx tsc --noEmit`
Expected: exit 0. (`ModelDeployment` already exists in `types/index.ts`.)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/hooks/use-api.ts
git commit -m "feat(recipes): frontend types + recipe/deployment hooks"
```

---

## Task 4: Recipes admin page (CRUD) + nav + i18n

**Files:**
- Create: `frontend/src/app/(app)/admin/recipes/page.tsx`
- Modify: `frontend/src/components/app-sidebar.tsx`
- Modify: `frontend/messages/en.json`, `frontend/messages/ko.json`

**Interfaces — Consumes (Task 3):** `useServingRecipes`, `useCreateServingRecipe`, `useUpdateServingRecipe`, `useDeleteServingRecipe`, `ServingRecipe`, `ServingRecipeInput`.

- [ ] **Step 1: Add the nav entry**

In `frontend/src/components/app-sidebar.tsx`: add `ScrollText` to the `lucide-react` import, and add this line to the `SIDEBAR` array **immediately after** the `adminDeployments` entry:
```ts
  { key: "adminRecipes", href: "/admin/recipes", icon: ScrollText, roles: ["super_user"] },
```

- [ ] **Step 2: Add i18n (en, then ko)**

In `frontend/messages/en.json`: add `"adminRecipes": "Serving Recipes"` to the `"sidebar"` block, and add a new top-level `"servingRecipes"` block:
```json
  "servingRecipes": {
    "title": "Serving Recipes",
    "subtitle": "Reusable vLLM serving configurations",
    "newRecipe": "New recipe",
    "empty": "No recipes yet",
    "colName": "Name",
    "colModel": "Model path",
    "colImage": "Image",
    "colGpu": "GPU",
    "colActions": "Actions",
    "name": "Name",
    "description": "Description",
    "modelPath": "Model path",
    "image": "Image",
    "gpuCount": "GPU count",
    "gpuResourceKey": "GPU resource key",
    "cpuRequest": "CPU request",
    "cpuLimit": "CPU limit",
    "memoryRequest": "Memory request",
    "memoryLimit": "Memory limit",
    "vllmArgs": "vLLM args (one per line)",
    "env": "Env (KEY=VALUE per line)",
    "nodeSelector": "Node selector (KEY=VALUE per line)",
    "pvcName": "PVC name",
    "pvcMountPath": "PVC mount path",
    "create": "Create",
    "save": "Save",
    "edit": "Edit",
    "delete": "Delete",
    "deploy": "Deploy",
    "deleteConfirm": "Delete recipe \"{name}\"?",
    "createTitle": "New recipe",
    "editTitle": "Edit recipe",
    "saveError": "Failed to save recipe",
    "requiredError": "Name, model path and image are required"
  }
```
Add the identical keys to `frontend/messages/ko.json` (`sidebar.adminRecipes` = `"서빙 레시피"`) with Korean values, e.g. `title` `"서빙 레시피"`, `subtitle` `"재사용 가능한 vLLM 서빙 설정"`, `newRecipe` `"새 레시피"`, `deploy` `"배포"`, `deleteConfirm` `"레시피 \"{name}\"을(를) 삭제할까요?"`, etc. Keep en/ko key sets identical.

- [ ] **Step 3: Write the page**

Create `frontend/src/app/(app)/admin/recipes/page.tsx`. Uses the existing UI kit (`Dialog*`, `Button`, `Input`, `Label`, `Table*`) and `toast`. Text areas parse line-based args/env/node_selector to the JSON shapes.

```tsx
"use client";

import { useState } from "react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import {
  useServingRecipes,
  useCreateServingRecipe,
  useUpdateServingRecipe,
  useDeleteServingRecipe,
} from "@/hooks/use-api";
import type { ServingRecipe, ServingRecipeInput } from "@/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { DeployFromRecipeDialog } from "@/components/deploy-from-recipe-dialog";

const BLANK: ServingRecipeInput = {
  name: "", description: null, model_path: "", image: "", gpu_count: 1,
  gpu_resource_key: "nvidia.com/gpu", cpu_request: null, cpu_limit: null,
  memory_request: null, memory_limit: null, node_selector: null, tolerations: null,
  pvc_name: null, pvc_mount_path: null, vllm_extra_args: null, env: null,
};

const linesToList = (s: string): string[] | null => {
  const v = s.split("\n").map((x) => x.trim()).filter(Boolean);
  return v.length ? v : null;
};
const listToLines = (v: string[] | null): string => (v ?? []).join("\n");
const linesToMap = (s: string): Record<string, string> | null => {
  const out: Record<string, string> = {};
  for (const line of s.split("\n")) {
    const i = line.indexOf("=");
    if (i > 0) out[line.slice(0, i).trim()] = line.slice(i + 1).trim();
  }
  return Object.keys(out).length ? out : null;
};
const mapToLines = (m: Record<string, string> | null): string =>
  Object.entries(m ?? {}).map(([k, v]) => `${k}=${v}`).join("\n");

function toInput(r: ServingRecipe): ServingRecipeInput {
  const { id, created_by, updated_by, created_at, updated_at, ...rest } = r;
  void id; void created_by; void updated_by; void created_at; void updated_at;
  return rest;
}

export default function AdminRecipesPage() {
  const t = useTranslations("servingRecipes");
  const tc = useTranslations("common");
  const { data: recipes, isLoading } = useServingRecipes();
  const createMut = useCreateServingRecipe();
  const updateMut = useUpdateServingRecipe();
  const deleteMut = useDeleteServingRecipe();

  const [editing, setEditing] = useState<ServingRecipe | null>(null);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<ServingRecipeInput>(BLANK);
  const [argsText, setArgsText] = useState("");
  const [envText, setEnvText] = useState("");
  const [nsText, setNsText] = useState("");
  const [deployTarget, setDeployTarget] = useState<ServingRecipe | null>(null);

  function openCreate() {
    setEditing(null); setForm(BLANK); setArgsText(""); setEnvText(""); setNsText(""); setOpen(true);
  }
  function openEdit(r: ServingRecipe) {
    setEditing(r); setForm(toInput(r));
    setArgsText(listToLines(r.vllm_extra_args)); setEnvText(mapToLines(r.env)); setNsText(mapToLines(r.node_selector));
    setOpen(true);
  }
  function submit() {
    if (!form.name.trim() || !form.model_path.trim() || !form.image.trim()) {
      toast.error(t("requiredError")); return;
    }
    const body: ServingRecipeInput = {
      ...form,
      vllm_extra_args: linesToList(argsText),
      env: linesToMap(envText),
      node_selector: linesToMap(nsText),
    };
    const onDone = {
      onSuccess: () => { setOpen(false); },
      onError: (e: unknown) => toast.error(e instanceof Error ? e.message : t("saveError")),
    };
    if (editing) updateMut.mutate({ id: editing.id, body }, onDone);
    else createMut.mutate(body, onDone);
  }
  function remove(r: ServingRecipe) {
    if (!confirm(t("deleteConfirm", { name: r.name }))) return;
    deleteMut.mutate(r.id, { onError: (e) => toast.error(e instanceof Error ? e.message : t("saveError")) });
  }

  const saving = createMut.isPending || updateMut.isPending;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">{t("title")}</h1>
          <p className="text-muted-foreground mt-1">{t("subtitle")}</p>
        </div>
        <Button onClick={openCreate}>{t("newRecipe")}</Button>
      </div>

      {isLoading ? (
        <div className="h-32 animate-pulse rounded-lg bg-muted" />
      ) : !recipes || recipes.length === 0 ? (
        <div className="rounded-lg border border-dashed p-8 text-center text-muted-foreground">{t("empty")}</div>
      ) : (
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("colName")}</TableHead>
                <TableHead>{t("colModel")}</TableHead>
                <TableHead>{t("colImage")}</TableHead>
                <TableHead>{t("colGpu")}</TableHead>
                <TableHead className="text-right">{t("colActions")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {recipes.map((r) => (
                <TableRow key={r.id}>
                  <TableCell className="font-medium">{r.name}</TableCell>
                  <TableCell className="font-mono text-xs">{r.model_path}</TableCell>
                  <TableCell className="font-mono text-xs">{r.image}</TableCell>
                  <TableCell>{r.gpu_count} × {r.gpu_resource_key}</TableCell>
                  <TableCell className="text-right space-x-2 whitespace-nowrap">
                    <Button size="xs" onClick={() => setDeployTarget(r)}>{t("deploy")}</Button>
                    <Button size="xs" variant="outline" onClick={() => openEdit(r)}>{t("edit")}</Button>
                    <Button size="xs" variant="destructive" onClick={() => remove(r)}>{t("delete")}</Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editing ? t("editTitle") : t("createTitle")}</DialogTitle>
          </DialogHeader>
          <div className="grid grid-cols-2 gap-3">
            <Labeled label={t("name")}><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Labeled>
            <Labeled label={t("image")}><Input value={form.image} onChange={(e) => setForm({ ...form, image: e.target.value })} /></Labeled>
            <Labeled label={t("modelPath")} span2><Input value={form.model_path} onChange={(e) => setForm({ ...form, model_path: e.target.value })} /></Labeled>
            <Labeled label={t("description")} span2><Input value={form.description ?? ""} onChange={(e) => setForm({ ...form, description: e.target.value || null })} /></Labeled>
            <Labeled label={t("gpuCount")}><Input type="number" min={0} value={form.gpu_count} onChange={(e) => setForm({ ...form, gpu_count: Number(e.target.value) })} /></Labeled>
            <Labeled label={t("gpuResourceKey")}><Input value={form.gpu_resource_key} onChange={(e) => setForm({ ...form, gpu_resource_key: e.target.value })} /></Labeled>
            <Labeled label={t("cpuRequest")}><Input value={form.cpu_request ?? ""} onChange={(e) => setForm({ ...form, cpu_request: e.target.value || null })} /></Labeled>
            <Labeled label={t("cpuLimit")}><Input value={form.cpu_limit ?? ""} onChange={(e) => setForm({ ...form, cpu_limit: e.target.value || null })} /></Labeled>
            <Labeled label={t("memoryRequest")}><Input value={form.memory_request ?? ""} onChange={(e) => setForm({ ...form, memory_request: e.target.value || null })} /></Labeled>
            <Labeled label={t("memoryLimit")}><Input value={form.memory_limit ?? ""} onChange={(e) => setForm({ ...form, memory_limit: e.target.value || null })} /></Labeled>
            <Labeled label={t("pvcName")}><Input value={form.pvc_name ?? ""} onChange={(e) => setForm({ ...form, pvc_name: e.target.value || null })} /></Labeled>
            <Labeled label={t("pvcMountPath")}><Input value={form.pvc_mount_path ?? ""} onChange={(e) => setForm({ ...form, pvc_mount_path: e.target.value || null })} /></Labeled>
            <Labeled label={t("vllmArgs")} span2><Area value={argsText} onChange={setArgsText} /></Labeled>
            <Labeled label={t("env")} span2><Area value={envText} onChange={setEnvText} /></Labeled>
            <Labeled label={t("nodeSelector")} span2><Area value={nsText} onChange={setNsText} /></Labeled>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)} disabled={saving}>{tc("cancel")}</Button>
            <Button onClick={submit} disabled={saving}>{editing ? t("save") : t("create")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <DeployFromRecipeDialog recipe={deployTarget} onClose={() => setDeployTarget(null)} />
    </div>
  );
}

function Labeled({ label, span2, children }: { label: string; span2?: boolean; children: React.ReactNode }) {
  return (
    <div className={span2 ? "col-span-2 space-y-1" : "space-y-1"}>
      <Label className="text-xs">{label}</Label>
      {children}
    </div>
  );
}
function Area({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <textarea
      rows={3}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="w-full rounded-md border border-input bg-transparent px-3 py-2 font-mono text-xs focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
    />
  );
}
```

**Note (tolerations):** the form exposes `node_selector` and `pvc_*` but **not**
`tolerations` (a complex K8s list-of-objects). `tolerations` is still stored,
carried through on edit (`toInput` preserves `r.tolerations`), and settable via
the API; a raw-JSON tolerations editor is deferred. This is a deliberate v1 form
limitation, not a data-model gap — the column/type/serializer all include it.

- [ ] **Step 4: Type-check + lint**

Run (from `frontend/`): `npx tsc --noEmit` (exit 0) and `npm run lint` (0 new).
Note: this task imports `DeployFromRecipeDialog` from Task 5 — **do Task 5 before running the gates, or temporarily stub the import.** Recommended: implement Task 5 first, then run gates once for Tasks 4+5 together. (The reviewer will see both.)

- [ ] **Step 5: Commit** (after gates pass — may be combined with Task 5)

```bash
git add "frontend/src/app/(app)/admin/recipes/page.tsx" frontend/src/components/app-sidebar.tsx frontend/messages/en.json frontend/messages/ko.json
git commit -m "feat(recipes): recipes admin page (CRUD) + nav + i18n"
```

---

## Task 5: Deploy-from-recipe dialog

**Files:**
- Create: `frontend/src/components/deploy-from-recipe-dialog.tsx`
- Modify: `frontend/messages/en.json`, `frontend/messages/ko.json` (add `servingRecipes.deploy*` keys)

**Interfaces — Consumes (Task 3):** `useCreateDeployment`, `ServingRecipe`, `CreateDeploymentBody`.

- [ ] **Step 1: Add i18n keys**

Add to `servingRecipes` in both `en.json` and `ko.json` (equal sets):
```json
    "deployTitle": "Deploy \"{name}\"",
    "deployModelName": "Deployment model name",
    "deployNamespace": "Namespace",
    "deployClusterId": "Cluster ID (blank = portal default)",
    "deployIngressHost": "Ingress host",
    "deployIngressPath": "Ingress path",
    "deployIngressClass": "Ingress class",
    "deployReplicas": "Replicas",
    "deploySubmit": "Deploy",
    "deployRequired": "Deployment model name and ingress host are required",
    "deployError": "Failed to create deployment",
    "deploySuccess": "Deployment created"
```
en values as above; ko e.g. `deployTitle` `"\"{name}\" 배포"`, `deployModelName` `"배포 모델명"`, `deployIngressHost` `"인그레스 호스트"`, `deploySuccess` `"배포가 생성되었습니다"`, etc.

- [ ] **Step 2: Write the dialog**

Create `frontend/src/components/deploy-from-recipe-dialog.tsx`:

```tsx
"use client";

import { useState, useEffect } from "react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import { useCreateDeployment } from "@/hooks/use-api";
import type { ServingRecipe, CreateDeploymentBody } from "@/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";

export function DeployFromRecipeDialog({
  recipe,
  onClose,
}: {
  recipe: ServingRecipe | null;
  onClose: () => void;
}) {
  const t = useTranslations("servingRecipes");
  const tc = useTranslations("common");
  const createDep = useCreateDeployment();

  const [modelName, setModelName] = useState("");
  const [namespace, setNamespace] = useState("default");
  const [clusterId, setClusterId] = useState("");
  const [ingressHost, setIngressHost] = useState("");
  const [ingressPath, setIngressPath] = useState("/");
  const [ingressClass, setIngressClass] = useState("nginx");
  const [replicas, setReplicas] = useState(1);

  // Reset instance fields each time a new recipe opens the dialog.
  useEffect(() => {
    if (recipe) {
      setModelName(""); setNamespace("default"); setClusterId("");
      setIngressHost(""); setIngressPath("/"); setIngressClass("nginx"); setReplicas(1);
    }
  }, [recipe]);

  function submit() {
    if (!recipe) return;
    if (!modelName.trim() || !ingressHost.trim()) {
      toast.error(t("deployRequired"));
      return;
    }
    const body: CreateDeploymentBody = {
      model_name: modelName.trim(),
      cluster_id: clusterId.trim() || null,
      namespace: namespace.trim() || "default",
      image: recipe.image,
      replicas,
      gpu_count: recipe.gpu_count,
      gpu_resource_key: recipe.gpu_resource_key,
      cpu_request: recipe.cpu_request,
      cpu_limit: recipe.cpu_limit,
      memory_request: recipe.memory_request,
      memory_limit: recipe.memory_limit,
      node_selector: recipe.node_selector,
      tolerations: recipe.tolerations,
      pvc_name: recipe.pvc_name,
      pvc_mount_path: recipe.pvc_mount_path,
      model_path: recipe.model_path,
      vllm_extra_args: recipe.vllm_extra_args,
      env: recipe.env,
      ingress_host: ingressHost.trim(),
      ingress_path: ingressPath.trim() || "/",
      ingress_class: ingressClass.trim() || "nginx",
    };
    createDep.mutate(body, {
      onSuccess: () => { toast.success(t("deploySuccess")); onClose(); },
      onError: (e) => toast.error(e instanceof Error ? e.message : t("deployError")),
    });
  }

  return (
    <Dialog open={!!recipe} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{recipe ? t("deployTitle", { name: recipe.name }) : ""}</DialogTitle>
          <DialogDescription className="font-mono text-xs">
            {recipe ? `${recipe.model_path} · ${recipe.image} · ${recipe.gpu_count}×${recipe.gpu_resource_key}` : ""}
          </DialogDescription>
        </DialogHeader>
        <div className="grid grid-cols-2 gap-3">
          <Field label={t("deployModelName")} span2><Input value={modelName} onChange={(e) => setModelName(e.target.value)} /></Field>
          <Field label={t("deployNamespace")}><Input value={namespace} onChange={(e) => setNamespace(e.target.value)} /></Field>
          <Field label={t("deployClusterId")}><Input value={clusterId} onChange={(e) => setClusterId(e.target.value)} /></Field>
          <Field label={t("deployIngressHost")} span2><Input value={ingressHost} onChange={(e) => setIngressHost(e.target.value)} /></Field>
          <Field label={t("deployIngressPath")}><Input value={ingressPath} onChange={(e) => setIngressPath(e.target.value)} /></Field>
          <Field label={t("deployIngressClass")}><Input value={ingressClass} onChange={(e) => setIngressClass(e.target.value)} /></Field>
          <Field label={t("deployReplicas")}><Input type="number" min={0} value={replicas} onChange={(e) => setReplicas(Number(e.target.value))} /></Field>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={createDep.isPending}>{tc("cancel")}</Button>
          <Button onClick={submit} disabled={createDep.isPending}>{t("deploySubmit")}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Field({ label, span2, children }: { label: string; span2?: boolean; children: React.ReactNode }) {
  return (
    <div className={span2 ? "col-span-2 space-y-1" : "space-y-1"}>
      <Label className="text-xs">{label}</Label>
      {children}
    </div>
  );
}
```

- [ ] **Step 3: Type-check + lint (Tasks 4 + 5 together)**

Run (from `frontend/`):
```bash
npx tsc --noEmit
npm run lint
node -e "const en=require('./messages/en.json'),ko=require('./messages/ko.json');const a=Object.keys(en.servingRecipes),b=Object.keys(ko.servingRecipes);console.log('servingRecipes en',a.length,'ko',b.length,'mismatch',a.filter(k=>!b.includes(k)).concat(b.filter(k=>!a.includes(k))))"
```
Expected: tsc exit 0; lint 0 new; i18n `mismatch []` and equal counts.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/deploy-from-recipe-dialog.tsx frontend/messages/en.json frontend/messages/ko.json
git commit -m "feat(recipes): deploy-from-recipe dialog (POST existing create endpoint)"
```

---

## Verification (whole feature)

**Backend** (from `backend/`):
```bash
python -m pytest tests/test_serving_recipes.py -q
ruff check app/api/serving_recipes.py app/db/models/custom_serving_recipe.py migrations/versions/044_serving_recipe.py tests/test_serving_recipes.py
```
Gate: recipe tests pass; **0 new failures vs the `origin/main` baseline** for the rest of the suite.

**Frontend** (from `frontend/`):
```bash
npx tsc --noEmit      # exit 0
npm run lint          # 0 new problems
```

**Manual (dev server):** super-user → **Serving Recipes** in the sidebar → New recipe (name, model path, image, GPU, vLLM args) → save → row appears. Edit → change args → save. **Deploy** → fill deployment model name + ingress host → Deploy → toast success and the deployment appears under **Serving Deployments** (`/admin/deployments`). A duplicate deployment model name surfaces the endpoint error in the dialog. Toggle en/ko — all strings render.

**Manual (cluster E2E, optional):** confirm the created deployment's pod launches vLLM with the recipe's `--model <model_path>` + args and the chosen GPU/resources.
