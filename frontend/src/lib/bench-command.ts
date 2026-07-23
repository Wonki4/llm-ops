// frontend/src/lib/bench-command.ts
import type { LoadPreset } from "@/types";

/**
 * Render the `vllm bench serve` command a performance run will execute, for a
 * live read-only preview. Mirrors backend `_vllm_bench_argv`; the backend
 * remains authoritative — this is presentational. Target-derived flags
 * (base-url/model/tokenizer/result-*) are shown as placeholders when unknown.
 */
export function buildBenchCommand(
  preset: LoadPreset,
  extraFlags: string,
  opts: { model?: string } = {},
): string {
  const model = opts.model?.trim() || "<model>";
  const parts = [
    "vllm bench serve",
    "--backend openai-chat",
    "--base-url <target>",
    "--endpoint /v1/chat/completions",
    `--model ${model}`,
    `--tokenizer ${model}`,
    "--dataset-name random",
    `--random-input-len ${preset.random_input_len}`,
    `--random-output-len ${preset.random_output_len}`,
    `--num-prompts ${preset.num_prompts}`,
    "--percentile-metrics ttft,tpot,itl,e2el",
    "--metric-percentiles 90,99",
    "--seed 0",
    "--save-result --result-dir /tmp --result-filename r.json",
    `--max-concurrency ${preset.max_concurrency}`,
    "--ignore-eos",
  ];
  const extra = extraFlags.trim();
  if (extra) parts.push(extra);
  return parts.join(" \\\n  ");
}
