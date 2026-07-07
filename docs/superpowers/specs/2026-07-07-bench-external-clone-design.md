# Benchmark External Servings via Fresh Clone — Design

**Date:** 2026-07-07
**Status:** Approved

## Problem

The benchmark form's "서빙 디플로이먼트" picker lists only **portal-created**
deployments that are **Ready**. Environments where every serving is external
(discovered from K8s by #190) see an empty dropdown and cannot benchmark at all
— even though the discovered Deployments carry the full vLLM serve options
(image, args) needed to reproduce them. The existing ephemeral clone-bench
pipeline (provision → wait Ready → `vllm bench serve` → teardown) only accepts a
portal deployment row as its template.

## Goal

Let the benchmark form pick a **discovered external serving** as the target.
Picking one spins up a **fresh benchmark clone** of that serving (from its live
K8s spec), runs `vllm bench serve` against the clone, and tears it down —
never touching the live serving. Also surface the existing clone mode properly
for portal deployments (radio instead of hidden checkbox, Ready-filter lifted).

## Decisions (from brainstorming)

| Question | Decision |
|---|---|
| What the user picks | The existing deployment dropdown becomes two optgroups: Portal deployments / External servings (from `useExternalServings`) |
| How external is benched | Always via ephemeral clone of the **live full Deployment spec** (discovery data alone lacks volumes/resources); never direct |
| Clone placement | Same cluster **and same namespace** as the external serving (PVC/Secret refs are namespace-local) |
| Scope v1 | Performance bench (`vllm_serving`/`sglang_serving` → `vllm bench serve`) only; accuracy (lm_eval) keeps the LiteLLM-registered path |
| Portal UX | Ready-filter lifted (non-Ready ⇒ clone forced); hidden ephemeral checkbox → explicit radio "새로 띄워서 벤치 (복제, 기본)" vs "실행 중인 서빙에 직접" (direct only for Ready portal) |
| Schema | None — `CustomBenchmarkRun.deployment_id` is already nullable and the reconciler keys off `serving_k8s_name`/`k8s_namespace`/`cluster_id` only |

## Architecture

The ephemeral pipeline is reused unchanged; only the **template source** is
extended. For an external target the API reads the live Deployment via the K8s
API (`deployments: get` RBAC already granted), builds clone manifests that copy
the pod template faithfully (container image/command/args/env/resources/ports +
pod volumes/nodeSelector/tolerations, replicas forced to 1, bench-owned labels),
creates the run with `deployment_id=None, ephemeral=True,
serving_k8s_name=<clone>`, applies the manifests, and lets
`reconcile_benchmarks` drive provision → bench Job → teardown exactly as today.

## Components

### Backend

**`clients/k8s.py`** — `read_deployment(namespace: str, name: str) -> dict | None`:
`read_namespaced_deployment` sanitized to a plain dict of the spec the clone
builder needs (container image/command/args/env/envFrom/resources/ports/
volumeMounts; pod volumes/nodeSelector/tolerations). None on 404.

**`services/benchmark_serving.py`** — external clone builder:
- `build_external_clone(spec: dict, *, name: str) -> list[dict]` — Deployment +
  Service manifests. Faithful copy of the first container + pod-level fields;
  `replicas: 1`; labels `{app: llmops-bench-serving, bench-serving: <name>}`
  (and the Service selects them); Service port 80 → target port resolved as:
  `--port` in args → first containerPort → 8000.
- `external_bench_facts(spec: dict) -> dict` — extracts
  `served_model` (`--served-model-name` value, else `--model` value),
  `tokenizer` (`--model` value), `api_key` (reuse the `serving_api_key`
  convention over args/env; "EMPTY" when unauthenticated). Raises a clear error
  when no `--model`/`--served-model-name` can be found (400 upstream).
- `serving_overrides` support: the existing override mechanism applies where it
  maps (replicas is always 1; resource overrides patch the cloned container).

**`api/benchmarks.py`** —
- `CreateBenchmarkRequest.external_target: {cluster_id: str | None, namespace: str,
  deployment_name: str} | None` (mutually exclusive with `deployment_id`/
  `model_name`; performance tools only — 400 for `lm_eval`).
- Create flow: resolve `k8s_for_cluster(db, cluster_id)` → `read_deployment` →
  404 if gone, 502 with reason on API errors → `external_bench_facts` (400 if
  unparseable) → create run (`model_name=served_model`, `deployment_id=None`,
  `ephemeral=True`, `cluster_id`, `k8s_namespace=<serving's namespace>`,
  `serving_k8s_name=ephemeral_model_name(run.id)`, `status="provisioning"`) →
  apply clone manifests. Store the source identity in
  `params["external_source"] = {cluster_id, namespace, deployment_name}` for
  display; no schema change.
- `/preview` supports the same branch (manifests + bench command preview).

**Reconciler** — unchanged. Provision gating, bench Job creation (target =
`serving_target_url(serving_k8s_name, k8s_namespace)`), and teardown already
operate on run fields the external path fills identically.

### Frontend (benchmark form)

- Deployment dropdown → two optgroups:
  - **Portal deployments**: ALL portal deployments (Ready-filter removed),
    labels show status; non-Ready options force clone mode.
  - **External servings**: from `useExternalServings()`; options show
    `deployment_name (engine · namespace)`; identity encoded as
    `ext::<cluster_id|"">::<namespace>::<deployment_name>` (same convention as
    the llm-d form).
- Mode radio replaces the hidden checkbox: "새로 띄워서 벤치 (복제)" —
  default — vs "실행 중인 서빙에 직접". Direct is selectable only for Ready
  portal deployments; external / non-Ready force clone (radio disabled with
  hint).
- Picking an external serving: cluster/namespace display auto-set from the
  serving (fixed — clone placement rule); `serving_overrides` editor stays
  available; accuracy tools hide/disable the external group.
- Submit: external target sends `external_target: {...}` + `ephemeral` implied;
  portal paths unchanged.
- i18n en/ko for the new group labels, radio labels, hints (GPU-capacity and
  RWO-PVC caveats in the clone hint).

## Data flow (external)

```
Pick external serving → radio locked to "새로 띄워서 벤치"
  → POST /api/benchmarks { external_target: {cluster_id, namespace, deployment_name}, tool, params, ... }
  → backend: read_deployment (live spec) → external_bench_facts → run(provisioning) → apply clone
  → reconcile_benchmarks: clone Ready → vllm bench serve Job (in-cluster URL) → result → teardown clone
```

## Error handling & edge cases

- Live spec fetch: 404 → "serving no longer exists"; ApiException → 502 with
  reason; `K8sNotConfigured` → 503.
- No `--model`/`--served-model-name` in args → 400 "could not derive the served
  model from the deployment's vLLM options".
- **GPU capacity**: the clone runs alongside the live serving — the clone hint
  says so and points at `serving_overrides` to shrink it. If the cluster lacks
  capacity the clone stays Pending and the existing provisioning timeout fails
  the run with a clear message (already implemented).
- **RWO PVC**: cloned volumes may fail to attach if the live pod holds an RWO
  volume on another node — documented in the hint; same known caveat as portal
  clones.
- Multi-container pods: v1 clones the **first** container (vLLM/SGLang servers
  are single-container in practice); sidecars are dropped, noted in the spec.
- lm_eval + external_target → 400 (v1 perf-only).

## Testing

- Unit (`test_benchmark_serving.py` additions): port resolution precedence
  (`--port` → containerPort → 8000), served-model extraction
  (`--served-model-name` wins over `--model`; missing → error), volumes/
  nodeSelector/tolerations preserved, replicas forced 1, overrides applied,
  Service selector matches clone labels.
- API: external_target happy path (mock K8s: read_deployment + create_or_patch;
  run row fields asserted), template-gone 404, unparseable args 400, lm_eval
  400, existing ephemeral/portal paths regression-free.
- Frontend: tsc + lint gates; grouped dropdown + radio wiring.

## Out of scope

- Accuracy (lm_eval) against external clones.
- Cloning sidecar containers / multi-container pods faithfully.
- Direct (no-clone) benchmarking of external servings.
- Auto-detecting the live serving's Service for direct URL use.
