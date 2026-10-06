import type { EngineArgs, ModelDeployment, PdRoleOverride } from "@/types";
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

export type RoleBase = {
  gpu_count: number;
  gpu_type: string | null;
  cpu_request: string | null;
  cpu_limit: string | null;
  memory_request: string | null;
  memory_limit: string | null;
  engine_args: EngineArgs | null;
  vllm_extra_args: string[] | null;
  env: Record<string, string> | null;
};

/**
 * The pool's complete, effective config: the recipe base with the role's
 * values on top (role wins, extra args appended, env merged) — the same rule
 * the backend renders with. Fills the form tabs and the detail cards.
 */
export function effectiveRole(base: RoleBase, role: PdRoleOverride | null | undefined): PdRoleOverride {
  const r = role ?? {};
  const extra = [...(base.vllm_extra_args ?? []), ...(r.vllm_extra_args ?? [])];
  const env = { ...(base.env ?? {}), ...(r.env ?? {}) };
  const args = { ...(base.engine_args ?? {}), ...(r.engine_args ?? {}) };
  return {
    replicas: r.replicas ?? 1,
    gpu_count: r.gpu_count ?? base.gpu_count,
    gpu_type: r.gpu_type ?? base.gpu_type,
    cpu_request: r.cpu_request ?? base.cpu_request,
    cpu_limit: r.cpu_limit ?? base.cpu_limit,
    memory_request: r.memory_request ?? base.memory_request,
    memory_limit: r.memory_limit ?? base.memory_limit,
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
