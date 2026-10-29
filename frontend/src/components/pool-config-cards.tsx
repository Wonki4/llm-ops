"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";

import type { GpuTypeOption, PoolConfig, ProbeSpec, ProbesSpec, RuntimeOptions, ServingEngine } from "@/types";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { StorageMountsEditor } from "@/components/storage-mounts-editor";
import { Area, EngineArgsFields, Field, ROW_GRID, withEngineArg } from "@/components/engine-args-fields";
import { CollapsibleSection, FormSection } from "@/components/form-section";
import { linesToList, linesToMap, linesToTolerations, listToLines, mapToLines, tolerationsToLines } from "@/lib/placement";

type OptionalText =
  | "cpu_request" | "cpu_limit" | "memory_request" | "memory_limit";
type OptionalProbe = "liveness" | "startup";

/**
 * The sections that describe one serving pool (rendered inside a FormCard):
 * image & model, resources, storage, engine args, then folded health checks,
 * env, runtime and placement with a one-line summary each.
 * An aggregated recipe binds these to the recipe row; a P/D recipe renders
 * one set per pool. Line-oriented text areas keep their own string state
 * and emit the parsed value on every change.
 */
export function PoolConfigCards({
  idPrefix, engine, value, onChange, gpuTypes, pdHints,
}: {
  idPrefix: string;
  engine: ServingEngine;
  value: PoolConfig;
  onChange: (patch: Partial<PoolConfig>) => void;
  gpuTypes: GpuTypeOption[];
  /** P/D: show the startup-probe default note. */
  pdHints?: boolean;
}) {
  const t = useTranslations("servingRecipes");
  const id = (s: string) => `${idPrefix}-${s}`;
  const [argsText, setArgsText] = useState(() => listToLines(value.vllm_extra_args));
  const [envText, setEnvText] = useState(() => mapToLines(value.env));
  const [nsText, setNsText] = useState(() => mapToLines(value.node_selector));
  const [tolText, setTolText] = useState(() => tolerationsToLines(value.tolerations));
  const [extraResText, setExtraResText] = useState(() => mapToLines(value.runtime?.extra_resources ?? null));
  const runtime: RuntimeOptions = value.runtime ?? {};

  const text = (k: "image" | "model_path" | "gpu_resource_key") => (e: React.ChangeEvent<HTMLInputElement>) =>
    onChange({ [k]: e.target.value });
  const optText = (k: OptionalText) => (e: React.ChangeEvent<HTMLInputElement>) => onChange({ [k]: e.target.value || null });

  function setProbe(kind: "readiness" | OptionalProbe, key: keyof ProbeSpec, raw: string) {
    const probes: ProbesSpec = { ...(value.probes ?? {}) };
    const spec: ProbeSpec = { ...(probes[kind] ?? {}) };
    if (raw === "") delete spec[key];
    else if (key === "path") spec.path = raw;
    else spec[key] = Number(raw);
    probes[kind] = spec;
    onChange({ probes });
  }
  function setOptionalProbe(kind: OptionalProbe, enabled: boolean) {
    onChange({ probes: { ...(value.probes ?? {}), [kind]: enabled ? (value.probes?.[kind] ?? {}) : null } });
  }
  function setRuntime(patch: Partial<RuntimeOptions>) {
    const next: RuntimeOptions = { ...runtime, ...patch };
    const empty = !next.shm_size_gi && !next.host_ipc && !next.privileged && !Object.keys(next.extra_resources ?? {}).length;
    onChange({ runtime: empty ? null : next });
  }

  const probes = value.probes ?? {};
  const probeSummary = [
    `readiness ${probes.readiness?.path ?? READINESS_DEFAULTS.path} · ${probes.readiness?.initial_delay_seconds ?? READINESS_DEFAULTS.initial_delay_seconds}s`,
    probes.liveness != null && "liveness",
    probes.startup != null && "startup",
  ].filter(Boolean).join(" · ");
  const envCount = Object.keys(value.env ?? {}).length;
  const runtimeSummary = [
    runtime.host_ipc && "hostIPC",
    runtime.privileged && "privileged",
    ...Object.entries(runtime.extra_resources ?? {}).map(([k, v]) => `${k}=${v}`),
  ].filter(Boolean).join(" · ");
  const placementSummary = [
    value.gpu_resource_key !== "nvidia.com/gpu" && value.gpu_resource_key,
    ...Object.entries(value.node_selector ?? {}).map(([k, v]) => `${k}=${v}`),
    (value.tolerations?.length ?? 0) > 0 && `tolerations ${value.tolerations!.length}`,
  ].filter(Boolean).join(" · ");
  const inline = "text-xs text-muted-foreground";

  return (
    <>
      <FormSection title={t("sectionImage")}>
        <Field id={id("image")} label={t("image")} required>
          <Input id={id("image")} className="font-mono" value={value.image} onChange={text("image")} />
        </Field>
        <Field id={id("model-path")} label={t("modelPath")} required>
          <Input id={id("model-path")} className="font-mono" value={value.model_path} onChange={text("model_path")} />
        </Field>
      </FormSection>

      <FormSection title={t("sectionResources")}>
        <Field id={id("gpu-count")} label={t("resGpu")} hint={t("gpuTypeHint")}>
          <div className="flex items-center gap-2">
            <Input
              id={id("gpu-count")} aria-label={t("gpuCount")} type="number" min={0} className="w-20 shrink-0" value={value.gpu_count}
              onChange={(e) => onChange({ gpu_count: e.target.value === "" ? 0 : Number(e.target.value) })}
            />
            <span className={inline}>×</span>
            <select
              id={id("gpu-type")}
              aria-label={t("gpuType")}
              className="h-9 min-w-0 flex-1 rounded-md border border-input bg-transparent px-3 font-mono text-sm"
              value={value.gpu_type ?? ""}
              onChange={(e) => onChange({ gpu_type: e.target.value || null })}
            >
              <option value="">{t("gpuTypeNone")}</option>
              {gpuTypes.map((g) => <option key={g.name} value={g.name}>{g.name}</option>)}
              {/* A saved value no cluster defines any more stays selectable instead of silently clearing. */}
              {value.gpu_type && !gpuTypes.some((g) => g.name === value.gpu_type) && (
                <option value={value.gpu_type}>{t("gpuTypeUnregistered", { name: value.gpu_type })}</option>
              )}
            </select>
          </div>
        </Field>
        <Field id={id("cpu-req")} label={t("resCpu")}>
          <div className="grid grid-cols-[auto_minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-2">
            <span className={inline}>{t("resRequest")}</span>
            <Input id={id("cpu-req")} aria-label={t("cpuRequest")} placeholder="4" value={value.cpu_request ?? ""} onChange={optText("cpu_request")} />
            <span className={inline}>{t("resLimit")}</span>
            <Input id={id("cpu-limit")} aria-label={t("cpuLimit")} placeholder="8" value={value.cpu_limit ?? ""} onChange={optText("cpu_limit")} />
          </div>
        </Field>
        <Field id={id("mem-req")} label={t("resMemory")}>
          <div className="grid grid-cols-[auto_minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-2">
            <span className={inline}>{t("resRequest")}</span>
            <Input id={id("mem-req")} aria-label={t("memoryRequest")} placeholder="32Gi" value={value.memory_request ?? ""} onChange={optText("memory_request")} />
            <span className={inline}>{t("resLimit")}</span>
            <Input id={id("mem-limit")} aria-label={t("memoryLimit")} placeholder="64Gi" value={value.memory_limit ?? ""} onChange={optText("memory_limit")} />
          </div>
        </Field>
        <Field id={id("shm")} label={t("resShm")} hint={t("shmHint")}>
          <div className="flex items-center gap-2">
            <Input
              id={id("shm")} type="number" min={1} max={4096} step={1} className="w-28"
              value={runtime.shm_size_gi ?? ""}
              onChange={(e) => setRuntime({ shm_size_gi: e.target.value === "" ? null : Number(e.target.value) })}
            />
            <span className={inline}>Gi · /dev/shm</span>
          </div>
          {(value.gpu_count ?? 0) >= 2 && !runtime.shm_size_gi && (
            <p className="text-[11px] text-amber-600" data-testid={id("shm-warning")}>{t("shmMultiGpuWarning")}</p>
          )}
        </Field>
      </FormSection>

      <FormSection title={t("sectionStorage")}>
        <StorageMountsEditor
          idPrefix={idPrefix}
          value={value.storages ?? []}
          onChange={(storages) => onChange({ storages })}
          modelPath={value.model_path}
        />
      </FormSection>

      <FormSection title={t("sectionEngineArgs")} hint={t("engineArgsHint")}>
        <EngineArgsFields
          engine={engine} values={value.engine_args} idPrefix={id("arg")}
          onChange={(key, v) => onChange({ engine_args: withEngineArg(value.engine_args, key, v) })}
        />
        <Field id={id("args")} label={t("extraArgs")} hint={pdHints ? t("pdHint") : t("extraArgsPortHint")}>
          <Area
            id={id("args")} value={argsText}
            placeholder={engine === "sglang" ? "--log-level=info\n--schedule-policy=lpm" : "--max-model-len=8192\n--tensor-parallel-size=2"}
            onChange={(v) => { setArgsText(v); onChange({ vllm_extra_args: linesToList(v) }); }}
          />
        </Field>
      </FormSection>

      <CollapsibleSection title={t("sectionProbes")} summary={probeSummary} hint={t("probesHint")}>
        <ProbeFields
          idPrefix={idPrefix} kind="readiness" title={t("readiness")}
          spec={probes.readiness ?? null} defaults={READINESS_DEFAULTS}
          onChange={(k, v) => setProbe("readiness", k, v)}
        />
        <label className="flex items-center gap-2 text-xs font-medium">
          <input type="checkbox" checked={probes.liveness != null} onChange={(e) => setOptionalProbe("liveness", e.target.checked)} />
          {t("livenessEnable")}
        </label>
        {probes.liveness != null && (
          <ProbeFields
            idPrefix={idPrefix} kind="liveness" title={t("liveness")}
            spec={probes.liveness} defaults={LIVENESS_DEFAULTS}
            onChange={(k, v) => setProbe("liveness", k, v)}
          />
        )}
        <label className="flex items-center gap-2 text-xs font-medium">
          <input type="checkbox" checked={probes.startup != null} onChange={(e) => setOptionalProbe("startup", e.target.checked)} />
          {t("startupEnable")}
        </label>
        {pdHints && <p className="text-[11px] text-muted-foreground">{t("startupPdHint")}</p>}
        {probes.startup != null && (
          <ProbeFields
            idPrefix={idPrefix} kind="startup" title={t("startup")}
            spec={probes.startup} defaults={STARTUP_DEFAULTS}
            onChange={(k, v) => setProbe("startup", k, v)}
          />
        )}
      </CollapsibleSection>

      <CollapsibleSection title={t("env")} summary={envCount ? t("summaryCount", { count: envCount }) : t("summaryNone")} hint={t("envHint")}>
        <Area
          id={id("env")} value={envText} placeholder={"HF_HOME=/models/.cache\nVLLM_LOGGING_LEVEL=INFO"}
          onChange={(v) => { setEnvText(v); onChange({ env: linesToMap(v) }); }}
        />
      </CollapsibleSection>

      <CollapsibleSection title={t("sectionRuntime")} summary={runtimeSummary || t("summaryDefault")} hint={t("runtimeHint")}>
        <div className="flex flex-wrap gap-x-5 gap-y-1.5 sm:pl-[9.5rem]">
          <label className="flex items-center gap-1.5 text-xs">
            <input type="checkbox" checked={!!runtime.host_ipc} onChange={(e) => setRuntime({ host_ipc: e.target.checked })} />
            {t("runtimeHostIpc")}
          </label>
          <label className="flex items-center gap-1.5 text-xs">
            <input type="checkbox" checked={!!runtime.privileged} onChange={(e) => setRuntime({ privileged: e.target.checked })} />
            {t("runtimePrivileged")}
          </label>
        </div>
        <Field id={id("extra-resources")} label={t("runtimeExtraResources")} hint={t("runtimeExtraResourcesHint")}>
          <Area
            id={id("extra-resources")} rows={2} value={extraResText} placeholder="rdma/rdma_shared_device_a=1"
            onChange={(v) => { setExtraResText(v); setRuntime({ extra_resources: linesToMap(v) ?? {} }); }}
          />
        </Field>
      </CollapsibleSection>

      <CollapsibleSection title={t("advancedPlacement")} summary={placementSummary || t("summaryDefault")} hint={t("advancedPlacementHint")}>
        <Field id={id("gpu-key")} label={t("gpuResourceKey")}>
          <Input id={id("gpu-key")} className="font-mono" value={value.gpu_resource_key} onChange={text("gpu_resource_key")} />
        </Field>
        <Field id={id("node-selector")} label={t("nodeSelector")}>
          <Area
            id={id("node-selector")} rows={2} value={nsText} placeholder="gpu-type=a100-80g"
            onChange={(v) => { setNsText(v); onChange({ node_selector: linesToMap(v) }); }}
          />
        </Field>
        <Field id={id("tolerations")} label={t("tolerations")}>
          <Area
            id={id("tolerations")} value={tolText} placeholder={"nvidia.com/gpu=present:NoSchedule\ndedicated=llm:NoExecute\nspot"}
            onChange={(v) => { setTolText(v); onChange({ tolerations: linesToTolerations(v) }); }}
          />
        </Field>
      </CollapsibleSection>
    </>
  );
}

/** Mirrors backend serving_probes defaults; shown as placeholders so an empty field reads as "default". */
export const READINESS_DEFAULTS: Required<ProbeSpec> = {
  path: "/health", initial_delay_seconds: 10, period_seconds: 15, timeout_seconds: 3, failure_threshold: 3, success_threshold: 1,
};
export const LIVENESS_DEFAULTS: Required<ProbeSpec> = {
  path: "/health", initial_delay_seconds: 10, period_seconds: 15, timeout_seconds: 3, failure_threshold: 3, success_threshold: 1,
};
/** The P/D default (STARTUP_DEFAULTS_PD); aggregated servings only get a startup probe when enabled. */
export const STARTUP_DEFAULTS: Required<ProbeSpec> = {
  path: "/health", initial_delay_seconds: 10, period_seconds: 15, timeout_seconds: 3, failure_threshold: 120, success_threshold: 1,
};
const PROBE_NUMERIC: (keyof ProbeSpec)[] = [
  "initial_delay_seconds", "period_seconds", "timeout_seconds", "failure_threshold", "success_threshold",
];
const PROBE_LABEL: Record<keyof ProbeSpec, string> = {
  path: "probePath", initial_delay_seconds: "probeInitialDelay", period_seconds: "probePeriod",
  timeout_seconds: "probeTimeout", failure_threshold: "probeFailureThreshold", success_threshold: "probeSuccessThreshold",
};

function ProbeFields({
  idPrefix, kind, title, spec, defaults, onChange,
}: {
  idPrefix: string;
  kind: "readiness" | "liveness" | "startup";
  title: string;
  spec: ProbeSpec | null;
  defaults: Required<ProbeSpec>;
  onChange: (key: keyof ProbeSpec, value: string) => void;
}) {
  const t = useTranslations("servingRecipes");
  const id = (k: string) => `${idPrefix}-probe-${kind}-${k}`;
  return (
    <div className={ROW_GRID}>
      <div className="text-xs leading-4 text-muted-foreground sm:pt-6">{title}</div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
        <div className="col-span-full space-y-0.5">
          <Label htmlFor={id("path")} className="text-[11px] font-normal text-muted-foreground">{t("probePath")}</Label>
          <Input
            id={id("path")} className="font-mono" placeholder={defaults.path}
            value={spec?.path ?? ""} onChange={(e) => onChange("path", e.target.value)}
          />
        </div>
        {PROBE_NUMERIC.map((k) => (
          <div key={k} className="space-y-0.5">
            <Label htmlFor={id(k)} className="text-[11px] font-normal text-muted-foreground">{t(PROBE_LABEL[k])}</Label>
            <Input
              id={id(k)} type="number" min={k === "initial_delay_seconds" ? 0 : 1} step={1}
              max={k === "success_threshold" && kind !== "readiness" ? 1 : undefined}
              placeholder={String(defaults[k])}
              value={spec?.[k] ?? ""} onChange={(e) => onChange(k, e.target.value)}
            />
          </div>
        ))}
      </div>
    </div>
  );
}
