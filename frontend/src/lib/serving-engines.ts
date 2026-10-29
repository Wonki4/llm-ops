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

/** Structured flags a new recipe starts with (also re-applied when switching engine). */
export const DEFAULT_ENGINE_ARGS: Record<ServingEngine, EngineArgs | null> = {
  vllm: { "gpu-memory-utilization": 0.9, "enable-auto-tool-choice": true, "enable-prefix-caching": true },
  sglang: null,
};

/**
 * Free-text extra-args lines a new recipe starts with (swapped for the new engine's when switching engine).
 * One line is one argv element, so a valued flag is `--flag=value`. SGLang already gets `--host 0.0.0.0` from the portal.
 */
export const DEFAULT_EXTRA_ARGS: Record<ServingEngine, string[]> = {
  vllm: ["--host=0.0.0.0", "--kv-cache-metrics", "--enable-prompt-tokens-details"],
  sglang: [],
};

/**
 * Env a new recipe starts with: a fixed hash seed keeps prefix-cache block hashes stable across pods;
 * POD_IP is the pod's own IP (`fieldRef:<path>` renders as valueFrom.fieldRef).
 */
export const DEFAULT_ENV: Record<string, string> = { PYTHONHASHSEED: "0", POD_IP: "fieldRef:status.podIP" };

/**
 * vLLM refuses to start with --enable-auto-tool-choice but no --tool-call-parser.
 * True when a pool would hit that (the parser may also come from the free-text extra args).
 */
export function missingToolCallParser(args: EngineArgs | null | undefined, extraArgs: string[] | null | undefined): boolean {
  if (args?.["enable-auto-tool-choice"] !== true) return false;
  if (String(args["tool-call-parser"] ?? "").trim()) return false;
  return !(extraArgs ?? []).some((a) => a === "--tool-call-parser" || a.startsWith("--tool-call-parser="));
}

export type EngineArgField =
  | { key: string; type: "int"; min?: number; placeholder?: string }
  | { key: string; type: "float"; step: number; min?: number; max?: number; placeholder?: string }
  | { key: string; type: "text"; placeholder?: string }
  | { key: string; type: "bool" }
  | { key: string; type: "select"; options: string[] }; // "" option = unset

export const ENGINE_ARG_FIELDS: Record<ServingEngine, EngineArgField[]> = {
  vllm: [
    { key: "tensor-parallel-size", type: "int", min: 1, placeholder: "1" },
    { key: "max-model-len", type: "int", min: 1, placeholder: "8192" },
    { key: "gpu-memory-utilization", type: "float", step: 0.01, min: 0, max: 1, placeholder: "0.9" },
    { key: "max-num-seqs", type: "int", min: 1, placeholder: "256" },
    { key: "max-num-batched-tokens", type: "int", min: 1, placeholder: "8192" },
    { key: "served-model-name", type: "text", placeholder: "my-model" },
    { key: "reasoning-parser", type: "text" },
    { key: "tool-call-parser", type: "text" },
    { key: "enable-auto-tool-choice", type: "bool" },
    { key: "enable-prefix-caching", type: "bool" },
    { key: "enable-chunked-prefill", type: "bool" },
    { key: "trust-remote-code", type: "bool" },
  ],
  sglang: [
    { key: "tp-size", type: "int", min: 1, placeholder: "1" },
    { key: "dp-size", type: "int", min: 1, placeholder: "1" },
    { key: "context-length", type: "int", min: 1, placeholder: "8192" },
    { key: "mem-fraction-static", type: "float", step: 0.01, min: 0, max: 1, placeholder: "0.88" },
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
