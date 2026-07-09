# Self-Serving Benchmark Job — Design

**Date:** 2026-07-09
**Status:** Approved direction (user: collapse serve+bench into one Job; scope =
performance, the two "spin up fresh serving" flows only)

## Problem

The "spin up a fresh serving and benchmark it" flows (portal-deployment clone
`ephemeral`, and external-serving clone `external_target`) are two-phase:

1. Create a **Deployment + Service** (the throwaway serving) at submit time.
2. The reconciler waits for the Deployment to report ready, then creates a
   **separate bench Job** that hits the serving's Service URL.
3. Tear the serving down when the run finishes.

This cross-object gating is the source of "the serving comes up but the
benchmark never runs": if the readiness check never matches, or the Job build
throws when the serving turns ready, the run sticks in `provisioning` with the
serving still up. `reconcile_once` also wraps the whole batch in one
try/except + one commit, so one throwing run can roll back and wedge every
run.

vLLM can serve and benchmark in the **same pod**: start `vllm serve` in the
background, wait for `/health`, run `vllm bench serve` against
`localhost`, emit the result, stop the server. One Job, one lifecycle — the
entire provisioning/readiness/teardown machinery disappears for these flows.

## Scope

Convert **only** the two performance "spin up fresh" flows to a single
self-serving Job:

- `body.ephemeral` (portal-deployment clone), `kind == "performance"`.
- `body.external_target` (external clone), always `kind == "performance"`.

Unchanged:
- **direct** (bench an existing portal deployment's Service) and **LiteLLM
  model** flows — they bench an already-running endpoint; no serving to
  create. Still a bench-only Job hitting the target URL.
- **accuracy / lm_eval** — including its ephemeral variant. Keeps the current
  provisioning path (out of scope; revisit separately).

## Design

### New builder: `build_self_serving_bench_job`

`backend/app/services/benchmark_manifests.py` gains:

```
build_self_serving_bench_job(run, *, serving_image, serving_cli, serving_env,
    resources, volumes, volume_mounts, node_selector, tolerations, port,
    api_key, served_model, tokenizer, backoff_limit=0, ttl=...) -> dict
```

It returns ONE `batch/v1` Job whose pod runs the serving container image with
GPU `resources`, the model `volumes`/`volume_mounts`, `node_selector`,
`tolerations`, and `serving_env` copied from the source serving spec — but its
`command` is a wrapper script:

```
set -m
<serving_cli...> &            # the exact `vllm serve …` argv, backgrounded
SRV=$!
python - <<'PY'               # readiness poll on localhost:<port>/health
import urllib.request, time, sys
for _ in range(HEALTH_TRIES):
    try:
        urllib.request.urlopen("http://localhost:PORT/health", timeout=3); sys.exit(0)
    except Exception: time.sleep(HEALTH_INTERVAL)
sys.exit(1)
PY
<vllm bench serve --base-url http://localhost:PORT … the existing arg builder …>
echo "<<<RESULT>>>{…/tmp/r.json…}"
kill $SRV 2>/dev/null || true
```

- The `vllm bench serve` argv is the **same** builder logic already in
  `build_vllm_bench_job` (base-url → `localhost:<port>`, `--model
  served_model`, tokenizer, num-prompts/random-len/seed/goodput/request-rate/
  max-concurrency/ignore-eos, extra params passthrough, `extra_args`). Extract
  that argv construction into a shared helper so both builders stay DRY.
- If the readiness poll fails (server never healthy within the window), the
  script exits non-zero → the Job fails → the reconciler surfaces the pod
  logs (the serve crash reason) as `error_message`. This is strictly better
  diagnostics than today's silent `provisioning` timeout.
- The API key still flows in as `OPENAI_API_KEY` (self-served targets usually
  need none, but auth-gated serve args are honored).
- Port = the serving's own container port (reuse `_clone_target_port` /
  the deployment's port), defaulting to 8000.

`build_external_clone` / `build_ephemeral_deployment` / `ephemeral_manifests`
stay for any remaining Deployment use, but the two converted flows stop calling
them.

### Create path (`backend/app/api/benchmarks.py`)

Both converted branches change from "create serving now, Job later" to
"create the self-serving Job now" — mirroring the existing direct-path shape:

- Resolve the serving facts as today (external: from the live spec via
  `serving_cli` + `external_bench_facts`; ephemeral: from the template
  deployment). Keep populating `serving_snapshot` for the detail UI.
- Build the single Job with `build_self_serving_bench_job(...)`.
- `run.status = "pending"` (NOT `provisioning`); `run.k8s_job_name =
  job_name_for(run.id)`; `run.ephemeral` stays `True` for bookkeeping but
  `serving_k8s_name` is left null (there is no separate serving object) and
  `serving_torn_down = True` (nothing to tear down).
- `create_job(namespace, manifest)` at submit time; on failure mark failed +
  502, same as today.

### Reconciler (`backend/app/jobs/reconcile_benchmarks.py`)

- `_drive_provisioning` is no longer reached by the converted flows (they start
  in `pending`). It stays only for the accuracy-ephemeral path still using it;
  if no flow uses it after this change, remove it and the `provisioning`
  status handling. (Decide during planning by grepping remaining
  `status="provisioning"` writers — accuracy ephemeral currently sets it, so
  `_drive_provisioning` likely stays for now.)
- `_teardown_serving` / the safety sweep are keyed on `serving_k8s_name`;
  converted runs have it null, so they are skipped naturally. No change needed,
  but confirm the sweep predicate tolerates null.
- Cancel (`/{id}/cancel`) already deletes `run.k8s_job_name` and only tears
  down a serving when `serving_k8s_name` is set — works unchanged for the
  single-Job runs.

### Preview

The performance preview for these flows renders the single self-serving Job
manifest (redacted key) instead of a Deployment+Service. Non-converted flows'
previews are unchanged.

## Non-goals

- No change to direct / LiteLLM / accuracy flows.
- No Service / ingress path measurement — bench hits `localhost` on purpose
  (isolated model/serving measurement).
- No serving reuse across runs (each Job is self-contained and disposable).

## Edge notes

- GPU is held by the one pod for serve-startup + bench (same GPU-time as the
  two-object version, one object).
- The serving image must ship `vllm` (it does — it's the serving image) and
  `python` for the readiness poll (vLLM images are Python-based).
- Readiness window: default ~30 min (matches today's `PROVISION_TIMEOUT_S`
  1800s) via HEALTH_TRIES × HEALTH_INTERVAL, so slow PVC weight loads still
  pass.

## Verification

- Backend: new tests — `build_self_serving_bench_job` shape (single Job, GPU
  resources, model mount, serve-cli backgrounded, bench argv against
  localhost, RESULT emit, readiness poll present); create paths (external +
  ephemeral) produce status `pending` + a Job (no Deployment/Service create
  call) + `k8s_job_name` set + `serving_k8s_name` null; existing direct/model/
  accuracy tests unchanged. pytest 0 NEW failures (baseline 21), ruff 0 NEW
  (baseline 78).
- Frontend: no required change (status/labels already handled); build/lint
  unaffected. Preview shows the single Job.

## v1 implementation notes (post final review)

Final whole-branch review: APPROVE — 9/9 checks PASS (end-to-end traced for
both flows; port serve/health/bench consistency confirmed; the original
"serving up, bench never runs" failure mode is gone because converted flows
start `pending` and never enter `_drive_provisioning`; accuracy-ephemeral and
direct/model paths untouched; teardown/sweep/cancel no-op correctly on null
serving; `_vllm_bench_argv` refactor byte-identical). A serve that never
becomes healthy now fails with an explicit "serving did not become ready"
message instead of sticking.

Ship-as-is minors (all fail with the clear startup-timeout message, never a
silent hang):
- External port inference: if a serving omits `--port` and its declared
  containerPort differs from vLLM's 8000 default, health/bench target the
  wrong port. Pre-existing `_clone_target_port` assumption.
- Duplicate `--port` in extra args → argparse last-wins binds a port
  health/bench don't target (same convention as `build_deployment`).
- Compound `sh -c "vllm serve … && …"` external serve commands flatten to
  literal argv and won't start under the new direct-exec path (the plain
  `vllm serve <model> <flags>` case works). Narrow.
