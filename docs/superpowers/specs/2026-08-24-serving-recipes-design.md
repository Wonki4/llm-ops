# vLLM Serving Recipes — Design (v1)

**Date:** 2026-08-24
**Status:** Approved (design)

## Goal

Let an admin save a named, reusable **vLLM serving configuration** (a "recipe")
and apply it when creating a model deployment by **pre-filling** the deployment
form. A recipe captures the full "how to serve this model" spec — model weights,
image, compute, vLLM flags, env, and placement hints — minus the per-instance
values (name, namespace, cluster, ingress, replicas) that differ every time.

"Recipe" here means a **reusable serving-config template**, not a per-model
recommendation or a benchmark-result catalog.

## Scope

**v1 (this spec):** recipe CRUD (DB + API + admin UI) + apply-to-model-deployment
via form pre-fill.

**v2 (separate spec, deferred):** applying a recipe to **benchmark self-serving**
and **llm-d stacks**. The user chose the phased path so v1 ships usable value and
each later apply-mapper is added on its own.

### Clean boundary

**Applying a recipe is pure frontend pre-fill.** Selecting a recipe on the model
deployment create form sets the form's fields from the recipe; the user then
fills the per-instance values and submits through the **existing** deployment
create endpoint. So v1 adds only the recipe CRUD backend — **no change to the
deployment backend**.

## Background — verified facts

- **`custom_model_deployment` already holds a full serving config** (verified in
  `backend/app/db/models/custom_model_deployment.py`): `image`, `model_path`,
  `gpu_count`, `gpu_resource_key`, `cpu_request/limit`, `memory_request/limit`,
  `node_selector` (JSONB), `tolerations` (JSONB), `pvc_name`, `pvc_mount_path`,
  `vllm_extra_args` (JSONB list), `env` (JSONB dict), plus per-instance fields
  `model_name` (unique), `namespace`, `cluster_id`, `ingress_host/path/class`,
  `replicas`. A recipe is the reusable subset of these, so recipe→deployment
  mapping is 1:1 on field names.
- **Admin API pattern** (verified in `backend/app/api/budgets.py` and
  `main.py`): super-user-gated routers registered in `backend/app/main.py` via
  `app.include_router(...)`. Recipes follow the same shape.
- **Latest migration on `origin/main`** is `043_llmd_stack_ingress_overrides.py`;
  the recipe migration is **`044`**.
- **Deployment create UI** lives under
  `frontend/src/app/(app)/admin/deployments/`; the create form is where the
  recipe selector is added. Deployment data flows through
  `/api/model-deployments` (react-query hooks in `frontend/src/hooks/use-api.ts`).

## Decisions

1. **Recipe includes `model_path`** (the model is baked in) plus image, compute,
   vLLM flags, env, and placement hints (`node_selector`, `tolerations`,
   `pvc_name`, `pvc_mount_path`). Per-instance fields are **not** stored.
2. **Placement hints included** (`node_selector`/`tolerations`/`pvc_*`) — reusable
   across deployments in a homogeneous cluster.
3. **Pre-fill overwrites** the mapped form fields when a recipe is selected (a
   toast confirms), replacing whatever was there. Non-mapped per-instance fields
   are left for the user.
4. **Super-user only**, consistent with deployments/budgets.
5. **Mutable CRUD, no versioning**; multiple recipes may target the same model
   (uniqueness is on `name`, not model).

## Architecture

### Data model — `custom_serving_recipe`

New table (migration `044_serving_recipe.py`), model at
`backend/app/db/models/custom_serving_recipe.py`:

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | `default=uuid4` |
| `name` | String(256), **unique**, indexed, not null | recipe identifier |
| `description` | Text, null | |
| `model_path` | String(512), not null | weights path |
| `image` | String(512), not null | vLLM image |
| `gpu_count` | Integer, not null, default 1 | |
| `gpu_resource_key` | String(128), not null, default `nvidia.com/gpu` | |
| `cpu_request` / `cpu_limit` | String(32), null | |
| `memory_request` / `memory_limit` | String(32), null | |
| `node_selector` | JSONB, null | dict |
| `tolerations` | JSONB, null | list |
| `pvc_name` | String(256), null | |
| `pvc_mount_path` | String(512), null | |
| `vllm_extra_args` | JSONB, null | list of str |
| `env` | JSONB, null | dict str→str |
| `created_by` / `updated_by` | String(128), null | |
| `created_at` / `updated_at` | timestamptz | server_default now / onupdate |

Column types/defaults mirror `custom_model_deployment` so the mapping is exact.

### Backend API — `backend/app/api/serving_recipes.py`

Router `prefix="/api/admin/serving-recipes"`, super-user gated (`require_super_user`),
registered in `backend/app/main.py` alongside the other routers.

- `GET ""` — list all recipes (newest first).
- `POST ""` — create. 201 with the serialized recipe.
- `GET /{id}` — one recipe (404 if absent).
- `PUT /{id}` — update (full replace of editable fields).
- `DELETE /{id}` — delete (404 if absent).

**Validation** (400 unless noted): `name` required and **unique** (409 on
duplicate), `model_path` and `image` required, `gpu_count >= 0`,
`vllm_extra_args` is a list of strings, `env` is an object of string→string,
`node_selector` is an object, `tolerations` is a list. A shared serializer returns
every column as JSON.

### Frontend

- **Admin page** `frontend/src/app/(app)/admin/recipes/page.tsx`: list + create +
  edit + delete, following the existing admin-page style. The vLLM-flags editor
  reuses the args-list input pattern from the deployment form; env is a key/value
  editor consistent with the deployment form.
- **Apply (pre-fill)** on the deployment create form
  (`frontend/src/app/(app)/admin/deployments/`): a **"Recipe로 채우기" selector**
  at the top. Selecting a recipe sets `model_path`, `image`, `gpu_count`,
  `gpu_resource_key`, `cpu_request/limit`, `memory_request/limit`, `node_selector`,
  `tolerations`, `pvc_name`, `pvc_mount_path`, `vllm_extra_args`, `env` from the
  recipe (overwrite + "recipe 적용됨" toast). The user still provides
  `model_name`, `namespace`/`cluster`, `ingress_host`, `replicas`, then submits via
  the existing create endpoint.
- react-query hooks in `use-api.ts` (`useServingRecipes`, `useCreateServingRecipe`,
  `useUpdateServingRecipe`, `useDeleteServingRecipe`), a `ServingRecipe` type in
  `frontend/src/types/index.ts`, and **en/ko i18n** (equal key counts).

### Recipe → deployment field mapping

| Recipe field | Deployment form field | On apply |
|---|---|---|
| model_path, image, gpu_count, gpu_resource_key | same | overwrite |
| cpu/memory request/limit | same | overwrite |
| node_selector, tolerations, pvc_name, pvc_mount_path | same | overwrite |
| vllm_extra_args, env | same | overwrite |
| — | model_name, namespace, cluster_id, ingress_*, replicas | untouched (user) |

## Error handling

- Duplicate `name` → 409 with a clear message; the create/edit form surfaces it.
- Delete/get of a missing id → 404.
- Applying a recipe never calls the backend — it only sets local form state, so it
  cannot fail server-side; a missing/empty optional field simply clears that form
  field.

## Testing

- **Backend (pytest):** create → 201 and round-trips every field; duplicate name →
  409; missing `model_path`/`image` → 400; bad `vllm_extra_args`/`env` types → 400;
  update replaces fields; delete removes (and 404 after). Follow the fixture style
  in `backend/tests/` (compare failures against the pre-existing baseline).
- **Frontend:** `npx tsc --noEmit` (exit 0) + `npm run lint` (0 new). Manual:
  create a recipe → open deployment create → pick the recipe → mapped fields fill
  (overwrite) → add name/namespace/cluster/ingress → submit succeeds; en/ko both
  render.

## Non-goals (v1)

- Applying recipes to **benchmark self-serving** or **llm-d** (v2).
- Recipe **versioning**/history — mutable CRUD only.
- "Save an existing deployment as a recipe" (reverse direction).
- One-click "create deployment directly from recipe" — v1 is pre-fill only; the
  user reviews and submits.
