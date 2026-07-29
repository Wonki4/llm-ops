// frontend/src/lib/bench-command.ts

/**
 * Render the `vllm bench serve` command a performance run will execute, for a
 * live read-only preview. Mirrors backend `_vllm_bench_argv`; the backend
 * remains authoritative — this is presentational. Values come from the form's
 * editable workload fields (not a fixed preset), so the preview matches exactly
 * what runs. Target-derived flags (base-url/model/tokenizer/result-*) fall back
 * to placeholders when unknown.
 */
export interface BenchCommandParams {
  random_input_len: number;
  random_output_len: number;
  num_prompts: number;
  max_concurrency?: number | string;
  request_rate?: number | string;
  seed?: number;
  ignore_eos?: boolean;
  tokenizer?: string;
  goodput?: string;
}

export function buildBenchCommand(
  p: BenchCommandParams,
  extraFlags: string,
  opts: { model?: string; baseUrl?: string } = {},
): string {
  const model = opts.model?.trim() || "<model>";
  const tokenizer = p.tokenizer?.trim() || model;
  const baseUrl = opts.baseUrl?.trim() || "<target>";
  const parts = [
    "vllm bench serve",
    "--backend openai-chat",
    `--base-url ${baseUrl}`,
    "--endpoint /v1/chat/completions",
    `--model ${model}`,
    `--tokenizer ${tokenizer}`,
    "--dataset-name random",
    `--random-input-len ${p.random_input_len}`,
    `--random-output-len ${p.random_output_len}`,
    `--num-prompts ${p.num_prompts}`,
    "--percentile-metrics ttft,tpot,itl,e2el",
    "--metric-percentiles 90,99",
    `--seed ${p.seed ?? 0}`,
    "--save-result --result-dir /tmp --result-filename r.json",
  ];
  // Optional flags, in the same order the backend emits them.
  const requestRate = `${p.request_rate ?? ""}`.trim();
  if (requestRate !== "") parts.push(`--request-rate ${requestRate}`);
  const maxConcurrency = `${p.max_concurrency ?? ""}`.trim();
  if (maxConcurrency !== "") parts.push(`--max-concurrency ${maxConcurrency}`);
  const goodput = (p.goodput ?? "").trim();
  if (goodput !== "") parts.push(`--goodput ${goodput}`);
  if (p.ignore_eos) parts.push("--ignore-eos");

  const extra = extraFlags.trim();
  if (extra) parts.push(extra);
  return parts.join(" \\\n  ");
}
