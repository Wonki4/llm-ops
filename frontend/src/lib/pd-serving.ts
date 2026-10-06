import type { ModelDeployment, PdRoleOverride, PoolConfig } from "@/types";
import { engineArgsToFlags } from "@/lib/serving-engines";

export type PdRole = "prefill" | "decode";
export const PD_ROLES: PdRole[] = ["prefill", "decode"];

/** vLLM listen port per pool (the decode pool's sidecar owns 8000). Mirrors backend pd_serving. */
export const PD_VLLM_PORT: Record<PdRole, number> = { prefill: 8000, decode: 8200 };
export const DEFAULT_NIXL_PORT = 5600;

/** The pool's stored config block (always an object). */
export function roleOverride(dep: Pick<ModelDeployment, "pd_config">, role: PdRole): PdRoleOverride {
  return dep.pd_config?.[role] ?? {};
}

export const POOL_KEYS: (keyof PoolConfig)[] = [
  "image", "model_path", "gpu_count", "gpu_type", "gpu_resource_key",
  "cpu_request", "cpu_limit", "memory_request", "memory_limit", "pvc_name", "pvc_mount_path",
  "engine_args", "vllm_extra_args", "env", "probes", "runtime", "node_selector", "tolerations",
];

export const EMPTY_POOL: PoolConfig = {
  image: "", model_path: "", gpu_count: 1, gpu_type: null, gpu_resource_key: "nvidia.com/gpu",
  cpu_request: null, cpu_limit: null, memory_request: null, memory_limit: null, pvc_name: null, pvc_mount_path: null,
  engine_args: null, vllm_extra_args: null, env: null, probes: null, runtime: null, node_selector: null, tolerations: null,
};

/** Just the pool fields of a recipe/deployment row. */
export function poolOf(row: PoolConfig): PoolConfig {
  const out = { ...EMPTY_POOL };
  for (const k of POOL_KEYS) (out as Record<string, unknown>)[k] = row[k] ?? EMPTY_POOL[k];
  return out;
}

/**
 * The pool's complete, effective config: the recipe row with the role's
 * values on top (role wins, extra args appended, env merged) — the same rule
 * the backend renders with. Fills the form tabs and the detail cards.
 */
export function effectiveRole(base: PoolConfig, role: PdRoleOverride | null | undefined): PdRoleOverride & PoolConfig {
  const r = role ?? {};
  const extra = [...(base.vllm_extra_args ?? []), ...(r.vllm_extra_args ?? [])];
  const env = { ...(base.env ?? {}), ...(r.env ?? {}) };
  const args = { ...(base.engine_args ?? {}), ...(r.engine_args ?? {}) };
  const pick = <K extends keyof PoolConfig>(k: K): PoolConfig[K] => (r[k] ?? base[k] ?? EMPTY_POOL[k]) as PoolConfig[K];
  return {
    replicas: r.replicas ?? 1,
    image: pick("image"),
    model_path: pick("model_path"),
    gpu_count: pick("gpu_count"),
    gpu_type: pick("gpu_type"),
    gpu_resource_key: pick("gpu_resource_key"),
    cpu_request: pick("cpu_request"),
    cpu_limit: pick("cpu_limit"),
    memory_request: pick("memory_request"),
    memory_limit: pick("memory_limit"),
    pvc_name: pick("pvc_name"),
    pvc_mount_path: pick("pvc_mount_path"),
    probes: pick("probes"),
    runtime: pick("runtime"),
    node_selector: pick("node_selector"),
    tolerations: pick("tolerations"),
    engine_args: Object.keys(args).length ? args : null,
    vllm_extra_args: extra.length ? extra : null,
    env: Object.keys(env).length ? env : null,
  };
}

/**
 * The launch line a pool's vLLM container gets, rendered the way the backend
 * does it: model, port, the portal-owned KV transfer config, the recipe's
 * engine args with the pool's overrides on top, then both extra-arg lists.
 */
export function roleLaunchArgs(
  dep: Pick<ModelDeployment, "model_path" | "engine_args" | "vllm_extra_args" | "pd_config">,
  role: PdRole,
): string[] {
  const ov = roleOverride(dep, role);
  const kv = {
    kv_connector: "NixlConnector",
    kv_role: role === "prefill" ? "kv_producer" : "kv_consumer",
    ...(dep.pd_config?.kv_transfer_extra ?? {}),
  };
  return [
    "--model", dep.model_path,
    "--port", String(PD_VLLM_PORT[role]),
    "--kv-transfer-config", JSON.stringify(kv),
    ...engineArgsToFlags({ ...(dep.engine_args ?? {}), ...(ov.engine_args ?? {}) }),
    ...(dep.vllm_extra_args ?? []),
    ...(ov.vllm_extra_args ?? []),
  ];
}
