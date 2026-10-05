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
    pvc_name: dep.pvc_name,
    pvc_mount_path: dep.pvc_mount_path,
    vllm_extra_args: dep.vllm_extra_args,
    env: dep.env,
    engine: dep.engine,
    engine_args: dep.engine_args,
    probes: dep.probes ?? null,
    gpu_type: dep.gpu_type ?? null,
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
  const fields = new Map(ENGINE_ARG_FIELDS[engine].map((f) => [f.key, f]));
  const kept: EngineArgs = {};
  const relocated: string[] = [];
  for (const [key, raw] of Object.entries(draft.engine_args ?? {})) {
    if (raw == null || raw === false || raw === "") continue;
    const field = fields.get(key);
    const fitted = field ? fitField(field, raw) : undefined;
    if (fitted !== undefined) kept[key] = fitted;
    else relocated.push(flagLine(key, raw));
  }
  const extra = [...(draft.vllm_extra_args ?? []), ...relocated];
  return {
    input: {
      ...draft,
      engine,
      engine_args: Object.keys(kept).length ? kept : null,
      vllm_extra_args: extra.length ? extra : null,
    },
    relocated,
  };
}
