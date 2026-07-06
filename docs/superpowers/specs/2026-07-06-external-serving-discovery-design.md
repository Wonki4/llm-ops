# External vLLM/SGLang Serving Discovery — Design

**Date:** 2026-07-06
**Status:** Approved

## Problem

The Admin → Deployments page only shows servings created through the portal
(`custom_model_deployment` rows). vLLM/SGLang servers deployed directly to the
cluster (kubectl, Helm, other teams' pipelines) are invisible, so admins cannot
see the full serving landscape or route traffic to those servers through
LiteLLM without manual work.

## Goal

Surface externally-deployed vLLM/SGLang servings in the existing Deployments
view (read-only), and let admins register one with the LiteLLM proxy in one
click. No editing, scaling, or deleting of external workloads.

## Decisions (from brainstorming)

| Question | Decision |
|---|---|
| Detection | Image heuristic: container image name contains `vllm` or `sglang` (case-insensitive) |
| Interaction | View + one-click LiteLLM registration (and symmetric unregister) |
| Scan scope | All clusters (portal default kubeconfig + every registered `custom_k8s_cluster`), all namespaces |
| UI placement | Same Deployments list, with an `External` badge |
| Architecture | Live scan at request time; only registration state persisted (Approach A) |

## Architecture

`GET /api/model-deployments/external` (super-user only) scans all clusters in
parallel at request time — one `list deployments (all namespaces)` call per
cluster. The existing RBAC ClusterRole already grants `deployments: list`
cluster-wide, so no RBAC change is needed.

Filtering:
- Include a Deployment when any container image matches the heuristic.
- Exclude Deployments labeled `llm-ops/managed-by: litellm-portal` — those are
  portal-managed and already in the main list.

Only LiteLLM registration state is persisted (new table
`custom_external_serving`); the scan result is joined against it to mark rows
as registered. No reconciler involvement, no lifecycle sync.

## Components

### Backend

**`clients/k8s.py`** — add `list_deployments_all()` using
`apps.list_deployment_for_all_namespaces()`.

**`services/deployment_status.py`** (new) — move `_classify()` out of
`jobs/reconcile_deployments.py` so the API and the worker share one status
classifier (Ready / Pending / Updating / Unhealthy / Failed / Stopped).
The reconciler imports it from the new location.

**`api/model_deployments.py`** — three new routes under the existing router:

- `GET /external` — parallel scan (`asyncio.gather`, 5 s timeout per cluster
  via `asyncio.wait_for`). Response:
  ```json
  {
    "servings": [
      {
        "cluster_id": "…|null", "cluster_name": "default|…",
        "namespace": "…", "deployment_name": "…",
        "engine": "vllm|sglang", "image": "…",
        "replicas": 2, "ready_replicas": 2, "status": "Ready",
        "status_message": null, "created_at": "…",
        "model_path": "…",        // best-effort from container args --model
        "registration": { "model_name": "…", "api_base": "…", "litellm_model_id": "…" } | null
      }
    ],
    "errors": [ { "cluster": "…", "message": "…" } ]
  }
  ```
- `POST /external/register` — body `{cluster_id?, namespace, deployment_name,
  model_name, api_base, api_key?}`. Calls LiteLLM `/model/new` with
  `openai/<served-name>` + the given `api_base`/`api_key` (default `EMPTY`),
  mirroring the reconciler's `_register_with_litellm`. On success, inserts a
  `custom_external_serving` row. 409 when the (cluster, namespace, name) key is
  already registered. 502 when LiteLLM fails.
- `DELETE /external/register/{id}` — calls LiteLLM `/model/delete` for the
  stored `litellm_model_id`, then deletes the row.

**DB** — new table `custom_external_serving` (+ Alembic migration):

| column | type | notes |
|---|---|---|
| id | uuid PK | |
| cluster_id | uuid FK → custom_k8s_cluster, nullable | null = portal default kubeconfig |
| namespace | str | |
| deployment_name | str | |
| model_name | str | name registered in LiteLLM |
| api_base | str | admin-provided |
| litellm_model_id | str | from /model/new response |
| registered_by | str | |
| created_at | timestamptz | |

Unique constraint on `(cluster_id, namespace, deployment_name)`.

### Frontend (`(app)/admin/deployments/page.tsx`)

- Fetch the existing list and `GET /external`; render one merged table.
- External rows: `External` badge + engine badge (vLLM/SGLang) + cluster
  column; no edit/delete/scale actions. Action button: **Register to LiteLLM**
  (or registered state: model name + unregister button).
- Registration dialog: `model_name` (pre-filled from the discovered `--model`
  value's basename, else deployment name), `api_base` (required, free text —
  the portal cannot reliably infer an external serving's ingress),
  `api_key` (optional, default `EMPTY`).
- External row click: inline expansion with raw details (labels, image, args).
  No separate detail page.
- When `errors` is non-empty: warning banner naming the unreachable clusters.

## Error handling & edge cases

- **Cluster down / timeout** → that cluster is omitted; partial results +
  warning banner. The page always renders.
- **No kubeconfig at all** (`K8sNotConfigured`) → external section empty, DB
  list unaffected; no hard error.
- **Duplicate model_name** → uniqueness enforced only within
  `custom_external_serving`. If the name already exists in LiteLLM, LiteLLM
  treats it as another deployment in the same load-balanced group — allowed by
  design, not blocked.
- **Registered serving disappears from cluster** → it simply stops appearing in
  the scan; the mapping row stays. The LiteLLM model can still be removed via
  LiteLLM model management (or the mapping cleaned up later).
- **Portal-managed detection** relies on the `llm-ops/managed-by` label, not
  name matching, so renamed portal deployments never appear twice.

## Testing

- Unit: image heuristic (vllm image / sglang image / unrelated image /
  portal-labeled exclusion), `--model` arg extraction, status classification
  reuse.
- API: mocked K8s client — all clusters healthy; one cluster timing out
  (partial result + errors entry); nothing configured (empty + no 5xx).
- Registration: mocked LiteLLM client — success persists row; duplicate → 409;
  LiteLLM failure → 502 and no row; unregister deletes model + row.
- Follow existing backend pytest patterns.

## Out of scope

- Editing/scaling/deleting external workloads.
- Status history, events, or Slack alerts for external servings.
- Automatic api_base inference from Services/Ingresses.
- Auto-creating `custom_model_catalog` rows on external registration.

## v1 implementation notes (post-review)

Final whole-branch review verdict: ready to merge. Deliberate deviations and
accepted follow-ups recorded here so they aren't rediscovered as bugs:

- **`served_model_name` added to the register body** (spec's body list omitted
  it). An external vLLM's default served-model name is the full `--model` path,
  so the dialog pre-fills the full path and lets the admin override when the
  server used `--served-model-name`. The resulting `openai//models/...` double
  slash in litellm_model is intentional.
- **Follow-up (non-blocking):** `k8s_for_cluster()` decrypts stored kubeconfigs
  eagerly in the GET /external target-building loop, outside `scan_clusters`'
  error boundary — a corrupted stored kubeconfig 500s the endpoint instead of
  becoming a per-cluster `errors[]` entry. Same exposure as the reconciler.
  Fix by building targets per-cluster inside a try/except.
- **Security note:** the serving payload includes container `args` for the
  inline expansion; if an external server was started with `--api-key <secret>`
  the value is visible to super users (identical visibility they already have
  via kubectl). Add arg redaction if that trust boundary ever changes.
- `scan_clusters` classifies `asyncio.gather(return_exceptions=True)` results
  with `isinstance(result, BaseException)` — this is required (narrowing to
  `Exception` would let CancelledError fall into the success branch); do not
  "simplify" it.
