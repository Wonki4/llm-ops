import type { ModelDeployment, PdConfig, PdExtras, PdFramework, PdRoleOverride, PoolConfig, SidecarSpec } from "@/types";
import { engineArgsToFlags } from "@/lib/serving-engines";

export type PdRole = "prefill" | "decode";
export const PD_ROLES: PdRole[] = ["prefill", "decode"];

/** vLLM listen port per pool (the decode pool's sidecar owns 8000). Mirrors backend pd_serving. */
export const PD_VLLM_PORT: Record<PdRole, number> = { prefill: 8000, decode: 8200 };
export const DEFAULT_NIXL_PORT = 5600;
/** NIXL side-channel port each pool's P/D settings start with (the shared DEFAULT_NIXL_PORT is for pools without settings). */
export const PD_NIXL_PORT: Record<PdRole, number> = { prefill: 5557, decode: 5558 };
/**
 * Default `llm-d.ai/guide` value. `{{model_name}}` becomes the model name entered at
 * deploy time (K8s-safe), so both pools share it — it is not the recipe's name.
 */
export const PD_GUIDE_LABEL = "{{model_name}}";
/** P/D frameworks the portal can deploy with; the backend's PdFramework literal must list the same values. */
export const PD_FRAMEWORKS: { value: PdFramework; label: string }[] = [{ value: "llm-d", label: "llm-d" }];
export const DEFAULT_PD_FRAMEWORK: PdFramework = "llm-d";

/**
 * What a pool's "P/D settings" start with when a recipe switches to P/D: the
 * plumbing the portal used to inject (--port, --kv-transfer-config, the role
 * label, the NIXL side channel) plus a label grouping both pools and the RDMA resource.
 * Only seeded in the form; a pool without settings keeps the portal-injected
 * plumbing, so existing P/D deployments render unchanged.
 */
/** The routing sidecar's default args (mirrors backend pd_serving.SIDECAR_ARGS). */
export const DEFAULT_SIDECAR_ARGS = ["--port=8000", "--kv-connector=nixlv2", "--zap-log-level=1", "--secure-proxy=false"];

/** What the decode pool's sidecar settings start with; the image stays blank (portal default) unless the recipe set one. */
export function defaultSidecar(pd: PdConfig): SidecarSpec {
  return { image: pd.sidecar_image ?? null, args: [...DEFAULT_SIDECAR_ARGS], env: {}, requests: {}, limits: {} };
}

/**
 * Another pool's P/D settings re-pointed at `role` (copying a pool): the role
 * label, --port, the kv_role inside --kv-transfer-config and a default NIXL
 * port follow the target.
 */
export function retargetPdExtras(ex: PdExtras, role: PdRole): PdExtras {
  const kvRole = role === "prefill" ? "kv_producer" : "kv_consumer";
  const args = (ex.args ?? []).map((a) => {
    if (a.startsWith("--port=")) return `--port=${PD_VLLM_PORT[role]}`;
    if (a.startsWith("--kv-transfer-config=")) {
      try {
        const kv = JSON.parse(a.slice("--kv-transfer-config=".length));
        return `--kv-transfer-config=${JSON.stringify({ ...kv, kv_role: kvRole })}`;
      } catch {
        return a; // invalid JSON: leave it for the save-time error
      }
    }
    return a;
  });
  // The other pool's default NIXL port follows to this pool's default; a custom port is kept.
  const other: PdRole = role === "prefill" ? "decode" : "prefill";
  const env = { ...(ex.env ?? {}) };
  if (env.VLLM_NIXL_SIDE_CHANNEL_PORT === String(PD_NIXL_PORT[other])) env.VLLM_NIXL_SIDE_CHANNEL_PORT = String(PD_NIXL_PORT[role]);
  return { ...ex, env, args, labels: { ...(ex.labels ?? {}), "llm-d.ai/role": role } };
}

export function defaultPdExtras(role: PdRole, pd: PdConfig): PdExtras {
  const kv = {
    kv_connector: "NixlConnector",
    kv_role: role === "prefill" ? "kv_producer" : "kv_consumer",
    kv_load_failure_policy: "fail",
    ...(pd.kv_transfer_extra ?? {}),
  };
  return {
    env: {
      // `fieldRef:<path>` renders as valueFrom.fieldRef (the pod's own IP here).
      VLLM_NIXL_SIDE_CHANNEL_HOST: "fieldRef:status.podIP",
      VLLM_NIXL_SIDE_CHANNEL_PORT: String(PD_NIXL_PORT[role]),
      VLLM_ENGINE_READY_TIMEOUT_S: "3600",
    },
    args: [`--port=${PD_VLLM_PORT[role]}`, `--kv-transfer-config=${JSON.stringify(kv)}`],
    // llm-d.ai/guide groups both pools of the serving (same value on prefill and decode).
    labels: { "llm-d.ai/role": role, "llm-d.ai/guide": PD_GUIDE_LABEL },
    annotations: {},
    resources: { "rdma/ib": "1" },
  };
}

/** The pool's stored config block (always an object). */
export function roleOverride(dep: Pick<ModelDeployment, "pd_config">, role: PdRole): PdRoleOverride {
  return dep.pd_config?.[role] ?? {};
}

export const POOL_KEYS: (keyof PoolConfig)[] = [
  "image", "model_path", "gpu_count", "gpu_type", "gpu_resource_key",
  "cpu_request", "cpu_limit", "memory_request", "memory_limit", "storages",
  "engine_args", "vllm_extra_args", "env", "probes", "runtime", "node_selector", "tolerations",
];

export const EMPTY_POOL: PoolConfig = {
  image: "", model_path: "", gpu_count: 1, gpu_type: null, gpu_resource_key: "nvidia.com/gpu",
  cpu_request: null, cpu_limit: null, memory_request: null, memory_limit: null, storages: null,
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
    pd_extras: r.pd_extras ?? null,
    sidecar: r.sidecar ?? null,
    image: pick("image"),
    model_path: pick("model_path"),
    gpu_count: pick("gpu_count"),
    gpu_type: pick("gpu_type"),
    gpu_resource_key: pick("gpu_resource_key"),
    cpu_request: pick("cpu_request"),
    cpu_limit: pick("cpu_limit"),
    memory_request: pick("memory_request"),
    memory_limit: pick("memory_limit"),
    storages: r.storages ?? base.storages ?? [],
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
  const engineFlags = engineArgsToFlags({ ...(dep.engine_args ?? {}), ...(ov.engine_args ?? {}) });
  const extra = [...(dep.vllm_extra_args ?? []), ...(ov.vllm_extra_args ?? []), ...(ov.pd_extras?.args ?? [])];
  // A pool with P/D settings carries --port / --kv-transfer-config in them.
  if (ov.pd_extras) return ["--model", dep.model_path, ...engineFlags, ...extra];
  const kv = {
    kv_connector: "NixlConnector",
    kv_role: role === "prefill" ? "kv_producer" : "kv_consumer",
    ...(dep.pd_config?.kv_transfer_extra ?? {}),
  };
  return [
    "--model", dep.model_path,
    "--port", String(PD_VLLM_PORT[role]),
    "--kv-transfer-config", JSON.stringify(kv),
    ...engineFlags,
    ...extra,
  ];
}
