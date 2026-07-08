# External Serving CLI Parsing for Clone-Bench — Design

**Date:** 2026-07-08
**Status:** Approved (user: "오케이 부탁")

## Problem

`external_bench_facts` reads only `container.args` and only the
`--model`/`--served-model-name` flag forms. Real servings commonly launch as
`vllm serve <model>` (positional), SGLang's `--model-path`, flags placed in
`command` instead of `args`, or a single `sh -c "vllm serve …"` shell string.
All of these fail benchmark creation with 400
"no --model/--served-model-name found in serving args" even though the
serving obviously has a model. The same args-only assumption weakens
`_clone_target_port` (`--port` in command is missed) and the snapshot's
`vllm_extra_args` (bench-job `--api-key` derivation misses command-form keys).

## Decision

Normalize the serving's launch line once and parse everything against it.

New public helper in `backend/app/services/benchmark_serving.py`:

- `serving_cli(container: dict) -> list[str]` — concatenates
  `command + args` (either may be missing/None) and expands any token
  containing whitespace via `shlex.split` (covers `sh -c "…"`), falling back
  to the raw token if shlex fails.
- `_positional_model(cli) -> str | None` — the token immediately after a
  `serve` token, when it exists and does not start with `-`.

`external_bench_facts` resolves:

- `model_arg = --model | serve-positional | --model-path` (first match)
- `served_model = --served-model-name | model_arg`
- ValueError message lists everything it looked for.
- Tokenizer and PVC-mount detection keep using `model_arg` (unchanged
  semantics).

`_clone_target_port` reads `--port` from `serving_cli(container)`.

`backend/app/api/benchmarks.py` external create stores
`"vllm_extra_args": serving_cli(spec["container"])` in the snapshot instead
of raw `args`, so `serving_api_key` finds `--api-key` regardless of where
the launch line lives. The reconciler reads only `--api-key` from this
field — a merged superset is strictly compatible. No schema/API/frontend
change.

## Non-goals

- No support for models injected purely via env vars or config files.
- No multi-container scanning (first container, existing v1 limit).
- `serve` positional heuristic is token-based; a `--flag serve` value
  collision is accepted as out of scope (flags are checked first anyway).

## Verification

- New tests in `backend/tests/test_bench_external_clone.py`: positional
  `vllm serve`, SGLang `--model-path` (+`--served-model-name`), flags in
  command-only, `sh -c` shell string (facts + clone target port), PVC
  detection with positional model, container missing both command/args
  (clean ValueError), API-level snapshot merged-CLI assertion.
- Backend gates: pytest 0 NEW failures (baseline 21), ruff 0 NEW
  (baseline 78). Existing flag-form tests must pass unchanged.

## v1 implementation notes (post final review)

Final whole-branch review: Ready to merge — 7/7 checks PASS, including an
end-to-end reconciler trace of an `sh -c "vllm serve …"` serving (bench Job
gets served-name --model + weights-path --tokenizer; clone Service targets
the parsed --port) and a full snapshot-reader sweep (only serving_api_key
reads vllm_extra_args; merged CLI is a compatible superset). Ship-as-is
notes: run-detail/compare pages now display the merged launch line for
external runs (more faithful, cosmetic); `serve`-positional heuristic can
misfire only on a hypothetical flag whose value is literally "serve" with no
--model present (spec non-goal).
