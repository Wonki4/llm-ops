// Perf metrics with direction. "higher" = bigger is better, "lower" = smaller is better.
export type Direction = "higher" | "lower";
export type MetricSpec = { key: string; path: (string | number)[]; direction: Direction };

// Keys emitted by `vllm bench serve` result.json (throughput in req|tok/s,
// latencies in ms). Flat schema — one path segment each.
export const PERF_METRICS: MetricSpec[] = [
  { key: "request_throughput", path: ["request_throughput"], direction: "higher" },
  { key: "output_throughput", path: ["output_throughput"], direction: "higher" },
  { key: "total_token_throughput", path: ["total_token_throughput"], direction: "higher" },
  { key: "completed", path: ["completed"], direction: "higher" },
  { key: "total_output_tokens", path: ["total_output_tokens"], direction: "higher" },
  { key: "duration", path: ["duration"], direction: "lower" },
  { key: "mean_ttft_ms", path: ["mean_ttft_ms"], direction: "lower" },
  { key: "median_ttft_ms", path: ["median_ttft_ms"], direction: "lower" },
  { key: "p99_ttft_ms", path: ["p99_ttft_ms"], direction: "lower" },
  { key: "mean_tpot_ms", path: ["mean_tpot_ms"], direction: "lower" },
  { key: "median_tpot_ms", path: ["median_tpot_ms"], direction: "lower" },
  { key: "p99_tpot_ms", path: ["p99_tpot_ms"], direction: "lower" },
  { key: "mean_itl_ms", path: ["mean_itl_ms"], direction: "lower" },
  { key: "p99_itl_ms", path: ["p99_itl_ms"], direction: "lower" },
  { key: "mean_e2el_ms", path: ["mean_e2el_ms"], direction: "lower" },
  { key: "p99_e2el_ms", path: ["p99_e2el_ms"], direction: "lower" },
];

export function getAt(obj: unknown, path: (string | number)[]): unknown {
  let cur: unknown = obj;
  for (const k of path) {
    if (cur == null || typeof cur !== "object") return undefined;
    cur = (cur as Record<string, unknown>)[k as string];
  }
  return cur;
}

export function fmt(value: unknown): string {
  if (value == null) return "-";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return String(value);
    if (Number.isInteger(value)) return value.toString();
    return value.toFixed(4);
  }
  return String(value);
}

export function pickBestWorst(
  values: (number | null)[],
  direction: Direction,
): { bestIdx: number | null; worstIdx: number | null } {
  const nums = values
    .map((v, i) => ({ v, i }))
    .filter((x): x is { v: number; i: number } => x.v !== null && Number.isFinite(x.v));
  if (nums.length < 2) return { bestIdx: null, worstIdx: null };
  const best = nums.reduce((acc, x) =>
    direction === "higher" ? (x.v > acc.v ? x : acc) : x.v < acc.v ? x : acc,
  );
  const worst = nums.reduce((acc, x) =>
    direction === "higher" ? (x.v < acc.v ? x : acc) : x.v > acc.v ? x : acc,
  );
  if (best.i === worst.i) return { bestIdx: null, worstIdx: null };
  return { bestIdx: best.i, worstIdx: worst.i };
}
