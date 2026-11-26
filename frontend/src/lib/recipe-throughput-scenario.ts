import type { SavedRecipeThroughput, ThroughputScenario } from "@/types";

export type CacheCondition = "warm" | "cold" | "unknown";

export function throughputForCondition(result: SavedRecipeThroughput, preference: CacheCondition) {
  const scenarios = result.method === "roofline" && result.status === "estimated" ? result.cache_scenarios : undefined;
  if (!scenarios?.cold) return { rates: result, condition: null, canChoose: false, scenario: null };
  const canChoose = (scenarios.warm?.reused_input_tokens ?? 0) > 0;
  const condition = canChoose ? preference : "cold";
  const scenario = condition === "unknown" ? null : scenarios[condition];
  return { rates: scenario ?? result, condition, canChoose, scenario };
}

export const throughputFields = ["input_tokens", "output_tokens", "concurrency", "shared_prefix_percent"] as const;

export const throughputLimits: Record<keyof ThroughputScenario, [number, number]> = {
  input_tokens: [1, 10_000_000],
  output_tokens: [1, 1_000_000],
  concurrency: [1, 1_000_000],
  shared_prefix_percent: [0, 100],
};

export const defaultThroughputInputs = {
  input_tokens: "100000",
  output_tokens: "1000",
  concurrency: "8",
  shared_prefix_percent: "90",
};

export function throughputFieldValue(key: keyof ThroughputScenario, raw: string): number | null {
  const value = Number(raw);
  const [min, max] = throughputLimits[key];
  if (!/^\d+$/.test(raw) || !Number.isSafeInteger(value) || value < min || value > max) return null;
  return value;
}

export function parseThroughputInputs(inputs: typeof defaultThroughputInputs): ThroughputScenario | null {
  const scenario = {} as ThroughputScenario;
  for (const field of throughputFields) {
    const value = throughputFieldValue(field, inputs[field]);
    if (value === null) return null;
    scenario[field] = value;
  }
  return scenario;
}
