"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";

import type { GpuTypeOption, PoolConfig, ProbeSpec, ProbesSpec, RuntimeOptions, ServingEngine } from "@/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Area, EngineArgsFields, Field, withEngineArg } from "@/components/engine-args-fields";
import { linesToList, linesToMap, linesToTolerations, listToLines, mapToLines, tolerationsToLines } from "@/lib/placement";

type OptionalText =
  | "cpu_request" | "cpu_limit" | "memory_request" | "memory_limit" | "pvc_name" | "pvc_mount_path";
type OptionalProbe = "liveness" | "startup";

/**
 * The cards that describe one serving pool: image & model, resources,
 * storage, engine args, health checks, advanced (env, runtime, placement).
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

  return (
    <>
      <Card>
        <CardHeader><CardTitle className="text-base">{t("sectionImage")}</CardTitle></CardHeader>
        <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field id={id("image")} label={t("image")} required>
            <Input id={id("image")} className="font-mono" value={value.image} onChange={text("image")} />
          </Field>
          <Field id={id("model-path")} label={t("modelPath")} required>
            <Input id={id("model-path")} className="font-mono" value={value.model_path} onChange={text("model_path")} />
          </Field>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">{t("sectionResources")}</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field id={id("gpu-count")} label={t("gpuCount")}>
              <Input
                id={id("gpu-count")} type="number" min={0} value={value.gpu_count}
                onChange={(e) => onChange({ gpu_count: e.target.value === "" ? 0 : Number(e.target.value) })}
              />
            </Field>
            <Field id={id("gpu-type")} label={t("gpuType")} hint={t("gpuTypeHint")}>
              <Input
                id={id("gpu-type")} className="font-mono" list={id("gpu-type-options")} placeholder="a100-80g"
                value={value.gpu_type ?? ""} onChange={(e) => onChange({ gpu_type: e.target.value.trim() || null })}
              />
              <datalist id={id("gpu-type-options")}>
                {gpuTypes.map((g) => (
                  <option key={g.name} value={g.name}>{g.clusters.map((c) => c.cluster_name).join(", ")}</option>
                ))}
              </datalist>
            </Field>
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Field id={id("cpu-req")} label={t("cpuRequest")}>
              <Input id={id("cpu-req")} placeholder="4" value={value.cpu_request ?? ""} onChange={optText("cpu_request")} />
            </Field>
            <Field id={id("cpu-limit")} label={t("cpuLimit")}>
              <Input id={id("cpu-limit")} placeholder="8" value={value.cpu_limit ?? ""} onChange={optText("cpu_limit")} />
            </Field>
            <Field id={id("mem-req")} label={t("memoryRequest")}>
              <Input id={id("mem-req")} placeholder="32Gi" value={value.memory_request ?? ""} onChange={optText("memory_request")} />
            </Field>
            <Field id={id("mem-limit")} label={t("memoryLimit")}>
              <Input id={id("mem-limit")} placeholder="64Gi" value={value.memory_limit ?? ""} onChange={optText("memory_limit")} />
            </Field>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">{t("sectionStorage")}</CardTitle></CardHeader>
        <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field id={id("pvc-name")} label={t("pvcName")}>
            <Input id={id("pvc-name")} className="font-mono" value={value.pvc_name ?? ""} onChange={optText("pvc_name")} />
          </Field>
          <Field id={id("pvc-mount")} label={t("pvcMountPath")}>
            <Input id={id("pvc-mount")} className="font-mono" placeholder="/models" value={value.pvc_mount_path ?? ""} onChange={optText("pvc_mount_path")} />
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
            engine={engine} values={value.engine_args} idPrefix={id("arg")}
            onChange={(key, v) => onChange({ engine_args: withEngineArg(value.engine_args, key, v) })}
          />
          <Field id={id("args")} label={t("extraArgs")} hint={pdHints ? t("pdHint") : undefined}>
            <Area
              id={id("args")} value={argsText}
              placeholder={engine === "sglang" ? "--log-level=info\n--schedule-policy=lpm" : "--max-model-len=8192\n--tensor-parallel-size=2"}
              onChange={(v) => { setArgsText(v); onChange({ vllm_extra_args: linesToList(v) }); }}
            />
          </Field>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("sectionProbes")}</CardTitle>
          <p className="text-xs text-muted-foreground">{t("probesHint")}</p>
        </CardHeader>
        <CardContent className="space-y-5">
          <ProbeFields
            idPrefix={idPrefix} kind="readiness" title={t("readiness")}
            spec={value.probes?.readiness ?? null} defaults={READINESS_DEFAULTS}
            onChange={(k, v) => setProbe("readiness", k, v)}
          />
          <div className="space-y-3">
            <label className="flex items-center gap-2 text-sm font-medium">
              <input type="checkbox" checked={value.probes?.liveness != null} onChange={(e) => setOptionalProbe("liveness", e.target.checked)} />
              {t("livenessEnable")}
            </label>
            {value.probes?.liveness != null && (
              <ProbeFields
                idPrefix={idPrefix} kind="liveness" title={t("liveness")}
                spec={value.probes.liveness} defaults={LIVENESS_DEFAULTS}
                onChange={(k, v) => setProbe("liveness", k, v)}
              />
            )}
          </div>
          <div className="space-y-3">
            <label className="flex items-center gap-2 text-sm font-medium">
              <input type="checkbox" checked={value.probes?.startup != null} onChange={(e) => setOptionalProbe("startup", e.target.checked)} />
              {t("startupEnable")}
            </label>
            {pdHints && <p className="text-xs text-muted-foreground">{t("startupPdHint")}</p>}
            {value.probes?.startup != null && (
              <ProbeFields
                idPrefix={idPrefix} kind="startup" title={t("startup")}
                spec={value.probes.startup} defaults={STARTUP_DEFAULTS}
                onChange={(k, v) => setProbe("startup", k, v)}
              />
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">{t("sectionAdvanced")}</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <Field id={id("env")} label={t("env")}>
            <Area
              id={id("env")} value={envText} placeholder={"HF_HOME=/models/.cache\nVLLM_LOGGING_LEVEL=INFO"}
              onChange={(v) => { setEnvText(v); onChange({ env: linesToMap(v) }); }}
            />
          </Field>
          <details className="rounded-md border p-3" open={!!(runtime.shm_size_gi || runtime.host_ipc || runtime.privileged || extraResText)}>
            <summary className="cursor-pointer text-sm font-medium">{t("sectionRuntime")}</summary>
            <p className="mt-1 text-xs text-muted-foreground">{t("runtimeHint")}</p>
            <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Field id={id("shm")} label={t("runtimeShm")}>
                <Input
                  id={id("shm")} type="number" min={1} max={4096} step={1} placeholder="16"
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
              <Field id={id("extra-resources")} label={t("runtimeExtraResources")} span2 hint={t("runtimeExtraResourcesHint")}>
                <Area
                  id={id("extra-resources")} rows={2} value={extraResText} placeholder="rdma/ib=1"
                  onChange={(v) => { setExtraResText(v); setRuntime({ extra_resources: linesToMap(v) ?? {} }); }}
                />
              </Field>
            </div>
          </details>
          <details className="rounded-md border p-3" open={!!(value.node_selector || value.tolerations || value.gpu_resource_key !== "nvidia.com/gpu")}>
            <summary className="cursor-pointer text-sm font-medium">{t("advancedPlacement")}</summary>
            <p className="mt-1 text-xs text-muted-foreground">{t("advancedPlacementHint")}</p>
            <div className="mt-3 space-y-4">
              <Field id={id("gpu-key")} label={t("gpuResourceKey")}>
                <Input id={id("gpu-key")} className="font-mono" value={value.gpu_resource_key} onChange={text("gpu_resource_key")} />
              </Field>
              <Field id={id("node-selector")} label={t("nodeSelector")}>
                <Area
                  id={id("node-selector")} value={nsText} placeholder="gpu-type=a100-80g"
                  onChange={(v) => { setNsText(v); onChange({ node_selector: linesToMap(v) }); }}
                />
              </Field>
              <Field id={id("tolerations")} label={t("tolerations")}>
                <Area
                  id={id("tolerations")} value={tolText} placeholder={"nvidia.com/gpu=present:NoSchedule\ndedicated=llm:NoExecute\nspot"}
                  onChange={(v) => { setTolText(v); onChange({ tolerations: linesToTolerations(v) }); }}
                />
              </Field>
            </div>
          </details>
        </CardContent>
      </Card>
    </>
  );
}

/** Mirrors backend serving_probes defaults; shown as placeholders so an empty field reads as "default". */
export const READINESS_DEFAULTS: Required<ProbeSpec> = {
  path: "/health", initial_delay_seconds: 60, period_seconds: 10, timeout_seconds: 5, failure_threshold: 30,
};
export const LIVENESS_DEFAULTS: Required<ProbeSpec> = {
  path: "/health", initial_delay_seconds: 120, period_seconds: 30, timeout_seconds: 5, failure_threshold: 3,
};
/** The P/D default (STARTUP_DEFAULTS_PD); aggregated servings only get a startup probe when enabled. */
export const STARTUP_DEFAULTS: Required<ProbeSpec> = {
  path: "/health", initial_delay_seconds: 15, period_seconds: 30, timeout_seconds: 5, failure_threshold: 120,
};
const PROBE_NUMERIC: (keyof ProbeSpec)[] = ["initial_delay_seconds", "period_seconds", "timeout_seconds", "failure_threshold"];
const PROBE_LABEL: Record<keyof ProbeSpec, string> = {
  path: "probePath", initial_delay_seconds: "probeInitialDelay", period_seconds: "probePeriod",
  timeout_seconds: "probeTimeout", failure_threshold: "probeFailureThreshold",
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
    <div className="space-y-2">
      <div className="text-sm font-medium">{title}</div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        <div className="space-y-1 col-span-2 sm:col-span-1">
          <Label htmlFor={id("path")} className="text-xs">{t("probePath")}</Label>
          <Input
            id={id("path")} className="font-mono" placeholder={defaults.path}
            value={spec?.path ?? ""} onChange={(e) => onChange("path", e.target.value)}
          />
        </div>
        {PROBE_NUMERIC.map((k) => (
          <div key={k} className="space-y-1">
            <Label htmlFor={id(k)} className="text-xs">{t(PROBE_LABEL[k])}</Label>
            <Input
              id={id(k)} type="number" min={k === "initial_delay_seconds" ? 0 : 1} step={1}
              placeholder={String(defaults[k])}
              value={spec?.[k] ?? ""} onChange={(e) => onChange(k, e.target.value)}
            />
          </div>
        ))}
      </div>
    </div>
  );
}
