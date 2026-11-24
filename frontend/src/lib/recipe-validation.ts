import type { BenchmarkRun, ServingRecipeInput } from "@/types";

export const VALIDATIONS = "/admin/benchmarks/validations";
export const VALIDATION_API = "/api/admin/recipe-validations";
export const validationTerminal = (status: string) => ["succeeded", "failed", "cancelled"].includes(status);
export interface ValidationAssessment {
  passed: boolean; errors: string[]; metrics: Record<string, number>; measurement_ids: string[];
  quality: { task: string; metric: string; value: number | null; minimum: number; passed: boolean }[];
}
export interface ValidationSnapshot {
  recipe: ServingRecipeInput; hypothesis?: string | null; gpu_count: number; manifests: unknown[];
  checks: { kind: string; status: string; message: string }[];
}
export interface ValidationAttempt {
  id: string; number: number; purpose: string; status: string; stage_started_at: string;
  started_at: string; finished_at: string | null; deployment_id: string | null;
  diagnostics: Record<string, unknown> | null;
  analysis: { explanation: string; proposed_patch: Record<string, unknown>; author: string } | null;
  error_code: string | null; error_message: string | null; benchmarks: BenchmarkRun[];
  state: { startup_seconds?: number; outcome?: string; assessment?: ValidationAssessment; publication?: { model_id: string; latency_ms: number }; assets?: unknown; effective_manifests?: unknown };
}
export interface ValidationCandidate {
  id: string; name: string; weights: string; gpu_count: number; position: number; status: string;
  recipe_id: string | null; parent_candidate_id: string | null;
  snapshot_hash: string; snapshot: ValidationSnapshot; assessment: ValidationAssessment | null;
  attempts: ValidationAttempt[];
}
export interface RecipeSearch {
  target_model: string; alternative_models: string[]; max_candidates: number; max_search_seconds: number;
  patience: number; min_improvement_percent: number; min_context_length: number;
}
export interface ValidationConditions {
  version?: number;
  search?: RecipeSearch | null;
  ingress_host: string; ingress_class: string; gpu_limit: number; gpu_type: string | null;
  time_limits: { assets: number; scheduling: number; startup: number; measurement: number; total: number };
  capacity_search?: { max_rate: number; refinements?: number; max_lag_ms?: number } | null;
  performance: { slo?: import("@/types").ServiceObjectives; workload_version?: number; workload_type?: string; cache_ratio?: number; num_sessions?: number; scenario?: Record<string, unknown>; random_input_len: number; random_output_len: number; num_prompts: number; max_concurrency: number; seed: number; ignore_eos: boolean; request_rate?: number };
  repeats: number; prefix_len: number | null; accuracy: Record<string, unknown> | null; accuracy_image: string | null;
  quality_gates: { task: string; metric: string; minimum: number }[];
  max_ttft_p99_ms: number | null; min_output_throughput: number | null;
  selection_metric: string; tokenizer: string | null; airgap: boolean;
}
export interface RecipeValidation {
  id: string; name: string; model_name: string; cluster_id: string | null; namespace: string;
  status: string; phase: string; message: string | null; created_by: string;
  created_at: string; started_at: string | null; finished_at: string | null;
  conditions: ValidationConditions; selected_candidate_id: string | null;
  candidate_count?: number; completed_count?: number; selected_candidate?: string | null;
  search_state?: { closed_reason?: string; explanation?: string; saved_recipe_id?: string } | null;
  search_progress?: { candidate_count: number; remaining_candidates: number; remaining_search_seconds: number;
    stale_candidates: number; best_candidate_id: string | null; best_value: number | null;
    stop_reason: string | null; closed: boolean; next_action: string } | null;
  candidates: ValidationCandidate[];
}
export interface ValidationCreate {
  request_key: string; name: string; model_name: string; cluster_id: string | null; namespace: string;
  candidates: { recipe_id?: string; recipe?: ServingRecipeInput }[]; conditions: ValidationConditions;
}
export interface ValidationPreview {
  namespace: string; cluster_id: string | null; max_gpu_usage: number; candidates: ValidationSnapshot[]; conditions: ValidationConditions;
}

/** RFC 7396 patch relative to the public snapshot; unchanged hidden credentials are preserved. */
export function recipePatch(before: unknown, after: unknown): unknown {
  if (JSON.stringify(before) === JSON.stringify(after)) return undefined;
  if (!after || typeof after !== "object" || Array.isArray(after)) return after;
  const left = before && typeof before === "object" ? before as Record<string, unknown> : {};
  const right = after as Record<string, unknown>;
  const patch: Record<string, unknown> = {};
  for (const key of new Set([...Object.keys(left), ...Object.keys(right)])) {
    const value = key in right ? recipePatch(left[key], right[key]) : null;
    if (value !== undefined) patch[key] = value;
  }
  return patch;
}
