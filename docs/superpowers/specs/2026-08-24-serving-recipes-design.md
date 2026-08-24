# vLLM Serving Recipes — Design (v1)

**Date:** 2026-08-24
**Status:** Approved (design)

## Goal

Let an admin save a named, reusable **vLLM serving configuration** (a "recipe")
and launch a model deployment from it through a focused **"Deploy from recipe"
dialog**. A recipe captures the full "how to serve this model" spec — model
weights, image, compute, vLLM flags, env, and placement hints — minus the
per-instance values (name, namespace, cluster, ingress, replicas) that differ
every time; the dialog collects exactly those.

"Recipe" here means a **reusable serving-config template**, not a per-model
recommendation or a benchmark-result catalog.

## Scope

**v1 (this spec):** recipe CRUD (DB + API + admin UI) + apply-to-model-deployment
via a **Deploy-from-recipe dialog**.

**v2 (separate spec, deferred):** applying a recipe to **benchmark self-serving**
and **llm-d stacks**. The user chose the phased path so v1 ships usable value and
each later apply-mapper is added on its own.

### Why a dialog, not form pre-fill

The design first assumed the apply target was pre-filling the deployment *create
form*. Verified during plan prep: **the portal has no deployment create/edit
form** — the deployments list and detail pages are read-only, and
`frontend/src/hooks/use-api.ts` has **no create/update mutation** for
`/api/model-deployments` (only list/get/delete/external/events). The backend
`POST /api/model-deployments` exists but the frontend never calls it (deployments
are created out-of-band via API/CLI). So there is nothing to pre-fill.

Instead, applying a recipe = a small **"Deploy" dialog** launched from the recipe
list. It collects only the per-instance fields, then POSTs to the **existing**
`POST /api/model-deployments` endpoint, merging the recipe's stored fields with
the dialog's instance fields. This delivers real "apply" value without building a
whole deployment form, and the dialog itself is the review-and-submit step.

### Clean boundary

**No change to the deployment backend.** v1 adds the recipe CRUD backend and, on
the frontend, a new `useCreateDeployment` mutation that calls the pre-existing
create endpoint. `recipe fields ∪ dialog fields` cover every field of the backend
`CreateDeploymentRequest` exactly (see the mapping table).

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
- **There is no deployment create/edit form in the portal UI.**
  `frontend/src/app/(app)/admin/deployments/` (list + detail) is read-only, and
  `frontend/src/hooks/use-api.ts` has no create/update mutation for
  `/api/model-deployments` — only list, get, delete, external-serving, events. The
  backend `POST /api/model-deployments` (`create_deployment`) exists and is
  reused; v1 adds the missing frontend create mutation to call it.

## Decisions

1. **Recipe includes `model_path`** (the model is baked in) plus image, compute,
   vLLM flags, env, and placement hints (`node_selector`, `tolerations`,
   `pvc_name`, `pvc_mount_path`). Per-instance fields are **not** stored.
2. **Placement hints included** (`node_selector`/`tolerations`/`pvc_*`) — reusable
   across deployments in a homogeneous cluster.
3. **Apply = a "Deploy" dialog** launched from the recipe list. It collects the
   per-instance fields only (`model_name`, `cluster_id`, `namespace`,
   `ingress_host`/`path`/`class`, `replicas`), then POSTs to the existing create
   endpoint with the recipe's stored fields merged in. The dialog is the
   review-and-submit step; nothing is created until the user confirms.
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
- **Apply — "Deploy from recipe" dialog** on the recipe page: each recipe row has
  a **Deploy** action opening a dialog that collects the per-instance fields only
  (`model_name` required, `cluster_id` optional, `namespace` default `default`,
  `ingress_host` required, `ingress_path` default `/`, `ingress_class` default
  `nginx`, `replicas` default 1). On confirm it POSTs to
  `POST /api/model-deployments` with the recipe's stored serving fields merged in,
  then routes to / toasts the created deployment. The recipe's fields are shown
  read-only in the dialog for confirmation.
- react-query hooks in `use-api.ts`: `useServingRecipes`, `useCreateServingRecipe`,
  `useUpdateServingRecipe`, `useDeleteServingRecipe`, **and a new
  `useCreateDeployment`** (POST `/api/model-deployments`, invalidates
  `["model-deployments"]`) — none exists today. A `ServingRecipe` type in
  `frontend/src/types/index.ts`, and **en/ko i18n** (equal key counts).

### Deploy dialog → `POST /api/model-deployments` body

`CreateDeploymentRequest` fields (verified in `model_deployments.py`) are covered
exactly by the recipe plus the dialog:

| Source | Fields sent to the create endpoint |
|---|---|
| **Recipe (stored)** | model_path, image, gpu_count, gpu_resource_key, cpu_request/limit, memory_request/limit, node_selector, tolerations, pvc_name, pvc_mount_path, vllm_extra_args, env |
| **Dialog (per-instance)** | model_name, cluster_id, namespace, replicas, ingress_host, ingress_path, ingress_class |

Every `CreateDeploymentRequest` field is supplied by exactly one source; no field
is left unset.

## Error handling

- Duplicate recipe `name` → 409 with a clear message; the create/edit form surfaces it.
- Delete/get of a missing recipe id → 404.
- The Deploy dialog submits to the existing create endpoint, which enforces its own
  rules (e.g. duplicate `model_name` is unique-constrained). Any 4xx/5xx from that
  POST is surfaced in the dialog and the deployment is not created; the recipe is
  untouched.

## Testing

- **Backend (pytest):** create → 201 and round-trips every field; duplicate name →
  409; missing `model_path`/`image` → 400; bad `vllm_extra_args`/`env` types → 400;
  update replaces fields; delete removes (and 404 after). Follow the fixture style
  in `backend/tests/` (compare failures against the pre-existing baseline).
- **Frontend:** `npx tsc --noEmit` (exit 0) + `npm run lint` (0 new). Manual:
  create/edit/delete a recipe; open its **Deploy** dialog → the recipe's serving
  fields show read-only → fill model_name/namespace/cluster/ingress_host/replicas →
  confirm → a deployment is created (appears in the deployments list); a duplicate
  model_name surfaces the endpoint error in the dialog; en/ko both render.

## Non-goals (v1)

- Applying recipes to **benchmark self-serving** or **llm-d** (v2).
- Recipe **versioning**/history — mutable CRUD only.
- "Save an existing deployment as a recipe" (reverse direction).
- A full standalone deployment create/edit **form** — v1 only adds the focused
  Deploy-from-recipe dialog (the user still supplies the per-instance fields; it is
  not a blind one-click deploy).
