import type { EngineArgs, ModelDeployment, ServingEngine, ServingRecipeInput } from "@/types";
import { ENGINE_ARG_FIELDS, type EngineArgField } from "@/lib/serving-engines";

/**
 * Pre-filling the recipe form from something that already runs.
 *
 * Two sources: a portal deployment (structured columns, a straight field copy)
 * and a reverse-parsed external Deployment (the backend returns every long flag
 * in `engine_args`; here the flags the form has no field for move to the
 * free-text extra args so nothing is hidden from the operator).
 */

/** Recipe fields of a portal deployment; instance fields (namespace, replicas, ingress) are dropped. */
export function recipeFromDeployment(dep: ModelDeployment, name: string, description: string | null): ServingRecipeInput {
  return {
    name,
    description,
    model_path: dep.model_path,
    image: dep.image,
    gpu_count: dep.gpu_count,
    gpu_resource_key: dep.gpu_resource_key,
    cpu_request: dep.cpu_request,
    cpu_limit: dep.cpu_limit,
    memory_request: dep.memory_request,
    memory_limit: dep.memory_limit,
    node_selector: dep.node_selector,
    tolerations: dep.tolerations,
    storages: (dep.storages ?? []).map(({ storage_id, mount_path, read_only }) => ({ storage_id, mount_path, read_only })),
    vllm_extra_args: dep.vllm_extra_args,
    env: dep.env,
    engine: dep.engine,
    engine_args: dep.engine_args,
    probes: dep.probes ?? null,
    gpu_type: dep.gpu_type ?? null,
    serving_mode: dep.serving_mode ?? "aggregated",
    pd_config: dep.pd_config ?? null,
    runtime: dep.runtime ?? null,
  };
}

type Scalar = string | number | boolean;

/** Coerce a parsed value to what the form field expects; `undefined` = does not fit, relocate. */
function fitField(field: EngineArgField, value: Scalar): Scalar | undefined {
  switch (field.type) {
    case "int": {
      const n = typeof value === "number" ? value : typeof value === "string" ? Number(value) : NaN;
      return Number.isInteger(n) ? n : undefined;
    }
    case "float": {
      const n = typeof value === "number" ? value : typeof value === "string" ? Number(value) : NaN;
      return Number.isFinite(n) ? n : undefined;
    }
    case "bool":
      return value === true ? true : undefined;
    case "select":
      return typeof value === "string" && field.options.includes(value) ? value : undefined;
    case "text":
      return typeof value === "boolean" ? undefined : String(value);
  }
}

/** `--key=value` (or `--key` for true) as one extra-args line. */
export function flagLine(key: string, value: Scalar): string {
  return value === true ? `--${key}` : `--${key}=${String(value)}`;
}

/**
 * Split parsed `engine_args` into form fields vs. extra-args lines for `engine`.
 * Returns the adjusted input and the flags that were relocated, so the page can
 * tell the operator where they went.
 */
export function normalizeDraft(draft: ServingRecipeInput): { input: ServingRecipeInput; relocated: string[] } {
  const engine: ServingEngine = draft.engine === "sglang" ? "sglang" : "vllm";
  const split = splitEngineArgs(engine, draft.engine_args, draft.vllm_extra_args);
  return { input: { ...draft, engine, ...split.value }, relocated: split.relocated };
}

/**
 * Keep the `engine_args` the form has a field for; turn the rest into extra-args
 * lines (appended), so no flag is hidden from the operator or lost on save.
 */
export function splitEngineArgs(
  engine: ServingEngine,
  engineArgs: EngineArgs | null | undefined,
  extraArgs: string[] | null | undefined,
): { value: { engine_args: EngineArgs | null; vllm_extra_args: string[] | null }; relocated: string[] } {
  const fields = new Map(ENGINE_ARG_FIELDS[engine].map((f) => [f.key, f]));
  const kept: EngineArgs = {};
  const relocated: string[] = [];
  for (const [key, raw] of Object.entries(engineArgs ?? {})) {
    if (raw == null || raw === false || raw === "") continue;
    const field = fields.get(key);
    const fitted = field ? fitField(field, raw) : undefined;
    if (fitted !== undefined) kept[key] = fitted;
    else relocated.push(flagLine(key, raw));
  }
  const extra = [...(extraArgs ?? []), ...relocated];
  return {
    value: { engine_args: Object.keys(kept).length ? kept : null, vllm_extra_args: extra.length ? extra : null },
    relocated,
  };
}
