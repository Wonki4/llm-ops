# Serving Recipes — vLLM / SGLang engine split with structured engine args — Design

**Date:** 2026-09-09
**Status:** Approved (design, in chat)

## Goal

Let a serving recipe (and the deployment launched from it) say **which engine**
it runs — **vLLM** or **SGLang** — and expose each engine's **main launch
arguments as dedicated form fields** instead of one free-text args box. A
deployment launched from an SGLang recipe must actually start an SGLang server.

## Scope

**In scope:**

- `engine` + `engine_args` on recipes and portal-managed deployments (one migration).
- Manifest rendering that branches on engine (container command/args).
- Recipe API + deployment create/update API accept and return the new fields.
- Recipe form: engine toggle, per-engine structured arg fields, free-text extra args.
- Deploy-from-recipe dialog forwards `engine` / `engine_args`.
- Recipe list, deployments list and deployment detail show the engine.
- Benchmark ephemeral deployments copy `engine` / `engine_args`, and the
  self-serving bench job's serve argv is derived from the same launch helper.

**Follow-ups (not this spec):** SGLang-specific benchmark tooling changes
(`sglang_serving` tool selection stays as it is today); llm-d stacks; external
serving registration (already engine-aware via image detection).

## Background — verified facts

- **Portal deployments are vLLM-only today.** `build_deployment()` in
  `backend/app/services/model_deployment_manifests.py` renders container args as
  `--model <path> --port 8000` and relies on the image entrypoint (vLLM's
  OpenAI server). Container name is `vllm`; readiness probe is `GET /health`.
- Neither `custom_serving_recipe` nor `custom_model_deployment` has an engine
  column. Latest migration is `045_llmd_direct_route`.
- SGLang exists only in external-serving detection (`_detect_engine` in
  `backend/app/services/external_servings.py`, by image name) and the benchmark
  tool enum (`sglang_serving`).
- `build_deployment()` is also called for **benchmark ephemeral deployments**
  (`backend/app/services/benchmark_serving.py`, `backend/app/api/benchmarks.py`),
  which are in-memory `CustomModelDeployment` rows built via
  `build_ephemeral_deployment(base, ...)` that copies fields explicitly. The
  self-serving performance job builds its own
  `serve_argv = ["vllm", "serve", model_path, "--port", ...]` at two call sites.
- In-memory rows do not get SQLAlchemy column defaults, so new columns read as
  `None` there (existing code already guards `gpu_count` the same way).
- The recipe form is a full page (`/admin/recipes/new`, `/admin/recipes/[id]`)
  backed by `frontend/src/components/serving-recipe-form.tsx`, with cards
  Basics / Resources / Storage / Advanced. The Advanced card holds the vLLM args,
  env and node-selector textareas.
- Both engines serve an OpenAI-compatible API and answer `GET /health`, so the
  readiness probe, Service and Ingress are engine-independent.

## Decisions

1. **One `engine` column, values `vllm` | `sglang`, default `vllm`.** Existing
   rows become vLLM, which matches current behaviour.
2. **Structured args are stored as a JSONB dict `engine_args`** keyed by the
   bare flag name (no leading dashes), e.g.
   `{"tensor-parallel-size": 4, "dtype": "bfloat16", "enable-prefix-caching": true}`.
   The backend renders it **generically** into CLI flags and validates only the
   key shape and value types; it does not hold an allow-list. The curated list of
   "main args" lives in one frontend constant per engine, so adding a field later
   is a frontend-only change.
3. **`vllm_extra_args` keeps its column and API name** but is now the
   engine-neutral "extra args" list. Renaming would touch a migration, both
   APIs, the benchmark override path and the frontend for no behaviour gain.
4. **Final container args order:** engine base args, then rendered
   `engine_args` (keys sorted), then `vllm_extra_args`. Sorting makes manifests
   deterministic; extra args last lets a power user override a structured value.
5. **Default images per engine:** `vllm/vllm-openai:latest`,
   `lmsysorg/sglang:latest`. The image field stays free text (air-gapped users
   point at their internal registry).
6. **Switching engine in the form resets `engine_args`** (the keys differ per
   engine) and swaps the image only if it is empty or equals the other engine's
   default. Free-text extra args are left alone; they are the user's text.
7. **Benchmark serve argv uses the shared launch helper** instead of a hardcoded
   `vllm serve` list, so an SGLang deployment benchmarked through the
   self-serving job starts the right server.

## Architecture

### Data model + migration `046_serving_engine`

Both `custom_serving_recipe` and `custom_model_deployment` get:

| column        | type                                 | notes                       |
|---------------|--------------------------------------|-----------------------------|
| `engine`      | `VARCHAR(16) NOT NULL DEFAULT 'vllm'` | ORM `default="vllm"`, `server_default="vllm"` |
| `engine_args` | `JSONB NULL`                         | dict of flag → value        |

Downgrade drops both columns from both tables.

### Launch rendering (`model_deployment_manifests.py`)

```python
SERVING_PORT = 8000  # VLLM_PORT stays as an alias for existing imports

ENGINE_LAUNCH = {
    "vllm":   {"command": None,
               "model_flag": "--model",
               "base_args": ["--port", "8000"]},
    "sglang": {"command": ["python3", "-m", "sglang.launch_server"],
               "model_flag": "--model-path",
               "base_args": ["--host", "0.0.0.0", "--port", "8000"]},
}

def engine_of(dep) -> str            # getattr(dep, "engine", None) or "vllm"
def render_engine_args(args: dict | None) -> list[str]
def engine_flags(dep) -> list[str]   # render_engine_args(dep.engine_args) + list(dep.vllm_extra_args or [])
def container_launch(dep) -> tuple[list[str] | None, list[str]]
    # (command, [model_flag, model_path, *base_args, *engine_flags])
def serve_argv(dep, port) -> list[str]
    # vllm:   ["vllm", "serve", model_path, "--port", port, *engine_flags]
    # sglang: ["python3", "-m", "sglang.launch_server", "--model-path", model_path,
    #          "--host", "0.0.0.0", "--port", port, *engine_flags]
```

`render_engine_args` rules, keys sorted:

| value type            | rendered as          |
|-----------------------|----------------------|
| `True`                | `--key`              |
| `False` / `None` / `""` | omitted            |
| int / float           | `--key <repr>`       |
| str                   | `--key <value>`      |

`build_deployment()` uses `container_launch()`; when `command` is not `None` it
is set on the container. The container name becomes the engine name. Everything
else in the pod spec is unchanged.

Unknown engine values never reach here: the API validates them. `engine_of`
treats `None` as `vllm` only to keep in-memory rows working.

### API

- `RecipeBody` (`backend/app/api/serving_recipes.py`): add
  `engine: Literal["vllm", "sglang"] = "vllm"` and
  `engine_args: dict[str, str | int | float | bool] | None = None` with a
  validator that rejects keys not matching `^[a-z0-9][a-z0-9-]*$` (422).
  `_serialize` returns both. Create/update copy them to the row.
- `CreateDeploymentRequest` / `UpdateDeploymentRequest`
  (`backend/app/api/model_deployments.py`): same two fields (update: optional).
  The create default image becomes engine-dependent when the client omits
  `image`: `DEFAULT_IMAGES[engine]`. `_serialize` returns both.
- `build_ephemeral_deployment` copies `engine` and `engine_args` from the base
  deployment; the `serving_overrides` dict may carry `engine_args` like it does
  `vllm_extra_args` today.
- The two `serve_argv = ["vllm", "serve", ...]` sites in `benchmarks.py` call
  `serve_argv(eph, VLLM_PORT)`.

### Frontend

**Types** (`frontend/src/types/index.ts`): `ServingEngine = "vllm" | "sglang"`,
`EngineArgs = Record<string, string | number | boolean>`; add
`engine: ServingEngine` and `engine_args: EngineArgs | null` to `ServingRecipe`,
`ServingRecipeInput`, `ModelDeployment`, `CreateDeploymentBody`.

**Engine catalogue** (`frontend/src/lib/serving-engines.ts`): the single place
that knows the curated args.

```ts
type EngineArgField =
  | { key: string; labelKey: string; type: "int" | "float" | "text"; placeholder?: string; step?: number; min?: number }
  | { key: string; labelKey: string; type: "bool" }
  | { key: string; labelKey: string; type: "select"; options: string[] };  // "" option = unset
export const ENGINE_DEFAULT_IMAGE: Record<ServingEngine, string>;
export const ENGINE_ARG_FIELDS: Record<ServingEngine, EngineArgField[]>;
export function engineArgsToFlags(args: EngineArgs | null): string[];  // mirrors backend rendering, for display
```

vLLM fields: `tensor-parallel-size` int, `pipeline-parallel-size` int,
`max-model-len` int, `gpu-memory-utilization` float (step 0.01, placeholder 0.9),
`dtype` select [auto, bfloat16, float16, float32], `quantization` select
["" , fp8, awq, gptq, gptq_marlin, bitsandbytes], `kv-cache-dtype` select
[auto, fp8], `max-num-seqs` int, `max-num-batched-tokens` int,
`served-model-name` text, `enable-prefix-caching` bool,
`enable-chunked-prefill` bool, `trust-remote-code` bool.

SGLang fields: `tp-size` int, `dp-size` int, `context-length` int,
`mem-fraction-static` float (step 0.01, placeholder 0.88), `dtype` select
[auto, bfloat16, float16, float32], `quantization` select
["", fp8, awq, gptq, bitsandbytes], `kv-cache-dtype` select
[auto, fp8_e5m2, fp8_e4m3], `max-running-requests` int,
`chunked-prefill-size` int, `served-model-name` text, `disable-radix-cache`
bool, `trust-remote-code` bool, `enable-torch-compile` bool.

Labels come from i18n keys `servingRecipes.arg.<camelCaseKey>`; the flag name is
shown under the label in monospace so admins can match docs.

**Recipe form** (`serving-recipe-form.tsx`):

- Basics card gains an **Engine** segmented control (two buttons, vLLM /
  SGLang) as the first row. Switching applies decision 6.
- New **Engine args** card between Storage and Advanced: the engine's fields in
  a 2-column grid (bool fields as checkboxes in a wrap row), then the free-text
  **Extra args** textarea moved here from Advanced. Empty text / unchecked bool
  / "" select do not appear in `engine_args`; numbers are stored as numbers.
- Advanced keeps env and node selector.
- Required check unchanged (name, model path, image).

**Recipe list**: engine badge in the name cell, before the name. **Deploy
dialog**: forwards `engine` and `engine_args`; header line shows the engine.
**Deployments list**: engine badge in the model column. **Deployment detail**:
engine badge in the header; the args block shows
`[...engineArgsToFlags(engine_args), ...vllm_extra_args].join(" ")` under an
engine-neutral "Launch args" label.

**i18n** (en + ko): `engine`, `engineVllm`, `engineSglang`, `sectionEngineArgs`,
`extraArgs` (recipe form), `arg.*` labels, deployments `extraArgs` relabelled to
"Launch args" / "실행 인자", plus an `engine` label for the deployment detail.

## Error handling

- Invalid `engine` → 422 from pydantic `Literal`.
- `engine_args` key with a bad shape (leading dash, spaces, uppercase) or a
  non-scalar value → 422 with the offending key in the detail.
- Form: no new client validation beyond input types; server errors surface via
  the existing toast path.

## Testing

Backend (`pytest` in `backend/`):

- New `tests/test_model_deployment_manifests.py`:
  - vLLM deployment renders exactly today's args (regression) and no `command`.
  - SGLang deployment renders the `python3 -m sglang.launch_server` command,
    `--model-path`, `--host 0.0.0.0`, `--port 8000`, container name `sglang`.
  - `render_engine_args`: sorted keys, bool true/false, empty string, numbers.
  - `engine_of` on an in-memory row without `engine` → `vllm`.
  - `serve_argv` for both engines.
- `tests/test_serving_recipes.py`: default engine in serialize, `engine_args`
  round-trip, invalid engine 422, invalid `engine_args` key 422.
- Existing benchmark tests keep passing (`serve_argv` for vLLM must equal the
  old hardcoded list).

Frontend: `tsc --noEmit`, `eslint`, and a Playwright pass against the dev server
with mocked API: create a vLLM recipe and an SGLang recipe and assert the POST
bodies carry `engine` and typed `engine_args`; edit round-trips; deploy dialog
body carries both fields.

## Non-goals

- Per-engine validation of flag names or values on the server.
- Engine-specific readiness paths or ports.
- Migrating existing `vllm_extra_args` strings into `engine_args`.
- Changing which benchmark tool runs for SGLang targets.
