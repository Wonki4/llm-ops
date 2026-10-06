# Prefill/Decode disaggregated serving (P/D) — Design

Date: 2026-10-05
Status: implemented 2026-10-06 on feat/recipe-engine-split (commits 916b908, 86431b5, fd49ae5, 13b228c, eb93420, 8ec181b); verified on minikube portal-test with mock-vllm:cpu — two pools + routing sidecar up, router stack auto-created, pd_status reconciled, LiteLLM registration via the router host (see "Verification" below)
Related: `backend/app/services/model_deployment_manifests.py`, `llmd_manifests.py`, `jobs/reconcile_deployments.py`, llm-d guide `guides/pd-disaggregation` (fetched 2026-10-05), vLLM NixlConnector

## Goal

Deploy one model as a **prefill pool + decode pool** behind an llm-d router,
from a single recipe, with the same lifecycle the portal already gives
aggregated deployments (create, reconcile, register with LiteLLM, benchmark,
delete). The operator describes the two roles' differences once (replicas,
GPU count, per-role engine args and env); the portal renders the two
Deployments, the KV-transfer flags, the NIXL side channel, the decode
routing sidecar and the router's P/D scheduler config.

Reference input (operator's docker runs for `zai-org/GLM-5.3-Flash`):

| | prefill | decode |
|---|---|---|
| `--kv-transfer-config` | `{"kv_connector":"NixlConnector","kv_role":"kv_producer","kv_load_failure_policy":"fail"}` | same with `kv_role":"kv_consumer"` |
| `--compilation-config` | `{"cudagraph_mm_encoder": true}` | `{"cudagraph_mode":"FULL_DECODE_ONLY"}` |
| extra | — | `--max-num-seqs 512` |
| shared flags | `--tensor-parallel-size 8 --no-disable-hybrid-kv-cache-manager --kv-cache-dtype fp8 --tool-call-parser glm47 --enable-auto-tool-choice --reasoning-parser glm47` | |
| shared env | `VLLM_ENGINE_READY_TIMEOUT_S=3600 VLLM_SSM_CONV_STATE_LAYOUT=DS VLLM_KV_CACHE_LAYOUT=HND` | |
| per-instance env | `VLLM_NIXL_SIDE_CHANNEL_HOST=<node ip>`, `VLLM_NIXL_SIDE_CHANNEL_PORT=5557/5558` | |
| runtime | `--gpus all --privileged --ipc=host`, HF cache mount | |

## Scope

In:

- `serving_mode = "pd"` on recipes and deployments with a structured
  `pd_config` (per-role overrides) and generic `runtime` options (shm size,
  hostIPC, privileged, extra resources) that aggregated recipes can use too.
- Manifest rendering for both roles exactly as the llm-d recipe does it:
  role labels, KV-transfer flags, NIXL port + side-channel env from the pod
  IP, decode on port 8200 behind the `llm-d-router-disagg-sidecar` native
  sidecar on 8000, `/dev/shm` memory volume, startup probe.
- An llm-d router stack created with the P/D scheduler profile
  (`disagg-profile-handler`, prefill/decode filters) targeting both pools.
- Reconciler: per-role status, overall status, LiteLLM registration through
  the router's ingress (not a per-deployment ingress).
- UI: recipe form "서빙 방식" toggle with a P/D section, deploy dialog, deployment
  detail with role cards + router link, serving home badge, benchmark target.
- `probes.startup` (third probe) since P/D servers load for minutes.

Out (see Non-goals): SGLang P/D, DisaggregatedSet rollouts, MooncakeConnector,
multi-node (TP across nodes), heterogeneous prefill/decode models, xPyD
autoscaling, importing an external P/D pair as a recipe.

## Background — verified facts

From `llm-d/llm-d@main` (`guides/pd-disaggregation`, `guides/recipes/modelserver/base/single-host/pd`, `guides/recipes/router`):

- Prefill Deployment: labels `llm-d.ai/role: prefill` (+ the guide's shared
  labels incl. `llm-d.ai/model`), container `modelserver`, ports `8000`
  (modelserver) and `5600` (nixl), probes on `/health` (liveness) and
  `/v1/models` (readiness, startup; startup `failureThreshold: 120`,
  `periodSeconds: 30`). vLLM args: `vllm serve <model> … --kv-transfer-config
  '{"kv_connector":"NixlConnector","kv_role":"kv_producer","kv_buffer_device":"cuda","kv_connector_extra_config":{"backends":["UCX"]}}' --no-disable-hybrid-kv-cache-manager`.
  Env: `VLLM_NIXL_SIDE_CHANNEL_HOST` from `fieldRef: status.podIP`,
  `HF_TOKEN` from a secret, `VLLM_HTTP_TIMEOUT_KEEP_ALIVE=120`.
  Volumes: `/dev/shm` emptyDir `medium: Memory, sizeLimit: 20Gi`, plus
  emptyDirs for torch-compile / vllm-config / triton caches.
- Decode Deployment: labels `llm-d.ai/role: decode`; vLLM gets `--port=8200`,
  container ports `8200` + `5600`; `kv_role: kv_consumer`. A **native sidecar**
  (`initContainers` with `restartPolicy: Always`) named `routing-proxy`,
  image `ghcr.io/llm-d/llm-d-router-disagg-sidecar:<tag>`, args
  `--port=8000 --kv-connector=nixlv2 --zap-log-level=1 --secure-proxy=false`,
  listens on `8000`. The router targets decode pods on 8000; the sidecar
  calls the prefill pod the EPP chose, then the local vLLM on 8200.
- Router values (`router/pd-disaggregation.values.yaml`): `router.epp.
  pluginsConfigFile: pd-config.yaml` + `pluginsCustomConfig` holding an
  `EndpointPickerConfig` with plugins `always-disagg-pd-decider`,
  `disagg-profile-handler`, `prefill-filter`, `decode-filter`,
  `approx-prefix-cache-producer`, `inflight-load-producer`,
  `prefix-cache-affinity-filter` (`peakPrefillThroughput`), `token-load-scorer`,
  `active-request-scorer`, `max-score-picker`, and two
  `schedulingProfiles` (`prefill`, `decode`). `router.modelServers.
  matchLabels` selects **both** pools by a shared label. Base values pin
  `router.modelServers.protocol: http`, Envoy sidecar args, EPP resources.
- The portal today: aggregated-only `build_deployment` (one container, args
  from `container_launch`, probes from `serving_probes`, labels
  `llm-ops/managed-by`, `llm-ops/model-name`, pod label `llm-d.ai/model`),
  `build_service` (80 → 8000), `build_ingress`; reconciler classifies one
  Deployment and registers `https://<ingress_host>` with LiteLLM on first
  Ready; llm-d stacks are separate rows (`custom_llmd_stack`) deployed via
  ArgoCD with `default_llmd_values()` (EPP image, `modelServers.matchLabels
  = llm-d.ai/model=<model>`, `targetPorts: 8000`) and linked to servers by
  label selector; `K8sClient.create_or_patch` handles Deployment / Service /
  Ingress only; `k8s_resource_names` gives `<model>-deployment/-service/
  -ingress`.
- Settings: `llmd_chart_* = llm-d-router-standalone v0.9.0`, EPP image
  `ghcr.io/llm-d/llm-d-router-endpoint-picker:v0.9.0`, ingress host
  `{argo_app_name}.{llmd_ingress_domain}`.
- Our GPU type profiles (2026-10-05) resolve `gpu_type` into placement at
  deploy time; probes are `probes.readiness/liveness`.

## Decisions

1. **P/D is a serving mode of a recipe, not a new entity.** A recipe keeps
   its shared base (image, model, engine args, env, GPU type, probes,
   runtime) and adds `pd_config` with per-role overrides. One deployment
   row per P/D deployment; the two K8s Deployments are its children.
2. **vLLM + NixlConnector only in v1.** `engine` must be `vllm`; the
   connector is fixed to `NixlConnector` with `kv_role` set by the portal.
   Extra connector fields (`kv_load_failure_policy`, `kv_buffer_device`,
   `kv_connector_extra_config`) come from `pd_config.kv_transfer_extra` and
   are merged into the JSON the portal emits. Users never type
   `--kv-transfer-config` themselves; if it appears in extra args it is
   rejected (400).
3. **Per-role merge rules:** `engine_args = {**base, **role}` (role wins per
   key), `vllm_extra_args = base + role` (concatenated, role last),
   `env = {**base, **role}`, `replicas`/`gpu_count`/`gpu_type` per role with
   base as default. `gpu_resource_key`, probes, runtime are shared.
4. **The portal owns the plumbing flags/env:** `--port` (8000 prefill, 8200
   decode), `--kv-transfer-config`, `VLLM_NIXL_SIDE_CHANNEL_HOST`
   (downward API `status.podIP`), `VLLM_NIXL_SIDE_CHANNEL_PORT`
   (`pd_config.nixl_port`, default 5600), container ports, the decode
   sidecar, `/dev/shm`. The operator's `--ipc=host --privileged` map to
   `runtime.host_ipc` / `runtime.privileged`; `--gpus all` is the GPU count.
5. **A P/D deployment has no ingress of its own; the router is the entry.**
   Creating a P/D deployment also creates an llm-d stack (ArgoCD) named
   `<model>-router` with P/D values selecting `llm-d.ai/model=<model>`,
   unless the request names an existing stack. `custom_model_deployment.
   router_stack_id` links them; deleting the deployment deletes the stack
   it created (never a pre-existing one).
6. **Status and registration:** per-role status in `pd_status` jsonb;
   overall = `Failed`/`Unhealthy` if either role is; `Ready` when both
   roles have all desired replicas ready; `Updating` when both have ≥1
   ready; else `Pending`. LiteLLM registration happens on first overall
   Ready **and** router stack healthy (or, without ArgoCD status, when the
   router Deployment is available), with `api_base = https://<router
   ingress host>`. `served_model_name` stays the model path basename.
7. **Router P/D values come from the portal, editable like any stack.**
   `default_llmd_values(..., serving_mode="pd", peak_prefill_throughput=…)`
   emits the plugin config verbatim from the llm-d guide; `targetPorts`
   stays 8000 (prefill direct, decode via sidecar). `pd_config.router`
   holds `peak_prefill_throughput` (default 33821, the guide's value) and
   `prefix_tokens_to_match` (131072).
8. **Sidecar image is a setting** (`llmd_sidecar_image_repository =
   ghcr.io/llm-d/llm-d-router-disagg-sidecar`, `llmd_sidecar_image_tag =
   main` — the guide pins `main`; air-gapped clusters set the registry),
   overridable per recipe via `pd_config.sidecar_image`.
9. **`runtime` is generic.** `{"shm_size_gi": int|null, "host_ipc": bool,
   "privileged": bool, "extra_resources": {"rdma/ib": "1"}}` on recipes and
   deployments; aggregated deployments render it too (shm volume, hostIPC,
   securityContext, extra resource limits). Benchmark clones copy it.
10. **`probes.startup`** joins readiness/liveness (same `ProbeSpec`); default
    none for aggregated, and for P/D a default startup probe of
    `/v1/models`, period 30 s, failure threshold 120 (60 min) as in the guide.
11. **Benchmarks against a P/D deployment target the router**, not a pool:
    `serving_target_url` resolves to the stack's `-epp` Service. Ephemeral
    (self-serving) benchmark clones of a P/D recipe are out of scope in v1
    (400 with a clear message).
12. **Pods keep the existing portal labels** (`llm-ops/managed-by`,
    `llm-ops/model-name`) so overview/external scan/llm-d link logic keeps
    working, plus `llm-d.ai/model` and `llm-d.ai/role`. The Deployment
    selectors add `llm-ops/pd-role` so prefill/decode never overlap.

## Architecture

### Data model (migration 050)

```
custom_serving_recipe / custom_model_deployment
  + serving_mode   varchar(16) not null default 'aggregated'   -- 'aggregated' | 'pd'
  + pd_config      jsonb null
  + runtime        jsonb null
custom_model_deployment
  + pd_status      jsonb null        -- {"prefill": {"desired", "ready", "available", "status", "message"}, "decode": {...}}
  + router_stack_id uuid null fk custom_llmd_stack.id on delete set null
  + router_stack_created bool not null default false
```

`pd_config` shape (all keys optional except roles):

```json
{
  "prefill": {"replicas": 8, "gpu_count": 1, "gpu_type": "h200",
              "engine_args": {"compilation-config": "{\"cudagraph_mm_encoder\": true}"},
              "vllm_extra_args": [], "env": {}},
  "decode":  {"replicas": 2, "gpu_count": 4,
              "engine_args": {"compilation-config": "{\"cudagraph_mode\":\"FULL_DECODE_ONLY\"}", "max-num-seqs": 512}},
  "nixl_port": 5600,
  "kv_transfer_extra": {"kv_load_failure_policy": "fail", "kv_buffer_device": "cuda",
                        "kv_connector_extra_config": {"backends": ["UCX"]}},
  "sidecar_image": null,
  "router": {"peak_prefill_throughput": 33821, "prefix_tokens_to_match": 131072}
}
```

Validation (`pydantic`, `app/services/pd_serving.py`): roles required with
`replicas ≥ 0`, `gpu_count ≥ 0`; `nixl_port` 1024–65535; engine args keys
by the existing regex; `--kv-transfer-config`/`--port` forbidden in any
extra args; `engine == "vllm"`.

### Rendering (`app/services/pd_serving.py`, pure; called from `model_deployment_manifests.build_all`)

- `role_view(dep, role) -> RoleSpec` applies the merge rules and the
  plumbing: `port` (8000/8200), `kv_transfer_config` JSON, env
  additions, labels.
- `build_pd_deployments(dep) -> [prefill_deployment, decode_deployment]`
  built on top of `build_deployment`'s container (same probes/resources/
  volumes code path, extended with `runtime`), differing in: name
  `<model>-prefill-deployment` / `<model>-decode-deployment`; selector
  `{portal labels, llm-ops/pd-role: <role>}`; pod labels add
  `llm-d.ai/role`; args `--model … --port <p> --kv-transfer-config <json>
  <merged engine args> <merged extra args>`; env += `VLLM_NIXL_SIDE_CHANNEL_HOST`
  (podIP), `VLLM_NIXL_SIDE_CHANNEL_PORT`; container ports `http` + `nixl`;
  decode adds the native sidecar init container with the configured image
  and a `sidecar` port 8000, and its readiness is the vLLM `/health` on
  8200 (so the router only routes once vLLM is up).
- `build_pd_services(dep)`: `<model>-decode-service` (80 → 8000 sidecar) and
  `<model>-prefill-service` (80 → 8000) for debugging/port-forward; no
  Ingress.
- `k8s_resource_names(dep)` returns the full set for P/D so delete removes
  everything; `K8sClient.delete` iterates the provided names (no new kinds).
- `runtime` rendering (both modes): `hostIPC`, container `securityContext.
  privileged`, `/dev/shm` emptyDir `medium: Memory` with `sizeLimit`, extra
  resource keys into limits+requests.

### Router (`llmd_manifests.default_llmd_values(serving_mode, pd_router)`)

Adds, for `pd`:

```yaml
router:
  epp:
    pluginsConfigFile: pd-config.yaml
    pluginsCustomConfig:
      pd-config.yaml: |   # verbatim EndpointPickerConfig from the llm-d guide,
                          # with peakPrefillThroughput / maxPrefixTokensToMatch substituted
  modelServers:
    matchLabels: {llm-d.ai/model: <model>}
    targetPorts: [{number: 8000}]
```

`api/model_deployments.create` with `serving_mode == "pd"` and no
`router_stack_id` calls the llm-d create path (`api/llmd.py` internals
refactored into a service function `create_stack(db, user, request)`) with
`name=<model>-router`, the deployment's cluster/namespace, and the P/D
values; stores `router_stack_id`, `router_stack_created=True`. ArgoCD
unavailable → the deployment is still created and the error is recorded in
`status_message` (`router stack not created: …`) so the operator can create
one by hand; registration then waits.

### Reconciler (`jobs/reconcile_deployments.py`)

For `serving_mode == "pd"`: read both Deployments, classify each with the
existing `classify()`, store `pd_status`, derive the overall status (Decision
6), set `ready_replicas = decode ready`, `replicas = decode desired` (the
list column stays meaningful: "serving capacity" = decode). Registration
condition adds the router check: stack row exists and `_live_status` is
healthy, or (no ArgoCD status) the `<argo_app_name>-epp` Deployment is
available. `api_base = https://<stack ingress host>`.

### API

- Recipe body: `serving_mode`, `pd_config`, `runtime`; validated by
  `pd_serving.validate_pd_config` when mode is `pd`.
- Deployment create: same fields copied from the recipe (dialog sends them);
  per-role replicas are taken from `pd_config` (the dialog shows two
  replica inputs); `ingress_*` ignored for `pd` (router provides;
  `router_ingress_host` optional override forwarded to the stack);
  `router_stack_id` optional.
- Deployment read: `serving_mode`, `pd_config`, `runtime`, `pd_status`,
  `router_stack_id`, `router_stack` (name, ingress host, health) when
  linked.
- Delete: K8s objects for both roles + services, then the auto-created stack
  via the existing llm-d delete path. LiteLLM model/catalog cleanup stays as
  today (known gap, separate task).
- Serving overview: deployment entries carry `serving_mode` and a compact
  `pd` summary (`prefill ready/desired`, `decode ready/desired`).
- Benchmarks: `serving_target_url(dep)` for `pd` → `http://<argo_app_name>-epp.<ns>.svc:80`;
  `build_ephemeral_deployment` on a `pd` base → 400.
- Importer: when an external Deployment's args contain
  `--kv-transfer-config`, the draft gets warning `pd_detected` (with the
  kv_role) and the flag is dropped from engine args.

### Frontend

- Recipe form: "서빙 방식" segmented control (단일 / Prefill·Decode 분리). P/D
  shows two role cards (replicas, GPU 수, GPU 타입 override, 역할별 엔진 인자
  as `key=value` lines, 추가 인자, env) plus a "KV 전송" group (NIXL 포트,
  kv_transfer_extra as JSON, sidecar image override) and a "라우터" group
  (peak prefill throughput, prefix tokens). Engine toggle locked to vLLM in
  P/D. "런타임" group (shm GiB, hostIPC, privileged, extra resources lines)
  is visible in both modes under the advanced block. Startup probe fields
  join the health-check card.
- Deploy dialog: for P/D, replicas become two inputs (prefill/decode) and
  the ingress fields are replaced by "라우터 인그레스 호스트 (선택)"; GPU type
  select applies to both roles unless a role overrides it (shown).
- Deployment list: `P/D` badge, replicas cell shows `D 2/2 · P 8/8`.
- Deployment detail: two role cards (desired/ready/available, status
  message, rendered launch args per role) and a router card linking to the
  stack (health, ingress host, "라우터로 등록됨" when LiteLLM registered).
- Serving home: `P/D` badge next to the engine.
- llm-d stack detail already lists linked servers; both roles appear with
  a role chip.
- Benchmark "new": a P/D deployment target shows "라우터를 통해 측정" note.

### Error handling

- `pd` with `engine != vllm` → 400. `--kv-transfer-config` or `--port` in
  any extra args → 400 naming the flag.
- Router stack creation failure → deployment created, `status_message`
  explains, detail page shows a "라우터 만들기" button that retries.
- One role missing in the cluster (someone deleted it) → `pd_status`
  carries `Missing` for that role, overall `Unhealthy`.
- Sidecar image pull failure → decode pods never Ready; `pd_status.decode.
  message` surfaces the pod condition reason (reconciler already reads
  conditions).

### Testing

- `tests/test_pd_serving.py`: merge rules per role, kv-transfer JSON
  (producer/consumer, extras merged, `kv_role` cannot be overridden),
  forbidden flags, port/env/labels/sidecar/ports per role, services, no
  ingress, runtime rendering (shm, hostIPC, privileged, rdma), startup
  probe defaults, resource names.
- `tests/test_llmd_manifests.py` additions: P/D values contain the plugin
  config with substituted parameters and select both pools.
- Reconciler tests: status derivation table (both ready, one pending, one
  failed, one missing), registration gated on the router, `api_base`.
- API tests: recipe/deploy validation, auto-created stack + linkage,
  delete order, overview summary, importer warning, benchmark target.
- Frontend: tsc/eslint/build; Playwright smoke of the recipe form P/D
  section and the deploy dialog with mocked proxy.
- Local e2e (best effort, minikube `portal-test`, CPU mocks): the mock
  server ignores flags, so prefill/decode pods come up; the real
  `llm-d-router-disagg-sidecar` image is a plain HTTP proxy (no GPU) and the
  router chart installs via the existing ArgoCD. Assert: both Deployments
  rendered as specified, decode pod has the sidecar, router stack created
  with the P/D config, overall status reaches Ready, a chat completion
  through the router's `-epp` Service returns the mock reply (if the sidecar
  requires NIXL handshake headers the mock cannot satisfy, record that and
  stop at "router routes to decode").

## Non-goals

- SGLang P/D, MooncakeConnector, TPU/XPU variants.
- DisaggregatedSet (coordinated rollouts) and any CRD-based operator.
- Multi-node tensor parallel (one pod per instance only).
- Autoscaling or xPyD ratio tuning; replicas are static per role.
- Ephemeral benchmark clones of P/D recipes.
- Importing an external P/D pair into one recipe (warning only).
- LiteLLM model/catalog cleanup on delete (pre-existing gap, separate task).

## Revision 2026-10-06 — full per-role configs, four tabs

Review feedback after the first implementation: "prefill, decode and the
router each need their own settings; base + overrides reads oddly". Changed:

- `pd_config.prefill` / `.decode` are now a **complete pool config** (replicas,
  gpu_count, gpu_type, cpu/memory requests/limits, engine_args, extra args,
  env). The backend keeps the merge rule (role wins, extra args appended, env
  merged), so rows saved as overrides still render the same; the form stores
  the shared engine args / extra args / env / cpu / memory as null in P/D mode
  and `gpu_count` as the larger pool's.
- `pd_config.router` gained `epp_registry` / `epp_repository` / `epp_tag`,
  `epp_replicas` and `ingress_class`; they become the auto-created stack's
  overrides and the EPP replica count. The host stays a deploy-time input.
- Recipe form: a serving-mode card on top; P/D recipes split into **Shared /
  Prefill / Decode / Router** tabs (all mounted, so switching never loses
  input). Shared = basics, storage, probes, runtime, placement. Each pool tab
  shows the full config with a "copy from the other pool" button. Router tab
  = EPP image/replicas, ingress class, scheduler tuning, sidecar image, NIXL
  port, kv-transfer extras. Deployment detail mirrors the split (shared card
  without pool resources, pool cards with their resources, router card with
  EPP/ingress/tuning).

## Verification (2026-10-06, minikube `portal-test`, no GPU)

- Recipe `pd-mock` (image `mock-vllm:cpu`, gpu 0, prefill 1×, decode 1× with `max-num-seqs=512`, `runtime.shm_size_gi=1`) deployed as `pd-mock`:
  `pd-mock-prefill-deployment` / `pd-mock-decode-deployment` + two Services, no Ingress; decode pod `2/2` with
  `routing-proxy` (`ghcr.io/llm-d/llm-d-router-disagg-sidecar:main`, native sidecar) running; pod labels carry
  `llm-d.ai/model`, `llm-d.ai/role`, `llm-ops/pd-role`; env `VLLM_NIXL_SIDE_CHANNEL_HOST` from `status.podIP`, port 5600;
  `/dev/shm` emptyDir 1Gi.
- ArgoCD Application `llmd-pd-mock-router` created with the P/D EPP config (`pluginsConfigFile: pd-config.yaml`,
  `peakPrefillThroughput: 5000`), `router_stack_created = true`.
- Reconciler: `pd_status` prefill/decode Ready, `pd_summary = "D 1/1 · P 1/1"`, event `LitellmRegistered … via router
  https://pd-mock.local`. The first pass registered while the Application was still `Unknown/Healthy` (ArgoCD had not
  compared yet) → the gate now requires `Synced` + `Healthy` (commit 8ec181b).
- `POST /api/benchmarks` with `ephemeral: true` on the P/D template → 400 "P/D recipes cannot be cloned…".
- Limitation of this laptop: the local `.env` points `APP_LLMD_CHART_REPO` at `registry.k8s.io/gateway-api-inference-extension/charts`
  (the GAIE `standalone` chart, a different values schema) and the ArgoCD AppProject only allows that registry, so the
  auto-created router could not pull `llm-d-router-standalone:v0.9.0` until the project was patched to allow
  `oci://ghcr.io/llm-d/charts`. Production uses the settings defaults (`oci://ghcr.io/llm-d/charts` or the air-gapped mirror).
- Not exercised: an actual NIXL KV transfer and the sidecar → prefill hop (needs GPUs and real vLLM).

## Open questions for review — answered

1. Sidecar tag: kept the guide's `main` as the default (`APP_LLMD_SIDECAR_IMAGE_TAG`); air-gapped sites pin their mirror's tag.
2. Registration gate: wait for the router — and it must be `Synced` as well as `Healthy` (a fresh Application is
   Healthy with nothing created yet).
3. `runtime.privileged` / `host_ipc`: allowed for any super user, with the hint in the form; no portal-level gate yet.
4. Per-role `gpu_type` override: kept (`pd_config.<role>.gpu_type`), resolved per cluster like the base `gpu_type`.

## Open questions for review (original)

1. Should the sidecar image tag default to the EPP tag (`v0.9.0`) instead of
   the guide's `main`? The sidecar repo's release tags need checking.
2. Registration gate: wait for the router to be healthy (safer, slower) or
   register as soon as both pools are Ready (router usually comes up first
   anyway)? Draft: wait for the router.
3. Should `runtime.privileged`/`host_ipc` be allowed on recipes created by
   any super user, or gated behind a portal setting (cluster policy may
   forbid privileged pods)? Draft: allowed, with a warning in the form.
4. Prefill/decode may want different GPU types (TP=1 on cheaper cards);
   the draft allows a per-role `gpu_type` override. Keep?
