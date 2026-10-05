import type { ModelDeployment, PdRoleOverride } from "@/types";
import { engineArgsToFlags } from "@/lib/serving-engines";

export type PdRole = "prefill" | "decode";
export const PD_ROLES: PdRole[] = ["prefill", "decode"];

/** vLLM listen port per pool (the decode pool's sidecar owns 8000). Mirrors backend pd_serving. */
export const PD_VLLM_PORT: Record<PdRole, number> = { prefill: 8000, decode: 8200 };
export const DEFAULT_NIXL_PORT = 5600;

/** The pool's effective override block (always an object). */
export function roleOverride(dep: Pick<ModelDeployment, "pd_config">, role: PdRole): PdRoleOverride {
  return dep.pd_config?.[role] ?? {};
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
