# Prefill/Decode Disaggregation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A recipe with `serving_mode = "pd"` deploys a prefill pool and a decode pool (vLLM + NixlConnector) behind an llm-d router with the P/D scheduler profile, reconciles per role, and registers the router with LiteLLM. Generic `runtime` options (shm, hostIPC, privileged, extra resources) and a startup probe come along for all recipes.

**Architecture:** `serving_mode`, `pd_config`, `runtime` on recipes/deployments plus `pd_status`, `router_stack_id` on deployments (migration 050). A pure `app/services/pd_serving.py` merges role overrides and renders the two Deployments + Services on top of the existing container builder; `llmd_manifests.default_llmd_values` gains a P/D mode emitting the llm-d guide's EndpointPickerConfig; deployment create auto-creates the router stack through a shared `llmd_stacks.create_stack()` service; the reconciler classifies both roles and gates registration on the router. Frontend: recipe form P/D section, deploy dialog, detail role cards, badges.

**Tech Stack:** FastAPI + pydantic v2 + SQLAlchemy async + Alembic (`uv run pytest --ignore=tests/e2e` from `backend/`, ~20 baseline failures need Postgres); Next.js + next-intl + TanStack Query (`npx tsc --noEmit -p .`, `npx eslint`); kubernetes_asyncio; ArgoCD for the router chart; minikube `portal-test` + CPU mocks for e2e (memory `local-k8s-deploy-testing`).

**Spec:** `docs/superpowers/specs/2026-10-05-pd-disaggregation-design.md`

## Global Constraints

- `serving_mode` values exactly `aggregated` | `pd`; default `aggregated`; `pd` requires `engine == "vllm"`.
- Ports are fixed: prefill vLLM 8000, decode vLLM 8200, decode sidecar 8000, NIXL `pd_config.nixl_port` (default 5600). `--port` and `--kv-transfer-config` in any extra args → 400.
- Merge rules (per role): engine_args role-wins-per-key; extra args base + role; env base-then-role; replicas/gpu_count/gpu_type per role with base defaults.
- Labels: pods carry `llm-ops/managed-by`, `llm-ops/model-name`, `llm-d.ai/model`, `llm-d.ai/role`; Deployment selectors add `llm-ops/pd-role`. Aggregated rendering is byte-for-byte unchanged when `runtime` is null (pin with a test).
- Registration for `pd`: both roles Ready + router healthy/available; `api_base = https://<router ingress host>`.
- Branch: `feat/recipe-engine-split` (depends on probes, GPU profiles, llm-d linkage there). One commit per task with the session's attribution trailer; no push unless told.

---

### Task 1: Migration 050 + ORM + startup probe

**Files:**
- Create: `backend/migrations/versions/050_pd_serving.py` (down_revision `049_gpu_profiles`)
- Modify: `backend/app/db/models/custom_serving_recipe.py`, `custom_model_deployment.py`
- Modify: `backend/app/services/serving_probes.py` (`ProbesSpec.startup`, `STARTUP_DEFAULTS_PD`, `render_probes(dep, startup_default=None)`)
- Test: `backend/tests/test_pd_serving.py` (new; schema + probes), additions to `tests/test_serving_probes.py`

- [ ] **Step 1: Failing tests** — columns/defaults (`serving_mode` server_default `aggregated` on both; `pd_config`, `runtime` nullable; `pd_status`, `router_stack_id` FK SET NULL, `router_stack_created` default false on deployments); `validate_probes({"startup": {"period_seconds": 30}})` kept; `render_probes` emits `startupProbe` only when set or when a default is passed; aggregated rows without startup render exactly as before.
- [ ] **Step 2–4:** FAIL → implement → PASS, ruff clean. **Step 5: Commit** `feat(pd): serving_mode/pd_config/runtime columns, pd_status + router link, startup probe`.

---

### Task 2: `pd_serving` — validation, role merge, manifests, runtime

**Files:**
- Create: `backend/app/services/pd_serving.py`
- Modify: `backend/app/services/model_deployment_manifests.py` (`build_deployment` factored into `build_container(dep, *, port, extra_env, extra_ports)` + `build_pod_spec(...)` so both modes share one path; `build_all` dispatches on `serving_mode`; `k8s_resource_names` returns prefill/decode names for `pd`; `runtime` rendering)
- Modify: `backend/app/clients/k8s.py` (`delete` iterates every name in the dict by kind; keep the old 3-key shape working)
- Modify: `backend/app/services/benchmark_serving.py` (clone copies `runtime`; `build_ephemeral_deployment` on a `pd` base raises `ValueError("pd recipes cannot be cloned for ephemeral benchmarks")`)
- Test: `backend/tests/test_pd_serving.py` (append)

**Interfaces:**
- `PdConfig` (pydantic): `prefill: RoleOverride`, `decode: RoleOverride`, `nixl_port: int = 5600`, `kv_transfer_extra: dict = {}`, `sidecar_image: str | None`, `router: {peak_prefill_throughput: int = 33821, prefix_tokens_to_match: int = 131072}`; `RoleOverride`: `replicas: int = 1`, `gpu_count: int | None`, `gpu_type: str | None`, `engine_args: dict | None`, `vllm_extra_args: list[str] | None`, `env: dict | None`. `validate_pd_config(value, *, engine, base_extra_args) -> dict` raises `ValueError` on forbidden flags / non-vllm.
- `Runtime` (pydantic): `shm_size_gi: int | None`, `host_ipc: bool = False`, `privileged: bool = False`, `extra_resources: dict[str, str] = {}`; `validate_runtime`.
- `kv_transfer_config(role, extra) -> str` (JSON with `kv_connector: NixlConnector`, `kv_role` forced).
- `role_view(dep, role) -> RoleSpec(name, port, replicas, gpu_count, gpu_type, engine_args, extra_args, env, labels, selector)`.
- `build_pd_deployments(dep, *, sidecar_image) -> list[dict]`, `build_pd_services(dep) -> list[dict]`, `pd_resource_names(dep) -> dict`.

- [ ] **Step 1: Failing tests** — merge rules; kv JSON for both roles incl. `kv_transfer_extra` and that `kv_role` from extras is ignored; forbidden flags; prefill args start with `--model <p> --port 8000 --kv-transfer-config <json>`, decode with `--port 8200`; env has `VLLM_NIXL_SIDE_CHANNEL_HOST` via `fieldRef status.podIP` and the port; container ports (`http`, `nixl`); decode has `initContainers[0]` = routing-proxy with the image, args, `restartPolicy: Always`, port 8000 named `sidecar`; readiness/liveness on 8200 for decode, 8000 for prefill; startup probe present with PD defaults when the recipe sets none; labels/selectors per role; services (decode → 8000, prefill → 8000); `build_all` for `pd` has no Ingress; `runtime` renders shm/hostIPC/privileged/rdma in both modes and aggregated output is unchanged when `runtime` is null (compare to a frozen expected dict from the current builder); `k8s_resource_names` for `pd`; benchmark clone raises.
- [ ] **Step 2–4:** FAIL → implement → PASS (also `tests/test_serving_engines.py`, `test_serving_probes.py`, `test_self_serving_bench.py` unchanged), ruff clean.
- [ ] **Step 5: Commit** `feat(pd): render prefill/decode Deployments with NIXL plumbing and the routing sidecar; generic runtime options`.

---

### Task 3: Router values for P/D + stack creation as a service

**Files:**
- Modify: `backend/app/config.py` (`llmd_sidecar_image_registry = "ghcr.io"`, `llmd_sidecar_image_repository = "llm-d/llm-d-router-disagg-sidecar"`, `llmd_sidecar_image_tag = "main"`)
- Modify: `backend/app/services/llmd_manifests.py` (`default_llmd_values(..., serving_mode="aggregated", pd_router=None)`, `PD_EPP_CONFIG_TEMPLATE`)
- Create: `backend/app/services/llmd_stacks.py` — move the body of `api/llmd.py::create_stack` (row + ArgoCD Application + ingress apply) into `async def create_stack(db, user, *, name, target_model_name, cluster_id, namespace, values, ...) -> CustomLlmdStack`; `api/llmd.py` calls it (behaviour unchanged, tests in `test_llmd.py` must stay green)
- Test: `backend/tests/test_llmd_manifests.py` (append), `tests/test_llmd.py` (unchanged expectations)

- [ ] **Step 1: Failing tests** — P/D values: `router.epp.pluginsConfigFile == "pd-config.yaml"`, the custom config parses as YAML to an `EndpointPickerConfig` with the plugin list and two profiles from the spec, `peakPrefillThroughput`/`maxPrefixTokensToMatch` substituted, `modelServers.matchLabels == {"llm-d.ai/model": model}`, `targetPorts == [{"number": 8000}]`; aggregated values unchanged.
- [ ] **Step 2–4:** FAIL → implement → PASS incl. `tests/test_llmd.py`. **Step 5: Commit** `feat(llmd): P/D router values; stack creation extracted to a service`.

---

### Task 4: Reconciler for two roles + router-gated registration

**Files:**
- Modify: `backend/app/jobs/reconcile_deployments.py` (`_observe_pd(k8s, dep)`, `derive_pd_status(prefill, decode) -> (status, message)`, registration gate `_router_ready(db, dep)`, `api_base` from the stack)
- Modify: `backend/app/services/deployment_status.py` (pure `derive_pd_status`)
- Test: `backend/tests/test_reconcile_pd.py` (new; mock K8s + mock DB like existing reconciler tests — check `tests/` for the current reconciler test file and mirror its fixtures)

- [ ] **Step 1: Failing tests** — status table: (Ready, Ready) → Ready; (Ready, Pending) → Pending `decode: No ready pods yet`; (Updating, Ready) → Updating; (Failed, Ready) → Failed; missing decode Deployment → Unhealthy `decode: Missing`; `pd_status` stored per role; `ready_replicas/replicas` mirror decode; registration not called while the router is unhealthy, called once with `api_base == f"https://{stack.ingress_host or f'{argo_app_name}.{domain}'}"` when healthy; aggregated path untouched.
- [ ] **Step 2–4:** FAIL → implement → PASS. **Step 5: Commit** `feat(pd): reconcile prefill/decode roles and register the router with LiteLLM`.

---

### Task 5: APIs

**Files:**
- Modify: `backend/app/api/serving_recipes.py` (fields + validators), `backend/app/api/model_deployments.py` (create: copy fields, per-role replicas, skip ingress for `pd`, auto-create/link router stack, `router_stack_created`; read: new fields + `router_stack` summary; delete: both roles + services, then the auto-created stack via `llmd_stacks.delete_stack`; importer warning `pd_detected`), `backend/app/api/serving_overview.py` (summary), `backend/app/api/benchmarks.py` (`serving_target_url` for `pd` → `-epp` Service; 400 for ephemeral clones of `pd`)
- Modify: `backend/app/services/recipe_import.py` (drop `--kv-transfer-config` into a warning with the role)
- Tests: `backend/tests/test_pd_api.py` (new), additions to `test_recipe_import.py`, `test_serving_overview.py`

- [ ] **Step 1: Failing tests** — recipe create `pd` with sglang → 400; forbidden flag → 400; deploy `pd` creates the stack (`create_stack` patched) and stores `router_stack_id` + `router_stack_created`; with `router_stack_id` given → no creation; stack failure → 201 with `status_message` set; delete calls `k8s.delete` with both role names and `delete_stack` only when `router_stack_created`; read exposes `pd_status`/`router_stack`; overview summary; importer warning; benchmark target URL and the 400.
- [ ] **Step 2–4:** FAIL → implement → PASS; full suite at baseline. **Step 5: Commit** `feat(pd): recipe/deployment APIs, auto-created router, delete, overview, benchmark target`.

---

### Task 6: Frontend

**Files:**
- Modify: `frontend/src/types/index.ts` (`ServingMode`, `PdRoleOverride`, `PdConfig`, `RuntimeOptions`, `PdStatus`, fields on recipe/deployment/body; `probes.startup`)
- Modify: `frontend/src/components/serving-recipe-form.tsx` (서빙 방식 toggle; `PdSection` with two `RoleCard`s, KV 전송, 라우터 groups; 런타임 group in the advanced block; startup probe fields; engine locked to vLLM in `pd`)
- Create: `frontend/src/components/pd-role-card.tsx`
- Modify: `frontend/src/components/deploy-from-recipe-dialog.tsx` (P/D replicas ×2, router ingress host instead of ingress fields, body fields)
- Modify: `frontend/src/app/(app)/admin/deployments/page.tsx` (`P/D` badge, `D r/d · P r/d`), `[id]/page.tsx` (role cards, router card, per-role launch args), `admin/serving/page.tsx` (badge), `admin/benchmarks/new/page.tsx` (note for P/D targets), `admin/llmd/[id]/page.tsx` (role chip on linked servers)
- Modify: `frontend/src/lib/recipe-drafts.ts` (carry new fields), `messages/en.json`, `ko.json` (`servingRecipes.pd.*`, `adminDeployments.pd*`, `warn.pd_detected`)

- [ ] **Step 1:** Types + i18n. **Step 2:** Recipe form + role card. **Step 3:** Dialog, list, detail, home, llm-d detail, benchmark note. **Step 4:** `tsc`/`eslint` clean; Playwright smoke with mocked proxy (form toggle shows two role cards; dialog shows two replica inputs and no ingress fields for a `pd` recipe; detail renders role cards from a mocked `pd_status`).
- [ ] **Step 5: Commit** `feat(pd): recipe form P/D section, deploy dialog, deployment role cards and badges`.

---

### Task 7: Live check on minikube (best effort) + docs

- [ ] Rebuild (`docker compose up -d --build backend backend-worker frontend`); kubeconfig per memory; ArgoCD present in `portal-test`.
- [ ] Pre-pull on the laptop and load into minikube: `ghcr.io/llm-d/llm-d-router-disagg-sidecar:<tag>` (arm64 availability to confirm; if absent, note and skip the sidecar assertion by setting `pd_config.sidecar_image` to the `mock-vllm:cpu` image with a no-op command — record this as a limitation).
- [ ] Create a P/D recipe on `mock-vllm:cpu` (`facebook/opt-125m`, prefill 2×, decode 1×, gpu 0, nixl 5600), deploy as `pd-test`. Assert via kubectl: two Deployments with the specified args/env/ports/labels, decode pod has the sidecar, Services exist, no Ingress; the router Application exists in ArgoCD with the P/D config; `pd_status` both Ready; LiteLLM registration points at the router host.
- [ ] Route a request: port-forward `svc/pd-test-router-epp 8080:80` and POST `/v1/chat/completions`; expect the mock reply (or document where the sidecar stops without NIXL).
- [ ] Delete `pd-test` from the portal: both Deployments, Services and the router Application gone; clean the LiteLLM model + catalog row by hand (known gap).
- [ ] Spec → implemented; answers to open questions; README paragraph "P/D 분리 서빙".
- [ ] **Commit** `docs(pd): verified on minikube; spec marked implemented`.
