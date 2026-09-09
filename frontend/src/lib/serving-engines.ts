import type { EngineArgs, ServingEngine } from "@/types";

/**
 * Curated "main" launch flags per engine, shown as dedicated fields in the
 * recipe form. `key` is the bare CLI flag name; the backend renders
 * `{key: value}` generically (`--key value`, `--key` for true), so adding a
 * field here needs no server change.
 */
export const ENGINES: ServingEngine[] = ["vllm", "sglang"];

export const ENGINE_DEFAULT_IMAGE: Record<ServingEngine, string> = {
  vllm: "vllm/vllm-openai:latest",
  sglang: "lmsysorg/sglang:latest",
};

export const ENGINE_LABEL_KEY: Record<ServingEngine, "engineVllm" | "engineSglang"> = {
  vllm: "engineVllm",
  sglang: "engineSglang",
};

export type EngineArgField =
  | { key: string; type: "int"; min?: number; placeholder?: string }
  | { key: string; type: "float"; step: number; min?: number; max?: number; placeholder?: string }
  | { key: string; type: "text"; placeholder?: string }
  | { key: string; type: "bool" }
  | { key: string; type: "select"; options: string[] }; // "" option = unset

const DTYPES = ["", "auto", "bfloat16", "float16", "float32"];

export const ENGINE_ARG_FIELDS: Record<ServingEngine, EngineArgField[]> = {
  vllm: [
    { key: "tensor-parallel-size", type: "int", min: 1, placeholder: "1" },
    { key: "pipeline-parallel-size", type: "int", min: 1, placeholder: "1" },
    { key: "max-model-len", type: "int", min: 1, placeholder: "8192" },
    { key: "gpu-memory-utilization", type: "float", step: 0.01, min: 0, max: 1, placeholder: "0.9" },
    { key: "dtype", type: "select", options: DTYPES },
    { key: "quantization", type: "select", options: ["", "fp8", "awq", "gptq", "gptq_marlin", "bitsandbytes"] },
    { key: "kv-cache-dtype", type: "select", options: ["", "auto", "fp8"] },
    { key: "max-num-seqs", type: "int", min: 1, placeholder: "256" },
    { key: "max-num-batched-tokens", type: "int", min: 1, placeholder: "8192" },
    { key: "served-model-name", type: "text", placeholder: "my-model" },
    { key: "enable-prefix-caching", type: "bool" },
    { key: "enable-chunked-prefill", type: "bool" },
    { key: "trust-remote-code", type: "bool" },
  ],
  sglang: [
    { key: "tp-size", type: "int", min: 1, placeholder: "1" },
    { key: "dp-size", type: "int", min: 1, placeholder: "1" },
    { key: "context-length", type: "int", min: 1, placeholder: "8192" },
    { key: "mem-fraction-static", type: "float", step: 0.01, min: 0, max: 1, placeholder: "0.88" },
    { key: "dtype", type: "select", options: DTYPES },
    { key: "quantization", type: "select", options: ["", "fp8", "awq", "gptq", "bitsandbytes"] },
    { key: "kv-cache-dtype", type: "select", options: ["", "auto", "fp8_e5m2", "fp8_e4m3"] },
    { key: "max-running-requests", type: "int", min: 1, placeholder: "256" },
    { key: "chunked-prefill-size", type: "int", min: 1, placeholder: "8192" },
    { key: "served-model-name", type: "text", placeholder: "my-model" },
    { key: "disable-radix-cache", type: "bool" },
    { key: "trust-remote-code", type: "bool" },
    { key: "enable-torch-compile", type: "bool" },
  ],
};

/** i18n key for a flag's label: `tp-size` → `arg.tpSize`. */
export function argLabelKey(key: string): string {
  return "arg." + key.replace(/-([a-z0-9])/g, (_, c: string) => c.toUpperCase());
}

/** Mirror of the backend renderer, for display only (sorted keys; true → bare flag). */
export function engineArgsToFlags(args: EngineArgs | null | undefined): string[] {
  const out: string[] = [];
  for (const key of Object.keys(args ?? {}).sort()) {
    const value = (args as EngineArgs)[key];
    if (value === false || value === "" || value == null) continue;
    if (value === true) out.push(`--${key}`);
    else out.push(`--${key}`, String(value));
  }
  return out;
}
