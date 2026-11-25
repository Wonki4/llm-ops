import type { CapacityScenario, RecipeCapacity } from "@/types";

export const defaultCapacityScenario: CapacityScenario = { context_tokens: 32768, requests: 1, shared_prefix_tokens: 0 };

/** Use the effective decode pool limit on first view. Subsequent requests honor the user's scenario. */
export function initialDSparkScenario(data?: RecipeCapacity): CapacityScenario | null {
  const pool = data?.pools.find(item => item.role === "decode" || item.role === "aggregated");
  const limit = pool?.speculation?.max_num_seqs ?? pool?.integrated_speculation?.max_num_seqs;
  if (!limit || !Number.isSafeInteger(limit) || limit <= 1 || limit > 1_000_000) return null;
  return { ...defaultCapacityScenario, requests: limit };
}
