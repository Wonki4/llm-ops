# GPU Type Profiles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Operators pick a GPU type by name on recipes and at deploy time; the portal resolves the name through a per-cluster profile (configurable node label key/value, tolerations, GPU resource key) into the placement fields the manifest builder already renders. Node discovery shows which label values exist and how many GPUs are free.

**Architecture:** New table `custom_gpu_profile` + `gpu_label_key` on clusters + `gpu_type` on recipes/deployments (migration 049). A pure resolver `app/services/gpu_profiles.py` merges a profile into explicit placement; `model_deployments.create` and the benchmark ephemeral builder both call it. `K8sClient.list_gpu_nodes` feeds a discovery endpoint. Frontend: profile table on the cluster settings page, GPU type pickers on the recipe form, deploy dialog (which also gains a real cluster select) and benchmark form.

**Tech Stack:** FastAPI + pydantic v2 + SQLAlchemy async + Alembic (`uv run pytest --ignore=tests/e2e` from `backend/`, ~20 baseline failures need Postgres); Next.js app router + next-intl + TanStack Query (`npx tsc --noEmit -p .`, `npx eslint <files>` from `frontend/`); kubernetes_asyncio client; minikube `portal-test` for live checks (see memory `local-k8s-deploy-testing`).

**Spec:** `docs/superpowers/specs/2026-10-03-gpu-type-profiles-design.md`

## Global Constraints

- Profile name regex `^[a-z0-9][a-z0-9.-]*$`, max 64, unique per cluster (`cluster_id` null = portal default cluster, NULLS NOT DISTINCT).
- Resolution precedence: profile label pair and `gpu_resource_key` override the recipe's explicit values for the same keys; other explicit nodeSelector keys stay; tolerations are the union de-duplicated on (key, operator, value, effect).
- `gpu_type` is optional everywhere; `gpu_count = 0` never requires it. A chosen cluster that has no profiles behaves exactly as today.
- Resolved values are stored on the deployment row in the existing columns; editing a profile never touches running deployments.
- No node labelling from the portal. Discovery is read-only with a 5 s timeout and never blocks a page.
- Branch: continue on `feat/recipe-engine-split` (depends on the recipe form, engine split and probes work there). Commit per task with the session's attribution trailer; do not push unless told.

---

### Task 1: Migration 049 + ORM

**Files:**
- Create: `backend/migrations/versions/049_gpu_profiles.py` (down_revision `048_serving_probes`)
- Create: `backend/app/db/models/custom_gpu_profile.py`; register in `backend/app/db/models/__init__.py`
- Modify: `backend/app/db/models/custom_k8s_cluster.py` (`gpu_label_key`), `custom_serving_recipe.py` and `custom_model_deployment.py` (`gpu_type`)
- Test: `backend/tests/test_gpu_profiles.py` (new; later tasks append)

- [ ] **Step 1: Failing test** — columns/defaults exist (`custom_k8s_cluster.gpu_label_key` server_default `gpu-type`; `gpu_type` nullable on both tables; `CustomGpuProfile.__table__` has the unique constraint on `(cluster_id, name)` with `postgresql_nulls_not_distinct=True`, mirroring `custom_external_serving`).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS, ruff clean.
- [ ] **Step 5: Commit** `feat(gpu): GPU profile table, cluster label key, gpu_type on recipes and deployments`.

---

### Task 2: Resolver

**Files:**
- Create: `backend/app/services/gpu_profiles.py`
- Modify: `backend/app/services/benchmark_serving.py` (replace the `gpu-type` fold with the resolver; keep a compat path)
- Test: append to `backend/tests/test_gpu_profiles.py`; adjust `tests/test_self_serving_bench.py` expectations

**Interfaces:**
- `Placement = dict(node_selector: dict | None, tolerations: list | None, gpu_resource_key: str)`
- `resolve_placement(profile | None, cluster_label_key: str, *, node_selector, tolerations, gpu_resource_key) -> Placement`
- `validate_profile_name(name) -> str` (raises `ValueError`)
- `build_ephemeral_deployment(..., overrides)`: `overrides["gpu_profile"]` (a resolved profile object or None) replaces `overrides["gpu_type"]` string handling; when only the legacy string is given and `overrides.get("gpu_profile") is None`, keep writing `node_selector["gpu-type"] = value` and emit a `DeprecationWarning` via logger.

- [ ] **Step 1: Failing tests** — profile wins on its label key, explicit other keys kept, custom `label_key` override, toleration union + de-dupe, resource key from profile, `None` profile passthrough, name regex; benchmark builder with a profile and with the legacy string.
- [ ] **Step 2–4:** FAIL → implement → PASS, ruff clean. **Step 5: Commit** `feat(gpu): placement resolver; benchmarks resolve gpu_type through profiles`.

---

### Task 3: Node discovery in the K8s client

**Files:**
- Modify: `backend/app/clients/k8s.py` (`list_gpu_nodes(label_key, resource_keys)`)
- Test: `backend/tests/test_k8s_gpu_nodes.py` (new; fake `CoreV1Api` like `test_bench_external_clone.py::_k8s_with`)

**Interfaces:** returns `[{name, label_value|None, schedulable, allocatable: {key: int}, requested: {key: int}}]` for nodes that have any of `resource_keys` in `status.allocatable`. Requested = sum of container `resources.requests[key]` over pods on that node whose phase is not Succeeded/Failed (one `list_pod_for_all_namespaces` call, grouped by `spec.node_name`). `schedulable = not spec.unschedulable`.

- [ ] **Step 1: Failing tests** — two GPU nodes (one labelled, one not, one cordoned), one CPU node filtered out, requests summed only for running/pending pods, int parsing of quantities.
- [ ] **Step 2–4:** FAIL → implement → PASS, ruff on the new lines only (file has pre-existing long-line findings). **Step 5: Commit** `feat(k8s): list GPU nodes with label value, allocatable and requested counts`.

---

### Task 4: Backend API

**Files:**
- Create: `backend/app/api/gpu_profiles.py` (router prefix `/api/admin/k8s-clusters`, routes `/{cluster_id}/gpu-profiles[...]` and `/{cluster_id}/gpu-nodes`; `cluster_id == "default"` → `None`), plus `GET /api/admin/gpu-types`; register in `app/main.py`
- Modify: `backend/app/api/k8s_clusters.py` (`gpu_label_key` on create/update/serialize)
- Modify: `backend/app/api/serving_recipes.py` (`gpu_type` in body + serialize, validated by `validate_profile_name` when set)
- Modify: `backend/app/api/model_deployments.py` (create: `gpu_type` → load profile for `(cluster_id, name)`; 400 with known names when missing and the cluster has profiles; apply `resolve_placement` before building the row; serialize `gpu_type`; update: `gpu_type` re-resolves the same way)
- Modify: `backend/app/api/benchmarks.py` (resolve `overrides.gpu_type` by name on the run's cluster → pass `gpu_profile`)
- Modify: `backend/app/api/serving_overview.py` (deployment entries carry `gpu_type`)
- Modify: `backend/app/services/recipe_import.py` + `model_deployments.external_recipe_draft` (pass the cluster's label key and its profiles; when the live nodeSelector has that key and the value matches an enabled profile, set `draft["gpu_type"]` and remove the key from `draft["node_selector"]`)
- Tests: `backend/tests/test_gpu_profiles_api.py` (new, mock_db pattern), additions to `test_model_deployments_engine.py`, `test_recipe_import.py`, `test_serving_overview.py`

- [ ] **Step 1: Failing tests** — CRUD (409 on duplicate name per cluster, `default` cluster id, 403 non-super-user); `gpu-nodes` grouping and `errors` on `K8sNotConfigured`; `gpu-types` union with availability; deploy with known type stores resolved columns + `gpu_type`, unknown type → 400 listing names, cluster without profiles → explicit values untouched; benchmark override resolution; importer pre-fills `gpu_type`.
- [ ] **Step 2–4:** FAIL → implement → PASS; `uv run pytest --ignore=tests/e2e -q` shows no new failures vs. baseline. **Step 5: Commit** `feat(gpu): profile CRUD, node discovery, gpu_type on recipe/deploy/benchmark APIs`.

---

### Task 5: Frontend — types, hooks, cluster settings page

**Files:**
- Modify: `frontend/src/types/index.ts` (`GpuProfile`, `GpuProfileInput`, `GpuNodeGroup`, `GpuTypeOption`; `gpu_type` on `ServingRecipe`, `ModelDeployment`, `CreateDeploymentBody`; `gpu_label_key` on the cluster type)
- Modify: `frontend/src/hooks/use-api.ts` (`useGpuProfiles(clusterId)`, create/update/delete mutations, `useGpuNodes(clusterId)`, `useGpuTypes()`)
- Modify: the cluster settings page under `frontend/src/app/(app)/admin/settings/` (find the cluster card/table) — add "GPU 라벨 키" to the cluster edit form and a "GPU 타입 프로필" section per cluster: table + create/edit dialog (name, label key override, label value, resource key, tolerations textarea reusing the recipe form's `linesToTolerations`/`tolerationsToLines` — export them from a small `frontend/src/lib/placement.ts`), and a "노드에서 가져오기" panel listing discovered label values with allocatable/requested/available and a "프로필 만들기" button per value, plus the unlabelled GPU nodes.
- Modify: `frontend/messages/en.json`, `ko.json` (namespace `gpuProfiles`, plus keys in `adminClusters`/settings namespace as the page uses)

- [ ] **Step 1:** Types + hooks + i18n (JSON edited via a Python round-trip script).
- [ ] **Step 2:** Move the toleration/nodeSelector line parsers from `serving-recipe-form.tsx` into `lib/placement.ts` (no behaviour change; keep the form importing them).
- [ ] **Step 3:** Cluster page UI. `npx tsc --noEmit -p .`, `npx eslint` on changed files.
- [ ] **Step 4:** Playwright smoke with mocked `/api/proxy/admin/k8s-clusters/*` (profiles list, nodes panel, create dialog posts the expected body); screenshot under `.playwright-mcp/`.
- [ ] **Step 5: Commit** `feat(gpu): cluster settings — GPU label key, profile table, node discovery panel`.

---

### Task 6: Frontend — recipe form, deploy dialog, benchmark form, detail pages

**Files:**
- Modify: `frontend/src/components/serving-recipe-form.tsx` (GPU section: "GPU 타입" combobox from `useGpuTypes()` with free text; move nodeSelector/tolerations/`gpu_resource_key` under a collapsible "고급: 직접 지정" with the override hint)
- Modify: `frontend/src/components/deploy-from-recipe-dialog.tsx` (cluster `<select>` from the clusters hook with "포털 기본" as the null option; GPU type `<select>` filtered to that cluster's enabled profiles, labels like `a100-80g · 6/8 available`, pre-selected from `recipe.gpu_type`, required when `gpu_count > 0` and the cluster has profiles; a read-only preview of the resolved nodeSelector/tolerations/resource key; body sends `gpu_type`)
- Modify: `frontend/src/app/(app)/admin/benchmarks/new/page.tsx` (its GPU type input → the same select, keeping free text when the cluster has no profiles)
- Modify: `frontend/src/app/(app)/admin/deployments/page.tsx`, `[id]/page.tsx`, `admin/serving/page.tsx` (type chip; detail shows `gpu_type` and the resolved label pair, with a "프로필 없음" hint when the name no longer resolves)
- Modify: `frontend/src/lib/recipe-drafts.ts` (`gpu_type` passthrough), `recipes/new/page.tsx` (no change expected beyond types)
- Modify: `frontend/messages/*.json`

- [ ] **Step 1–2:** Implement; `tsc`/`eslint` clean.
- [ ] **Step 3:** Playwright smoke (mocked proxy): recipe form shows the combobox; deploy dialog disables submit until a type is chosen for a GPU recipe on a cluster with profiles; benchmark form select renders.
- [ ] **Step 4: Commit** `feat(gpu): GPU type pickers on recipe, deploy, benchmark; type chips on deployments`.

---

### Task 7: Live verification on minikube + docs

- [ ] Rebuild: `docker compose up -d --build backend backend-worker frontend` (migration 049 applies on backend start). Ensure `/tmp/portal-test-incluster.kubeconfig` exists (memory `local-k8s-deploy-testing`).
- [ ] `kubectl --context portal-test label node portal-test gpu-type=cpu-mock --overwrite`. In the portal: cluster settings → default cluster → "노드에서 가져오기" shows `cpu-mock` (allocatable 0 since no GPU resource; confirm the group still appears because the label matches — if the implementation filters to GPU-resource nodes only, use the "unlabelled" rule's counterpart and note it) → create profile `cpu-mock` with resource key `nvidia.com/gpu`, toleration `cpu-only=true:NoSchedule`.
- [ ] Recipe `cpu-demo 레시피` → set `gpu_type = cpu-mock` (gpu_count stays 0) → deploy as `gpu-type-test` on the default cluster → assert the Deployment has `nodeSelector: {gpu-type: cpu-mock}` and the toleration, pod Ready, detail page shows the chip and the resolved pair.
- [ ] Deploy with `gpu_type = h100` (no such profile) → 400 and the dialog shows the message with known names.
- [ ] Clean up: delete the deployment and its LiteLLM model + catalog row (deletion leaves them; known gap), delete the profile, remove the node label.
- [ ] `docs/superpowers/specs/2026-10-03-gpu-type-profiles-design.md` → status implemented; record answers to the open questions; one paragraph in `README.md` under serving.
- [ ] **Commit** `docs(gpu): GPU type profiles verified on minikube`.
