import type { LoadPreset } from "@/types";

export const DEFAULT_PERF_PARAMS = {
  workload_type: "synthetic" as "synthetic" | "agentic_replay",
  random_input_len: 100_000, random_output_len: 1_000, num_prompts: 120,
  max_concurrency: 8 as number | string, cache_ratio: 90, num_sessions: 40,
  request_rate: "" as number | string, seed: 0, ignore_eos: false, tokenizer: "",
  scenario: undefined as Record<string, unknown> | undefined,
  goodput: "",
};
export type PerfParams = typeof DEFAULT_PERF_PARAMS;
export const requestCount = (p: PerfParams) => p.workload_type === "agentic_replay" ? p.num_sessions * 3 : p.num_prompts;
export function applyPreset(p: PerfParams, preset: LoadPreset): PerfParams {
  return { ...p, ...preset, workload_type: preset.workload_type ?? "synthetic", num_sessions: preset.num_sessions ?? 40,
    cache_ratio: preset.cache_ratio ?? 0, scenario: undefined };
}
export function matchedPresetKey(presets: Record<string, LoadPreset> | undefined, p: PerfParams) {
  return Object.entries(presets ?? {}).find(([, x]) =>
    (x.workload_type ?? "synthetic") === p.workload_type && x.random_output_len === p.random_output_len &&
    Number(x.max_concurrency) === Number(p.max_concurrency) && (p.workload_type === "agentic_replay" ?
      (x.num_sessions ?? 40) === p.num_sessions :
      x.random_input_len === p.random_input_len && x.num_prompts === p.num_prompts && (x.cache_ratio ?? 0) === p.cache_ratio))?.[0];
}
export function workloadParams(p: PerfParams): Record<string, unknown> {
  return { workload_version: 2, workload_type: p.workload_type, random_input_len: p.random_input_len,
    random_output_len: p.random_output_len, num_prompts: p.num_prompts, max_concurrency: Number(p.max_concurrency),
    cache_ratio: p.workload_type === "agentic_replay" ? 0 : p.cache_ratio, num_sessions: p.num_sessions,
    seed: p.seed, ignore_eos: p.workload_type === "synthetic" && p.ignore_eos,
    ...(p.scenario && p.workload_type === "agentic_replay" ? { scenario: p.scenario } : {}),
    ...(p.request_rate !== "" ? { request_rate: Number(p.request_rate) } : {}), ...(p.goodput ? { goodput: p.goodput } : {}) };
}

/** Manual URL runs use server defaults for settings absent from the form, including on replay. */
export function endpointWorkloadParams(p: PerfParams): Record<string, unknown> {
  const params = workloadParams(p);
  for (const key of ["seed", "ignore_eos", "goodput"]) delete params[key];
  return params;
}

export function restoreWorkload(params: Record<string, unknown>): PerfParams {
  const p = { ...DEFAULT_PERF_PARAMS };
  for (const key of Object.keys(p) as (keyof PerfParams)[]) {
    if (params[key] !== undefined) Object.assign(p, { [key]: params[key] });
  }
  p.cache_ratio = params.experiment === "prefix_cache" ? Number(params.prefix_len) / Number(params.random_input_len) * 100 : Number(params.cache_ratio ?? 0);
  return p;
}
