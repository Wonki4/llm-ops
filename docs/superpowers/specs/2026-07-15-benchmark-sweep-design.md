# Benchmark Sweeps — Design

**Date:** 2026-07-15
**Status:** Approved direction (user: approach A — sweep entity + fixed load
presets + sequential self-serving Jobs + auto comparison table)

## Problem

The benchmark feature works but is not *established* as a methodology:

1. **Every run is hand-assembled.** The run form exposes ~13 performance
   params plus extra-JSON/extra-args/NFS/namespace/image/key. Two runs are
   comparable only if the operator manually kept every field identical.
2. **No standard load profiles.** There is no portal-defined "this is how we
   measure chat latency" — conditions drift between runs and between people.
3. **The primary decision is serving-config tuning** (same model, same GPU,
   different `vllm serve` flags — which config to run in production), and
   answering it today takes N manual submissions plus manually multi-selecting
   runs on `/admin/benchmarks/compare` and hoping the conditions matched.

A **sweep** fixes all three: one submission = {base serving × 1 fixed load
preset × 1–2 swept serve-flags}, executed sequentially on the same resources,
rendered as a comparison table when done.

## Scope

- Performance benchmarks, vLLM only (`tool == "vllm_serving"`).
- Base target = the two **self-serving Job** flows that already exist
  (`build_self_serving_bench_job`): a portal deployment used as template
  (ephemeral clone) or a discovered external serving (live-spec clone).
- Grid of 1–2 variables (serve CLI flags with value lists), 2–12 combos
  total, strictly sequential execution (one GPU set held at a time).

Out of scope (see Non-goals): accuracy/lm-eval sweeps, sglang, resource
(gpu_count) sweeps, parallel combos, YAML preview on the sweep form.

## Design

### Load presets (the methodology, fixed in code)

New constant in `backend/app/services/benchmark_presets.py`:

| key | intent | random_input_len / random_output_len | num_prompts | max_concurrency |
|---|---|---|---|---|
| `chat` | short interactive chat | 512 / 256 | 300 | 32 |
| `long_input` | RAG / document summarization | 4096 / 512 | 120 | 8 |
| `long_output` | generation-heavy | 256 / 1024 | 200 | 16 |

Common to all presets: `seed=0`, `ignore_eos=true`, dataset `random`
(air-gap safe), percentile metrics `ttft,tpot,itl,e2el` @ p90/p99 — all
already emitted by `_vllm_bench_argv`.

- `GET /api/benchmarks/presets` returns the dict; the sweep form renders
  preset cards from it (labels/descriptions via i18n on the preset key).
- At submit the preset is **expanded into `run.params`** (plus
  `params["preset"] = key`), so existing run-detail/compare pages work
  unchanged and historical runs stay self-describing even if presets are
  retuned later. Comparisons remain valid via stored params, not the key.

### Data model (migration 041)

New table `custom_benchmark_sweep`:

- `id` UUID pk, `name` String(256) nullable
- `deployment_id` UUID nullable (template-deployment flow; plain UUID like
  the run column), `external_source` JSONB nullable
  (`{cluster_id, namespace, deployment_name}`) — exactly one of the two set
- `cluster_id` UUID FK `custom_k8s_cluster` RESTRICT nullable,
  `k8s_namespace` String(128)
- `preset` String(32), `variables` JSONB
  (`[{"flag": "--max-num-seqs", "values": [128, 256]}, …]`),
  `serving_overrides` JSONB nullable (applied to every combo)
- `status` String(16): `running | completed | cancelled` (default `running`)
- `created_by`, `created_at`, `finished_at`

`custom_benchmark_run` gains:

- `sweep_id` UUID FK `custom_benchmark_sweep` ondelete SET NULL, nullable
- `sweep_index` Integer nullable (0-based combo order — promotion order)
- `sweep_combo` JSONB nullable (`{"--max-num-seqs": 256, …}` for display)
- `queued_job_manifest` JSONB nullable — the fully built Job manifest,
  frozen at submit; cleared on promotion; **never serialized** by the API
  (it embeds the bench API key, same sensitivity as the manifest K8s
  already receives)
- new `status` value `queued` (row exists, no Job yet; `k8s_job_name`
  stays null until promotion)

### Create path (`POST /api/benchmarks/sweeps`)

Body: `{name?, deployment_id | external_target, cluster_id?, namespace?,
preset, variables, serving_overrides?, image?, api_key?}`.

Validation: known preset; 1–2 variables; flags match `^--[a-z0-9-]+$` and
are distinct; every value a scalar; 2 ≤ combos ≤ 12 (cartesian product,
submission order = row-major).

1. Resolve the base exactly like today's two self-serving branches in
   `benchmarks.py::create_benchmark` (external: live-spec read +
   `external_bench_facts` + `serving_cli`; template: `CustomModelDeployment`
   lookup + `build_ephemeral_deployment` with `serving_overrides`).
   **External specs are read once here** — every combo derives from the same
   frozen spec even if the external serving changes mid-sweep.
2. For each combo, merge flags into the serve argv (base argv →
   `serving_overrides` → combo; combo wins): if the flag exists, replace its
   value token; else append `flag value`. Bare boolean switches are not
   supported as variables in v1.
3. Create N run rows (status `queued`, `params` = preset expansion,
   `serving_snapshot` reflecting the **merged** per-combo args so the
   compare view shows the real config, `queued_job_manifest` = the built
   self-serving Job) + 1 sweep row (`running`).
4. Promote combo #0 immediately: `create_job`, status → `pending`,
   `k8s_job_name` set, manifest column cleared. Job-create failure marks
   that run `failed` and leaves the sweep `running` — the reconciler
   promotes the next combo on its next tick.

Also: `GET /api/benchmarks/sweeps` (list + per-sweep run-status rollup),
`GET /api/benchmarks/sweeps/{id}` (sweep + runs ordered by `sweep_index`,
manifest excluded), `POST /api/benchmarks/sweeps/{id}/cancel`.

### Reconciler (`reconcile_benchmarks.py`)

New step `_drive_sweeps`, idempotent from DB state (portal restarts resume
naturally):

- For each sweep with `status == "running"`: if it has a run in a
  non-terminal, non-queued status → nothing to do. Else promote the
  lowest-`sweep_index` `queued` run (create Job from its stored manifest,
  → `pending`, clear manifest). If none remain → sweep `completed`,
  `finished_at` set.
- A `failed` combo (OOM, serve-not-ready timeout, …) does **not** stop the
  sweep — the next combo still runs; the table shows the failure.
- The existing job-polling loop is unaffected: `queued` runs have no
  `k8s_job_name`; confirm its predicate skips them.

Cancel: delete the current Job (existing per-run cancel logic), set every
remaining `queued` run to `cancelled` (clear manifests), sweep → `cancelled`.

### Frontend

- **`/admin/benchmarks/sweeps/new`** — separate, deliberately small form:
  base target select (ready deployments + discovered external servings) →
  preset cards (from `GET /presets`) → variable editor (flag + comma-
  separated values, up to 2; live combo-count with the ≤12 guard) → submit.
  No raw-JSON editing.
- **`/admin/benchmarks/sweeps/[id]`** — the deliverable: rows = combos
  (flag values + status badge), columns = p99 TTFT, p99 TPOT, output tok/s,
  request throughput, completed, duration; best/worst highlighting reused
  from `compare/page.tsx` (extract `PERF_METRICS`/`pickBestWorst`/`getAt`
  into `frontend/src/lib/bench-metrics.ts` and import from both pages).
  Each row links to the run detail; failed combos render the error tail.
  Poll while any run is non-terminal (same refetch pattern as run detail).
- **`/admin/benchmarks`** list page gains a Sweeps tab (name/base/preset/
  progress `k/N`/status/created), linking to sweep detail.
- `queued` status: add to `BenchmarkRun["status"]` union in
  `frontend/src/types/index.ts`, `STATUS_STYLES` maps (list, detail,
  compare), and en/ko i18n. New `benchmarkSweep*` i18n namespaces in both
  `frontend/messages/en.json` and `ko.json`.

## Non-goals

- Accuracy (lm-eval) and sglang sweeps.
- Resource sweeps (gpu_count as a variable) — future extension; the
  merge/expansion design leaves room (a reserved non-`--` variable key).
- Parallel combo execution (defeats same-resources isolation and hogs GPU).
- YAML manifest preview on the sweep form (per-run preview still exists on
  the classic form).
- Regression baselines / scheduled re-runs.
- Deleting sweeps.

## Edge notes

- **Sequential by construction:** at most one non-queued, non-terminal run
  per sweep; each combo's pod fully releases GPU (Job terminal) before the
  next is created.
- **Freeze-at-submit:** manifests are prebuilt, so template-deployment edits
  or external-serving changes mid-sweep cannot skew later combos.
- Per-combo serve restart re-pays model load time each combo (~minutes on
  PVC); acceptable v1 cost for isolation. Multi-bench-pass-per-serving is a
  possible v2 optimization for bench-side variables.
- Flag merge touches only the serve argv; bench-side load stays
  preset-fixed for every combo.
- `queued_job_manifest` holds the api key; excluded from all serializers,
  cleared at promotion/cancel. Same trust boundary as `serving_snapshot.env`
  (external mode) already stored today.
- Sweep-run duplicates via the classic form's "load from run" prefill: fine —
  it produces a standalone run with the same params (preset key included).

## Verification

- Backend tests: combo expansion (row-major order, merge replace vs append,
  per-combo snapshot args, caps and flag validation → 400s); create path
  (N queued rows + sweep row, only combo #0's Job created, manifest cleared
  on promotion, key never in serialized output); reconciler promotion
  (terminal → next; failure continues; last combo → sweep completed;
  restart-resume idempotency); cancel (current Job deleted, queued →
  cancelled). pytest 0 new failures, ruff 0 new.
- Frontend: `tsc --noEmit` + build clean; en/ko key parity for new
  namespaces; `queued` styled everywhere `STATUS_STYLES` exists.
- Manual: 2×2 sweep (`--max-num-seqs` × `--gpu-memory-utilization`) against
  a small template deployment; confirm sequential Jobs, one failure combo
  (forced low memory) not blocking the rest, table highlights best/worst.
