# Benchmark Single-Run Redesign — Design

**Date:** 2026-07-23
**Status:** Approved direction (user: retire sweeps → single runs; preset seeds a
live `vllm bench serve` command preview; a free-text flags field appends
verbatim to the backend; add label + note to a run)

## Problem

The sweep feature (PR #204) added a grid of `vllm serve` flag variables over a
base serving, executed as sequential self-serving Jobs with an auto-comparison
table. In practice it grew too many knobs (base-serving selector + variable
grid + preset), and the "spin up a serving per combo" model is heavier than
most runs need. The user wants to go the other direction: one simple
single-run form where a load **preset** fills in the `vllm bench serve` command,
the command is shown live and can be freely extended, and each run carries a
**label** and a **note** for later identification and comparison.

## Scope

Two coordinated changes:

1. **Retire the sweep feature entirely** (it was merged but never ran a real
   cluster QA, so removal risk is low).
2. **Enhance the existing single-run benchmark form** (`/admin/benchmarks/new`)
   with a preset → live command preview → append-flags UX, plus label + note.

The two target modes the user asked for **already exist** on the single-run
form and are kept:
- **Endpoint mode** (no serving spin-up): the bench Job hits an already-running
  target — a portal deployment's Service URL (`direct`) or a LiteLLM model
  alias (`model`).
- **Serving mode** (self-serving): clone a portal deployment or an external
  serving and serve+bench in one pod (`clone`).

## Design

### A. Retire the sweep feature

Delete:
- Backend: `app/api/benchmark_sweeps.py`, `app/services/benchmark_sweeps.py`,
  `app/db/models/custom_benchmark_sweep.py`, the `_drive_sweeps` step in
  `app/jobs/reconcile_benchmarks.py`, the router registration in `main.py`, and
  tests `test_benchmark_sweep_helpers.py` / `test_benchmark_sweeps_api.py` /
  `test_reconcile_sweeps.py`.
- Frontend: `admin/benchmarks/sweeps/new/page.tsx`,
  `admin/benchmarks/sweeps/[id]/page.tsx`, the sweeps tab + `SweepsTable` on
  `admin/benchmarks/page.tsx`, and the sweep hooks/types
  (`useBenchmarkPresets` is KEPT — see §B; `useBenchmarkSweep(s)`,
  `useCreateBenchmarkSweep`, `useCancelBenchmarkSweep`, `BenchmarkSweep`,
  `SweepVariable`, `CreateBenchmarkSweepRequest` removed).
- `GET /api/benchmarks/presets` is KEPT (moves conceptually from "sweep presets"
  to "benchmark presets"); it stays served by a small router. Simplest: keep the
  `list_presets` handler but move it into `app/api/benchmarks.py` so the sweeps
  module can be deleted cleanly. `benchmark_presets.py` service is KEPT.

Migration **042** (`042_drop_benchmark_sweeps`):
- `DROP TABLE custom_benchmark_sweep`.
- Drop the four sweep columns on `custom_benchmark_run`: `sweep_id`,
  `sweep_index`, `sweep_combo`, `queued_job_manifest`.
- `downgrade()` recreates them (mirror of migration 041's additions) so the
  pair is reversible.

Status values: the `queued` status existed only for sweeps. No enum/migration
change is needed (status is a free `String(16)`); nothing writes `queued` after
the sweep code is gone. The reconciler's `provisioning`/`pending`/`running`
handling for ordinary + accuracy-ephemeral runs is untouched.

### B. Enhanced single-run form (`admin/benchmarks/new`)

The form keeps its target-mode tabs (`clone` / `direct` / `model` / `fromRun`)
and its accuracy path unchanged. The **performance** params section is
replaced:

Old: a block of ~13 raw perf inputs (num_prompts, random_input_len, …) + an
extra-JSON editor + an extra-args field.

New (performance tools only):
1. **Preset cards** (chat / long_input / long_output) from
   `GET /api/benchmarks/presets`. Selecting one sets the load params
   (random_input_len/output_len, num_prompts, max_concurrency, seed,
   ignore_eos) — the same values `preset_params()` returns.
2. **Live command preview**: a read-only, monospace render of the exact
   `vllm bench serve …` argv that will run, built client-side to mirror
   `_vllm_bench_argv` (backend stays the single source of truth; the preview is
   presentational). Target-derived flags (`--base-url`, `--model`,
   `--tokenizer`, `--save-result`/`--result-dir`/`--result-filename`) are shown
   but not user-editable — they wire the run to its target and to result
   harvesting.
3. **Additional flags** free-text field: whatever the user types is **appended
   verbatim** to the command. This maps directly to the existing
   `params.extra_args` passthrough (`shlex.split(extra_args)` appended in
   `_vllm_bench_argv`) — no new backend mechanism. The preview appends the same
   text so what-you-see is what-runs. A malformed value simply fails the Job
   with vLLM's own arg error (surfaced as `error_message`), same as today.

`num_prompts`/`max_concurrency`/`request_rate` etc. that a preset doesn't cover
are no longer surfaced as individual inputs — a user who needs to override them
does so through the additional-flags field (e.g. `--request-rate 8`), keeping
the form to "preset + free flags".

The submit body is unchanged shape-wise: `params` carries the preset expansion
(+ `preset` key) and `extra_args`; `label`/`note` are new top-level fields
(§C). NFS / namespace / image / cluster / api_key stay as they are (advanced).

### C. Label + note on a run

`custom_benchmark_run` gains (migration 042, same migration as the drops):
- `label` `String(128)` nullable — a short human identifier.
- `note` `Text` nullable — a longer free-form memo.

- `CreateBenchmarkRequest` gains optional `label` / `note`; `create_benchmark`
  and `preview_benchmark` persist them (preview ignores them). `_serialize`
  returns both.
- Form: a **Label** input and a **Note** textarea, shown for both performance
  and accuracy runs.
- **List** (`admin/benchmarks/page.tsx`): a Label column (falls back to a dash),
  shown before/with the model name so runs are identifiable at a glance.
- **Detail** (`admin/benchmarks/[id]/page.tsx`): label in the header, note in a
  block.
- **Compare** (`admin/benchmarks/compare/page.tsx`): the per-run column header
  shows the label when set (this is what replaces the retired sweep's grouped
  auto-comparison — pick runs by label, compare).

## Non-goals

- No sweep/grid execution of any kind (that is exactly what's removed).
- No full free-text editing of the whole command string (fragile to re-parse;
  the target/result flags must stay intact). Extension is append-only.
- Accuracy (lm_eval) params keep their current editor; presets and the command
  preview are performance-only.
- No change to the reconciler's run lifecycle, self-serving Job builder, or
  target resolution beyond removing the sweep step.

## Edge notes

- The command preview must stay faithful to `_vllm_bench_argv`; if the two
  drift, the preview misleads. Keep the preview's flag list minimal and
  derived from the same param names, and treat the backend argv as
  authoritative (the preview is a convenience, not a contract).
- `extra_args` already appends after all portal flags, so a user flag can
  override an earlier one only where vLLM's argparse allows last-wins;
  otherwise duplicate flags error out — acceptable and surfaced.
- Runs created before this change keep working; the dropped sweep columns were
  only read by sweep code, which is gone. Any rows with `status = 'queued'`
  (in-flight sweeps at deploy) would be orphaned — acceptable given no real QA
  yet, but the migration notes it; operationally, cancel in-flight sweeps
  before deploy.

## Verification

- Backend: migration 042 up/down reversible on a scratch DB; `create_benchmark`
  persists `label`/`note` and `_serialize` returns them; `GET /presets` still
  200s after the sweeps module is deleted; full suite has no NEW failures vs
  baseline; ruff no new. Sweep tests are deleted (not left failing).
- Frontend: `tsc --noEmit` clean; `npm run build` succeeds; the sweeps routes
  and imports are gone (no dangling imports); en/ko i18n key parity holds; the
  command preview matches a hand-checked `vllm bench serve` line for each
  preset.
- Manual: run one performance benchmark in endpoint mode and one in serving
  mode, each with a preset + an extra flag + a label + note; confirm the
  preview matched the Job's actual command, and the label/note show in
  list/detail/compare.
