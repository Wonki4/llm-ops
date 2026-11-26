# Preset-based performance benchmarks

## Changes

The new-run screen has one target form: API Base URL and an optional API key, followed by workload
and an execution summary. There are no clone/direct/LiteLLM/URL mode choices. A single model from
`/v1/models` is selected automatically, multiple models require selection, and servers without model
discovery support allow manual model entry. Advanced settings contain only an optional request-rate
limit and runner cluster/namespace. Name and note have their own optional section. Seed, tokenizer,
ignore-EOS, runner-image overrides and live YAML preview are removed from manual creation; reruns
restore visible workload settings but use current defaults for those removed settings. The form
discloses this on replay. Historical records and the preview API remain compatible. The runner
cluster must reach the supplied URL.
Recipe validation continues to provision its candidate serving and passes the resulting URL/key to
the runner. Old clone APIs and history remain compatible, but new manual runs only measure a URL.

List rows and detail headers show configured input length, maximum output, concurrency and shared
input ratio even without a name/note. Estimated text inputs are labeled; Agentic uses turn-dependent
inputs and conversation concurrency instead of irrelevant synthetic values. Older two-phase cache
runs show the 0% baseline and configured reuse ratio. Unknown values stay unavailable, not zero.
Second-resolution timestamps and short run IDs distinguish repeated runs with identical settings.

Measurement history groups two-part API names such as `A/B` and `A/C` under a foldable `A`
in the left navigation. Selecting a child opens the history for its full API name on the right.
Standalone names and explicit local paths appear as individual entries. Model groups aggregate
the entire history by the exact API model name, so
different owners, aliases, case or absolute paths are never merged. Absolute paths are shortened
visually with the full name available in the heading and tooltip. Groups show run counts and are
ordered by most recent execution; each model's records are newest first with 25 rows per page.
Search is case-insensitive substring matching. Model, filters and page are reflected in the URL,
including the return link from a run detail. Comparison selections survive model/page switches.

Synthetic input uses one measured phase. Initial chat, long-input and long-output presets share 90%
of their configured input; administrators can change this in shared presets or in one run. Long-input
is the initial default, with 100K (100,000) input units and a 1K (1,000) output-token cap.
Output is capped at the configured length and throughput uses actual server
usage. Manual runs use normal EOS handling. Recipe validation retains its explicit comparison options.
Rate limits accept positive decimals; blank means no rate limit within the concurrency bound. Agentic
rate is labeled conversations/second because it paces conversation starts, not individual turns.

The built-in Agentic preset replays three fixed turns: question, recorded search results, recorded
read-document results. Defaults: 40 conversations, 8 simultaneous conversations, 512 maximum output
tokens per turn, 120 measured calls. Every turn calls the model. Tools are never executed, and the
model's generated answer does not change subsequent inputs. Scenario version 1 and its SHA256 are
pinned in the run. Input lengths grow with recorded history; no synthetic 90% ratio is applied.

Warmup and prefix preparation happen before measurement and counter snapshots. Agentic preparation
allows a complete output budget: a single token may contain only tool-call metadata. Turn failures
skip the rest of that conversation while other conversations continue. Results retain failed and
skipped counts, successful conversations, per-turn metrics and conversation durations. Whole-run
throughput divides generated tokens by the whole measurement interval, not by summed request times
or averaged turn rates. Partial runs cannot win recipe validation.

## Interfaces and compatibility

- Admin `GET /api/benchmarks/model-groups` returns model names, full-history run/status counts and
  latest execution time, filtered by experiment/status. It does not load result payloads. Existing
  `GET /api/benchmarks` retains its response shape and adds nonnegative `offset` for model paging.
  Group and run filters share the same conditions; timestamp ties use the run ID for stable order.
- `GET /api/benchmarks/presets` retains `presets` and adds `default_preset`.
- Admin `PUT /api/benchmarks/presets` replaces the preset collection and default atomically. At least
  one preset and a valid default are required. Stored in `custom_portal_settings`, key
  `benchmark_presets_v2`; no migration.
- Run params use `workload_version: 2` and `workload_type: synthetic | agentic_replay`, `cache_ratio`,
  `num_sessions` and a server-pinned `scenario` snapshot. New fields imply version 2 if omitted.
  Requests without new fields retain version 1 behavior. Params, including effective preset name
  when matched, are copied into history; workers do not reload live presets.
- Existing results, including accuracy and two-phase cache comparisons, remain readable. Replaying
  an old cache comparison uses its saved fraction and one phase. New accuracy creation/preview is
  rejected; historical accuracy workers are retained for existing jobs.
- New recipe validations use conditions version 2 and the shared workload, default three repeats,
  throughput-median ranking and existing restoration/revalidation/publication. Accuracy requirements
  are removed for new jobs. Tokenizer and GPU comparability checks remain. Existing condition
  version 1 is inferred for old records and retains its quality gates and measurements.
- Credentials continue to use owned Kubernetes Secrets. Metrics attribution is server-selected;
  inherited serving environment variables cannot opt shared targets into isolated counters.
- Admin `POST /api/benchmarks/endpoint-models` queries the supplied URL using only the supplied key.
  It does not persist credentials, follow redirects or return upstream error bodies. A failed model
  lookup does not prevent a manually named run from a cluster with different network access.

## Measurement boundaries

A private, single-Pod vLLM clone uses token IDs with `/tokenize` and `/v1/completions`, keeping total
input length exact. Explicit tokenizer overrides and general OpenAI-compatible endpoints use text
chat inputs. Re-tokenization and server chat templates affect their actual token counts; the result
explains this and reports server usage. These paths must not be presented as exact input-token tests.
URL measurements without an explicit tokenizer use a bundled word corpus and freeze
`input_length_mode: text_estimate` in the run. Input size and shared ratio use those word units, with
the approximation disclosed in the form and result; actual token totals and throughput always come
from server usage. A LiteLLM/API model alias never triggers a Hugging Face download in this mode.

Cache counters are reported only for isolated targets with available metrics. Shared endpoints,
routers/P/D targets, missing counters, counter resets or zero queries report unavailable, rather than
0%. Positive query deltas with no hits report 0%. The configured common-input ratio is not a promised
cache hit rate. Tool-only streamed responses count argument output, not tool metadata, as first output.
Unsupported tools, oversized contexts and incomplete streams fail visibly; no truncation or silent
conversion to a different input mode is performed.

## Verification

- Model grouping: 9 backend tests query 512 real SQL records across five exact model names,
  including similarly named owners/paths, older models beyond the first 500 runs, matching
  experiment/status counts, stable pagination and administrator-only access. Live PostgreSQL
  groups match the stored local results (3 / 1 / 2). Browser checks cover actual model switching,
  cross-model comparison, refresh and detail return. A non-persisted 52-run browser fixture checks
  25 / 25 / 2 pagination, offsets 0 / 25 / 50, last-page disabling, detail return to page 3,
  model-switch page reset, partial search and no matches. Status/experiment filters and reset
  were checked against the live API. TypeScript, ESLint and production webpack build pass.
- Workload summary: webpack/TypeScript and ESLint pass. Seven browser fixtures cover different
  unnamed runs, zero ratio, estimated inputs, Agentic, legacy cache comparison, missing parameters
  and historical accuracy. Configured 90% is not replaced by an observed 23% hit rate; list/detail
  summaries match and duplicate timestamps retain separate run IDs. Fixtures are not persisted.
- Advanced-options cleanup: 4 workload helper tests, TypeScript, ESLint and webpack build pass.
  Browser checks cover decimal/blank rates, zero/negative rejection (including a collapsed section),
  Agentic conversation-rate labels, optional name/note, and removal of live preview requests.
  A historical-run fixture keeps its 75% ratio, request count and namespace while dropping obsolete
  overrides; the actual backend preview accepts that payload and resolves the default image/input
  mode. Browser creation is intercepted; no Kubernetes Jobs are launched by these checks.
- Unified endpoint follow-up: 99 focused backend tests and 3 workload UI-helper tests pass, plus
  TypeScript, ESLint and a webpack production build. Browser tests use a local HTTP model-list server
  for single-model automatic execution, multiple-model choice, explicit API-key authentication and
  manual fallback after a 404. Job creation is intercepted so browser checks do not provision GPUs.
  The actual HTTP/SSE runner also completes synthetic input for a non-Hugging-Face alias without
  loading a tokenizer, retains 90% shared word units and reports only measured server token usage.
- Initial backend regression coverage includes request validation, preset persistence/default deletion/auth,
  legacy execution, recipe creation/correction/ranking/restoration/publication, secrets and placement.
- Runner tests cover 0/90/100% lengths, one phase, preparation exclusion, Agentic ordering/concurrency,
  partial failures and skipped turns, frozen input hashes, tool-only streams and throughput arithmetic.
- A localhost HTTP/SSE fixture runs the real runner entrypoint: measured counter deltas are
  540 hits / 600 queries = 90%, preparation excluded; Agentic completes two three-turn conversations.
  This is protocol validation, not a GPU performance claim.
- 10 frontend tests pass; coverage includes defaults, edits without preset mutation, legacy replay and Agentic counts.
  TypeScript, ESLint and production webpack build pass. Webpack is used because this worktree's
  node_modules symlink is outside Turbopack's filesystem root.
- Browser verification covers default/editable ratio, Agentic fields, non-default placement,
  URL request bodies, persistent preset edits, no duplicate form IDs, legacy 75% replay,
  Agentic result tables and shared validation inputs. Creation requests use fixtures so these checks
  do not create GPU jobs. Preset persistence uses the isolated local preview database.

### Local live verification (2026-11-24 KST)

The actual portal API, database, Kubernetes Job, embedded portal runner and benchmark reconciler
were exercised in the `benchmark-verification` namespace of local minikube. The first long-input
run was submitted through the browser, without intercepting creation. The registered non-default
cluster ID was `5857b9ae-77d5-450a-b8e6-16d3284a2580`.

The real inference server was MLX-LM 0.32.2 / MLX 0.32.4 on Apple M1 Pro, 16 GiB RAM, using
`mlx-community/Qwen3.5-0.8B-4bit` at revision
`da28692b5f139cb0ec58a356b437486b7dac7462`. Its model configuration allows 262,144 tokens.
The runner reached `http://host.minikube.internal:18512/v1`; no cloud inference API was used.
The local ARM runner image was `llmops-benchmark:portal-v1`. This is a functional live-model
verification, not a production vLLM or CUDA performance comparison.

- Long input, run `fa2f357c-8237-4c1d-99ab-27151786b560`: **1/1 successful**, input setting
  100,000 estimated word units, output cap 1,000, concurrency 1, shared ratio 90%. Actual server
  usage was **125,174 input tokens and 1,000 output tokens**. Measured TTFT was **221,559.6 ms**,
  measured wall time **250.652 s**, and output throughput **3.990 tok/s**. Job lifetime including
  warmup and prefix preparation was about 12m 16s; the measured metrics exclude those requests.
  One sample does not establish a meaningful tail-latency distribution. Result values and the
  estimated-input disclosure persisted across browser refresh; unavailable cache counters showed
  `측정 불가`, not 0% or the configured 90%.
- Concurrent-client run `bc0bc119-b83f-43ec-81d8-fe5df8fd85f7`: **8/8 successful**, input setting
  reduced to 1,000 estimated units, output cap 1,000, concurrency 8, shared ratio 90%. Actual
  totals were 9,973 input / 8,000 output tokens; measurement took **82.242 s**, throughput
  **97.274 tok/s**, median TTFT **37,045.6 ms**. MLX inference was configured with decode and
  prompt concurrency 1 to limit memory; client concurrency 8 therefore exercised server queuing,
  not eight simultaneous model decoders. This does not replace the full 100K concurrency test.
- Agentic run `087d8e3a-00d7-4c38-94e0-0c75dd12acb0`: **2/2 conversations and 6/6 calls
  successful**, concurrency 2, output cap 128 per turn. Actual totals were 6,096 input / 608
  output tokens over **7.228 s**. Each turn completed twice; actual average input grew
  **372 → 487 → 2,189 tokens**. There were no failed or skipped turns. Per-turn totals matched
  overall usage, and throughput matched total output divided by measured wall time. Recorded tool
  results were replayed; no live search or document tools were invoked. Browser expansion of the
  turn table matched these input lengths, showed 2/2 successes for each turn and 0 failures/skips.
- Full-shape protocol run `0ec2c172-7e18-4c00-a03f-b90a7720b430`: **120/120 successful** through
  an authenticated HTTP/SSE fixture, **not a model**. Independent server counters observed 122
  requests: 2 preparation and 120 measured; peak concurrency 8; every measured request had
  100,000 word units and output cap 1,000. All measured inputs shared the same 90,000-word prefix
  and had 120 distinct suffixes. The fixture's synthetic token usage/latency is not model
  performance. The Job used an owned Kubernetes Secret, and neither the API result nor Job spec
  contained the key value.
- Context-limit failure run `621ed59b-9ba2-4b9b-98f9-1567fafffcf9`: the initial CPU Qwen server
  rejected a 124,994-token request against its configured 8,192-token context. The actual HTTP 400
  was stored as a failed run, without truncation or fabricated performance metrics. A separate
  1K attempt (`01e7cc22-3faf-4b20-9af3-42ab6259b0f4`) exposed that server's reused-connection
  failure; the successful MLX run used the unchanged portal streaming implementation.
- A running detail page's elapsed time stayed frozen when polling returned unchanged data.
  The display now uses React Query's latest successful fetch timestamp. Browser checks observed
  5m 32s → 5m 47s → 7m 3s without reload. TypeScript, ESLint, production webpack build and
  `git diff --check` passed; the completed result page had no browser console errors.
- The rebuilt new-run form retained defaults 100,000 / 1,000 / 120 requests / concurrency 8 / 90%.
  The live long-input result row showed the same input/output/concurrency/ratio as its detail page.

The full **100K / 1K / 120 requests / concurrency 8** configuration has protocol coverage above,
but has not completed against a real model. Prefix-caching-enabled/disabled vLLM engine counters,
production GPU performance, production tool-parser compatibility and closed-network completion
still require the deployment environment. Apply the existing validation
runtime-stability PR (#290) before GPU acceptance. This work is a separate follow-up branch based on
the menu stack, not part of its existing PRs.
