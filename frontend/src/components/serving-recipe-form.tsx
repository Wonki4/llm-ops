"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, Loader2, ScrollText } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import { useCreateServingRecipe, useGpuTypes, useUpdateServingRecipe } from "@/hooks/use-api";
import { linesToList, linesToMap, linesToTolerations, listToLines, mapToLines, tolerationsToLines } from "@/lib/placement";
import type {
  PdConfig, PdRoleOverride, ProbeSpec, ProbesSpec, RuntimeOptions, ServingEngine, ServingMode, ServingRecipe, ServingRecipeInput,
} from "@/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ENGINES, ENGINE_DEFAULT_IMAGE, ENGINE_LABEL_KEY } from "@/lib/serving-engines";
import { Area, EngineArgsFields, Field, withEngineArg } from "@/components/engine-args-fields";
import { PdRoleCard } from "@/components/pd-role-card";
import { DEFAULT_NIXL_PORT } from "@/lib/pd-serving";

export const RECIPES_HREF = "/admin/recipes";

const BLANK: ServingRecipeInput = {
  name: "", description: null, model_path: "", image: "", gpu_count: 1,
  gpu_resource_key: "nvidia.com/gpu", cpu_request: null, cpu_limit: null,
  memory_request: null, memory_limit: null, node_selector: null, tolerations: null,
  pvc_name: null, pvc_mount_path: null, vllm_extra_args: null, env: null,
  engine: "vllm", engine_args: null, probes: null, gpu_type: null,
  serving_mode: "aggregated", pd_config: null, runtime: null,
};

const PD_BLANK: PdConfig = { prefill: { replicas: 1 }, decode: { replicas: 1 } };
const MODES: ServingMode[] = ["aggregated", "pd"];
const MODE_LABEL_KEY: Record<ServingMode, "modeAggregated" | "modePd"> = { aggregated: "modeAggregated", pd: "modePd" };

function toInput(r: ServingRecipe): ServingRecipeInput {
  const { id, created_by, updated_by, created_at, updated_at, ...rest } = r;
  void id; void created_by; void updated_by; void created_at; void updated_at;
  return rest;
}

type RequiredText = "name" | "image" | "model_path" | "gpu_resource_key";
type OptionalText =
  | "description" | "cpu_request" | "cpu_limit" | "memory_request" | "memory_limit"
  | "pvc_name" | "pvc_mount_path";
type OptionalProbe = "liveness" | "startup";

/** Page chrome shared by the create and edit routes. */
export function RecipePageHeader({ title, description }: { title: string; description?: string }) {
  const t = useTranslations("servingRecipes");
  return (
    <div>
      <Link href={RECIPES_HREF} className="text-sm text-muted-foreground hover:text-foreground inline-flex items-center gap-1">
        <ArrowLeft className="size-3.5" />{t("backToList")}
      </Link>
      <h1 className="text-2xl font-bold mt-2 flex items-center gap-2"><ScrollText className="size-5" />{title}</h1>
      {description && <p className="text-muted-foreground mt-1">{description}</p>}
    </div>
  );
}

/**
 * Full-page serving recipe form. Creates when `recipe` is absent, updates
 * otherwise. Navigates back to the list on success.
 *
 * `initial` pre-fills a new recipe (captured from a deployment); the caller
 * remounts with a `key` when it changes. `sourceDeploymentId` is sent on
 * create so the backend can point that deployment at the new recipe.
 */
export function ServingRecipeForm({
  recipe, initial, sourceDeploymentId, children,
}: {
  recipe?: ServingRecipe;
  initial?: ServingRecipeInput;
  sourceDeploymentId?: string | null;
  /** Rendered above the form: import notice, parser warnings. */
  children?: React.ReactNode;
}) {
  const t = useTranslations("servingRecipes");
  const tc = useTranslations("common");
  const router = useRouter();
  const createMut = useCreateServingRecipe();
  const updateMut = useUpdateServingRecipe();
  const { data: gpuTypes } = useGpuTypes();

  // Drafts from the reverse parser predate some fields; BLANK fills the gaps.
  const seed: ServingRecipeInput = { ...BLANK, ...(recipe ? toInput(recipe) : initial ?? {}) };
  const [form, setForm] = useState<ServingRecipeInput>(seed);
  const [argsText, setArgsText] = useState(() => listToLines(seed.vllm_extra_args));
  const [envText, setEnvText] = useState(() => mapToLines(seed.env));
  const [nsText, setNsText] = useState(() => mapToLines(seed.node_selector));
  const [tolText, setTolText] = useState(() => tolerationsToLines(seed.tolerations));
  const [kvExtraText, setKvExtraText] = useState(() => {
    const extra = seed.pd_config?.kv_transfer_extra;
    return extra && Object.keys(extra).length ? JSON.stringify(extra, null, 2) : "";
  });
  const [extraResText, setExtraResText] = useState(() => mapToLines(seed.runtime?.extra_resources ?? null));

  const saving = createMut.isPending || updateMut.isPending;
  const isPd = form.serving_mode === "pd";
  const pd: PdConfig = form.pd_config ?? PD_BLANK;
  const runtime: RuntimeOptions = form.runtime ?? {};
  const gpuTypeNames = (gpuTypes ?? []).map((g) => g.name);

  const text = (k: RequiredText) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));
  const optText = (k: OptionalText) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.value || null }));

  function switchEngine(next: ServingEngine) {
    setForm((f) => {
      if (f.engine === next) return f;
      const otherDefault = ENGINE_DEFAULT_IMAGE[f.engine];
      const image = !f.image.trim() || f.image === otherDefault ? ENGINE_DEFAULT_IMAGE[next] : f.image;
      // Structured args are engine-specific; free-text extra args are the user's and stay.
      return { ...f, engine: next, engine_args: null, image };
    });
  }

  /** P/D needs vLLM: switching to it also switches the engine (and seeds the two pools). */
  function switchMode(next: ServingMode) {
    if (next === "pd") switchEngine("vllm");
    setForm((f) => ({
      ...f,
      serving_mode: next,
      pd_config: next === "pd" ? (f.pd_config ?? PD_BLANK) : f.pd_config,
    }));
  }

  function setPd(patch: Partial<PdConfig>) {
    setForm((f) => ({ ...f, pd_config: { ...(f.pd_config ?? PD_BLANK), ...patch } }));
  }
  function setRole(role: "prefill" | "decode", value: PdRoleOverride) {
    setPd({ [role]: value });
  }
  function setRuntime(patch: Partial<RuntimeOptions>) {
    setForm((f) => ({ ...f, runtime: { ...(f.runtime ?? {}), ...patch } }));
  }

  /** Probe fields: unset (undefined) means "portal default"; liveness/startup null means off. */
  function setProbe(kind: "readiness" | OptionalProbe, key: keyof ProbeSpec, value: string) {
    setForm((f) => {
      const probes: ProbesSpec = { ...(f.probes ?? {}) };
      const spec: ProbeSpec = { ...(probes[kind] ?? {}) };
      if (value === "") delete spec[key];
      else if (key === "path") spec.path = value;
      else spec[key] = Number(value);
      probes[kind] = spec;
      return { ...f, probes };
    });
  }
  function setOptionalProbe(kind: OptionalProbe, enabled: boolean) {
    setForm((f) => ({ ...f, probes: { ...(f.probes ?? {}), [kind]: enabled ? (f.probes?.[kind] ?? {}) : null } }));
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!form.name.trim() || !form.model_path.trim() || !form.image.trim()) {
      toast.error(t("requiredError"));
      return;
    }
    let pdConfig: PdConfig | null = null;
    if (isPd) {
      let kvExtra: Record<string, unknown> | undefined;
      if (kvExtraText.trim()) {
        try {
          const parsed: unknown = JSON.parse(kvExtraText);
          if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("not an object");
          kvExtra = parsed as Record<string, unknown>;
        } catch {
          toast.error(t("pdKvExtraInvalid"));
          return;
        }
      }
      pdConfig = { ...pd, kv_transfer_extra: kvExtra ?? {}, sidecar_image: pd.sidecar_image?.trim() || null };
    }
    const extraResources = linesToMap(extraResText);
    const runtimeOut: RuntimeOptions = { ...runtime, extra_resources: extraResources ?? {} };
    const runtimeEmpty = !runtimeOut.shm_size_gi && !runtimeOut.host_ipc && !runtimeOut.privileged && !extraResources;
    const body: ServingRecipeInput = {
      ...form,
      vllm_extra_args: linesToList(argsText),
      env: linesToMap(envText),
      node_selector: linesToMap(nsText),
      tolerations: linesToTolerations(tolText),
      pd_config: pdConfig,
      runtime: runtimeEmpty ? null : runtimeOut,
    };
    const opts = {
      onSuccess: () => {
        toast.success(recipe ? t("saveSuccess") : t("createSuccess"));
        router.push(RECIPES_HREF);
      },
      onError: (err: unknown) => toast.error(err instanceof Error ? err.message : t("saveError")),
    };
    if (recipe) updateMut.mutate({ id: recipe.id, body }, opts);
    else createMut.mutate({ ...body, source_deployment_id: sourceDeploymentId ?? null }, opts);
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      {children}
      <Card>
        <CardHeader><CardTitle className="text-base">{t("sectionBasic")}</CardTitle></CardHeader>
        <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field id="recipe-mode" label={t("servingMode")} span2 hint={isPd ? t("modePdHint") : undefined}>
            <div id="recipe-mode" role="radiogroup" className="inline-flex rounded-md border p-0.5">
              {MODES.map((m) => (
                <button
                  key={m}
                  type="button"
                  role="radio"
                  aria-checked={form.serving_mode === m}
                  data-testid={`recipe-mode-${m}`}
                  onClick={() => switchMode(m)}
                  className={
                    "rounded px-3 py-1 text-sm transition-colors " +
                    (form.serving_mode === m ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground")
                  }
                >
                  {t(MODE_LABEL_KEY[m])}
                </button>
              ))}
            </div>
          </Field>
          <Field id="recipe-engine" label={t("engine")} span2 hint={isPd ? t("modeEngineLocked") : undefined}>
            <div id="recipe-engine" role="radiogroup" className="inline-flex rounded-md border p-0.5">
              {ENGINES.map((e) => {
                const locked = isPd && e !== "vllm";
                return (
                  <button
                    key={e}
                    type="button"
                    role="radio"
                    aria-checked={form.engine === e}
                    aria-disabled={locked}
                    disabled={locked}
                    onClick={() => switchEngine(e)}
                    className={
                      "rounded px-3 py-1 text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-40 " +
                      (form.engine === e ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground")
                    }
                  >
                    {t(ENGINE_LABEL_KEY[e])}
                  </button>
                );
              })}
            </div>
          </Field>
          <Field id="recipe-name" label={t("name")} required>
            <Input id="recipe-name" value={form.name} onChange={text("name")} />
          </Field>
          <Field id="recipe-image" label={t("image")} required>
            <Input id="recipe-image" className="font-mono" value={form.image} onChange={text("image")} />
          </Field>
          <Field id="recipe-model-path" label={t("modelPath")} required span2>
            <Input id="recipe-model-path" className="font-mono" value={form.model_path} onChange={text("model_path")} />
          </Field>
          <Field id="recipe-description" label={t("description")} span2>
            <Input id="recipe-description" value={form.description ?? ""} onChange={optText("description")} />
          </Field>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">{t("sectionResources")}</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field id="recipe-gpu-count" label={t("gpuCount")}>
              <Input
                id="recipe-gpu-count" type="number" min={0} value={form.gpu_count}
                onChange={(e) => setForm((f) => ({ ...f, gpu_count: Number(e.target.value) }))}
              />
            </Field>
            <Field id="recipe-gpu-type" label={t("gpuType")}>
              <Input
                id="recipe-gpu-type" className="font-mono" list="recipe-gpu-type-options" placeholder="a100-80g"
                value={form.gpu_type ?? ""} onChange={(e) => setForm((f) => ({ ...f, gpu_type: e.target.value.trim() || null }))}
              />
              <datalist id="recipe-gpu-type-options">
                {(gpuTypes ?? []).map((g) => (
                  <option key={g.name} value={g.name}>{g.clusters.map((c) => c.cluster_name).join(", ")}</option>
                ))}
              </datalist>
              <p className="text-xs text-muted-foreground">{t("gpuTypeHint")}</p>
            </Field>
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Field id="recipe-cpu-req" label={t("cpuRequest")}>
              <Input id="recipe-cpu-req" placeholder="4" value={form.cpu_request ?? ""} onChange={optText("cpu_request")} />
            </Field>
            <Field id="recipe-cpu-limit" label={t("cpuLimit")}>
              <Input id="recipe-cpu-limit" placeholder="8" value={form.cpu_limit ?? ""} onChange={optText("cpu_limit")} />
            </Field>
            <Field id="recipe-mem-req" label={t("memoryRequest")}>
              <Input id="recipe-mem-req" placeholder="32Gi" value={form.memory_request ?? ""} onChange={optText("memory_request")} />
            </Field>
            <Field id="recipe-mem-limit" label={t("memoryLimit")}>
              <Input id="recipe-mem-limit" placeholder="64Gi" value={form.memory_limit ?? ""} onChange={optText("memory_limit")} />
            </Field>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">{t("sectionStorage")}</CardTitle></CardHeader>
        <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field id="recipe-pvc-name" label={t("pvcName")}>
            <Input id="recipe-pvc-name" className="font-mono" value={form.pvc_name ?? ""} onChange={optText("pvc_name")} />
          </Field>
          <Field id="recipe-pvc-mount" label={t("pvcMountPath")}>
            <Input id="recipe-pvc-mount" className="font-mono" placeholder="/models" value={form.pvc_mount_path ?? ""} onChange={optText("pvc_mount_path")} />
          </Field>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("sectionEngineArgs")}</CardTitle>
          <p className="text-xs text-muted-foreground">{t("engineArgsHint")}</p>
        </CardHeader>
        <CardContent className="space-y-4">
          <EngineArgsFields
            engine={form.engine} values={form.engine_args}
            onChange={(key, v) => setForm((f) => ({ ...f, engine_args: withEngineArg(f.engine_args, key, v) }))}
          />
          <Field id="recipe-args" label={t("extraArgs")} hint={isPd ? t("pdHint") : undefined}>
            <Area
              id="recipe-args" value={argsText} onChange={setArgsText}
              placeholder={form.engine === "sglang" ? "--log-level=info\n--schedule-policy=lpm" : "--max-model-len=8192\n--tensor-parallel-size=2"}
            />
          </Field>
        </CardContent>
      </Card>

      {isPd && (
        <Card data-testid="recipe-pd-section">
          <CardHeader>
            <CardTitle className="text-base">{t("sectionPd")}</CardTitle>
            <p className="text-xs text-muted-foreground">{t("pdHint")}</p>
          </CardHeader>
          <CardContent className="space-y-5">
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <PdRoleCard role="prefill" value={pd.prefill ?? {}} onChange={(v) => setRole("prefill", v)} gpuTypes={gpuTypeNames} />
              <PdRoleCard role="decode" value={pd.decode ?? {}} onChange={(v) => setRole("decode", v)} gpuTypes={gpuTypeNames} />
            </div>
            <div className="space-y-3">
              <div className="text-sm font-medium">{t("pdKvSection")}</div>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <Field id="pd-nixl-port" label={t("pdNixlPort")}>
                  <Input
                    id="pd-nixl-port" type="number" min={1024} max={65535} step={1}
                    value={pd.nixl_port ?? DEFAULT_NIXL_PORT}
                    onChange={(e) => setPd({ nixl_port: e.target.value === "" ? DEFAULT_NIXL_PORT : Number(e.target.value) })}
                  />
                </Field>
                <Field id="pd-sidecar-image" label={t("pdSidecarImage")} hint={t("pdSidecarImageHint")}>
                  <Input
                    id="pd-sidecar-image" className="font-mono" placeholder="ghcr.io/llm-d/llm-d-router-disagg-sidecar:main"
                    value={pd.sidecar_image ?? ""} onChange={(e) => setPd({ sidecar_image: e.target.value || null })}
                  />
                </Field>
                <Field id="pd-kv-extra" label={t("pdKvExtra")} span2 hint={t("pdKvExtraHint")}>
                  <Area id="pd-kv-extra" rows={3} value={kvExtraText} onChange={setKvExtraText} placeholder={'{"kv_buffer_device": "cuda"}'} />
                </Field>
              </div>
            </div>
            <div className="space-y-3">
              <div className="text-sm font-medium">{t("pdRouterSection")}</div>
              <p className="text-xs text-muted-foreground">{t("pdRouterHint")}</p>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <Field id="pd-peak-prefill" label={t("pdPeakPrefillThroughput")}>
                  <Input
                    id="pd-peak-prefill" type="number" min={1} step={1} placeholder="33821"
                    value={pd.router?.peak_prefill_throughput ?? ""}
                    onChange={(e) => setPd({ router: { ...(pd.router ?? {}), peak_prefill_throughput: e.target.value === "" ? undefined : Number(e.target.value) } })}
                  />
                </Field>
                <Field id="pd-prefix-tokens" label={t("pdPrefixTokensToMatch")}>
                  <Input
                    id="pd-prefix-tokens" type="number" min={1} step={1} placeholder="131072"
                    value={pd.router?.prefix_tokens_to_match ?? ""}
                    onChange={(e) => setPd({ router: { ...(pd.router ?? {}), prefix_tokens_to_match: e.target.value === "" ? undefined : Number(e.target.value) } })}
                  />
                </Field>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("sectionProbes")}</CardTitle>
          <p className="text-xs text-muted-foreground">{t("probesHint")}</p>
        </CardHeader>
        <CardContent className="space-y-5">
          <ProbeFields
            kind="readiness"
            title={t("readiness")}
            spec={form.probes?.readiness ?? null}
            defaults={READINESS_DEFAULTS}
            onChange={(k, v) => setProbe("readiness", k, v)}
          />
          <div className="space-y-3">
            <label className="flex items-center gap-2 text-sm font-medium">
              <input type="checkbox" checked={form.probes?.liveness != null} onChange={(e) => setOptionalProbe("liveness", e.target.checked)} />
              {t("livenessEnable")}
            </label>
            {form.probes?.liveness != null && (
              <ProbeFields
                kind="liveness"
                title={t("liveness")}
                spec={form.probes.liveness}
                defaults={LIVENESS_DEFAULTS}
                onChange={(k, v) => setProbe("liveness", k, v)}
              />
            )}
          </div>
          <div className="space-y-3">
            <label className="flex items-center gap-2 text-sm font-medium">
              <input type="checkbox" checked={form.probes?.startup != null} onChange={(e) => setOptionalProbe("startup", e.target.checked)} />
              {t("startupEnable")}
            </label>
            {isPd && <p className="text-xs text-muted-foreground">{t("startupPdHint")}</p>}
            {form.probes?.startup != null && (
              <ProbeFields
                kind="startup"
                title={t("startup")}
                spec={form.probes.startup}
                defaults={STARTUP_DEFAULTS}
                onChange={(k, v) => setProbe("startup", k, v)}
              />
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">{t("sectionAdvanced")}</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <Field id="recipe-env" label={t("env")}>
            <Area id="recipe-env" value={envText} onChange={setEnvText} placeholder={"HF_HOME=/models/.cache\nVLLM_LOGGING_LEVEL=INFO"} />
          </Field>
          <details className="rounded-md border p-3" open={!!(runtime.shm_size_gi || runtime.host_ipc || runtime.privileged || extraResText)}>
            <summary className="cursor-pointer text-sm font-medium">{t("sectionRuntime")}</summary>
            <p className="mt-1 text-xs text-muted-foreground">{t("runtimeHint")}</p>
            <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Field id="recipe-shm" label={t("runtimeShm")}>
                <Input
                  id="recipe-shm" type="number" min={1} max={4096} step={1} placeholder="16"
                  value={runtime.shm_size_gi ?? ""}
                  onChange={(e) => setRuntime({ shm_size_gi: e.target.value === "" ? null : Number(e.target.value) })}
                />
              </Field>
              <div className="flex flex-wrap items-end gap-x-6 gap-y-2 pb-2">
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={!!runtime.host_ipc} onChange={(e) => setRuntime({ host_ipc: e.target.checked })} />
                  {t("runtimeHostIpc")}
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={!!runtime.privileged} onChange={(e) => setRuntime({ privileged: e.target.checked })} />
                  {t("runtimePrivileged")}
                </label>
              </div>
              <Field id="recipe-extra-resources" label={t("runtimeExtraResources")} span2 hint={t("runtimeExtraResourcesHint")}>
                <Area id="recipe-extra-resources" rows={2} value={extraResText} onChange={setExtraResText} placeholder="rdma/ib=1" />
              </Field>
            </div>
          </details>
          <details className="rounded-md border p-3" open={!!(form.node_selector || form.tolerations || form.gpu_resource_key !== "nvidia.com/gpu")}>
            <summary className="cursor-pointer text-sm font-medium">{t("advancedPlacement")}</summary>
            <p className="mt-1 text-xs text-muted-foreground">{t("advancedPlacementHint")}</p>
            <div className="mt-3 space-y-4">
              <Field id="recipe-gpu-key" label={t("gpuResourceKey")}>
                <Input id="recipe-gpu-key" className="font-mono" value={form.gpu_resource_key} onChange={text("gpu_resource_key")} />
              </Field>
              <Field id="recipe-node-selector" label={t("nodeSelector")}>
                <Area id="recipe-node-selector" value={nsText} onChange={setNsText} placeholder="gpu-type=a100-80g" />
              </Field>
              <Field id="recipe-tolerations" label={t("tolerations")}>
                <Area id="recipe-tolerations" value={tolText} onChange={setTolText} placeholder={"nvidia.com/gpu=present:NoSchedule\ndedicated=llm:NoExecute\nspot"} />
              </Field>
            </div>
          </details>
        </CardContent>
      </Card>

      <div className="flex items-center justify-end gap-3">
        <Button asChild type="button" variant="outline" disabled={saving}>
          <Link href={RECIPES_HREF}>{tc("cancel")}</Link>
        </Button>
        <Button type="submit" disabled={saving}>
          {saving && <Loader2 className="size-4 animate-spin" />}
          {recipe ? t("save") : t("create")}
        </Button>
      </div>
    </form>
  );
}

/** Mirrors backend serving_probes defaults; shown as placeholders so an empty field reads as "default". */
const READINESS_DEFAULTS: Required<ProbeSpec> = {
  path: "/health", initial_delay_seconds: 60, period_seconds: 10, timeout_seconds: 5, failure_threshold: 30,
};
const LIVENESS_DEFAULTS: Required<ProbeSpec> = {
  path: "/health", initial_delay_seconds: 120, period_seconds: 30, timeout_seconds: 5, failure_threshold: 3,
};
/** The P/D default (STARTUP_DEFAULTS_PD); aggregated servings only get a startup probe when enabled here. */
const STARTUP_DEFAULTS: Required<ProbeSpec> = {
  path: "/health", initial_delay_seconds: 15, period_seconds: 30, timeout_seconds: 5, failure_threshold: 120,
};
const PROBE_NUMERIC: (keyof ProbeSpec)[] = ["initial_delay_seconds", "period_seconds", "timeout_seconds", "failure_threshold"];
const PROBE_LABEL: Record<keyof ProbeSpec, string> = {
  path: "probePath", initial_delay_seconds: "probeInitialDelay", period_seconds: "probePeriod",
  timeout_seconds: "probeTimeout", failure_threshold: "probeFailureThreshold",
};

function ProbeFields({
  kind, title, spec, defaults, onChange,
}: {
  kind: "readiness" | "liveness" | "startup";
  title: string;
  spec: ProbeSpec | null;
  defaults: Required<ProbeSpec>;
  onChange: (key: keyof ProbeSpec, value: string) => void;
}) {
  const t = useTranslations("servingRecipes");
  return (
    <div className="space-y-2">
      <div className="text-sm font-medium">{title}</div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        <div className="space-y-1 col-span-2 sm:col-span-1">
          <Label htmlFor={`probe-${kind}-path`} className="text-xs">{t("probePath")}</Label>
          <Input
            id={`probe-${kind}-path`} className="font-mono" placeholder={defaults.path}
            value={spec?.path ?? ""} onChange={(e) => onChange("path", e.target.value)}
          />
        </div>
        {PROBE_NUMERIC.map((k) => (
          <div key={k} className="space-y-1">
            <Label htmlFor={`probe-${kind}-${k}`} className="text-xs">{t(PROBE_LABEL[k])}</Label>
            <Input
              id={`probe-${kind}-${k}`} type="number" min={k === "initial_delay_seconds" ? 0 : 1} step={1}
              placeholder={String(defaults[k])}
              value={spec?.[k] ?? ""} onChange={(e) => onChange(k, e.target.value)}
            />
          </div>
        ))}
      </div>
    </div>
  );
}
