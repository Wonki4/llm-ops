"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";

import type { PdRoleOverride } from "@/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Area, EngineArgsFields, Field, withEngineArg } from "@/components/engine-args-fields";
import { linesToList, linesToMap, listToLines, mapToLines } from "@/lib/placement";

export type PdRole = "prefill" | "decode";

/**
 * One pool of a prefill/decode recipe: replicas, GPUs and the overrides that
 * sit on top of the recipe base. Text areas keep their own string state and
 * emit the parsed value, so the parent only ever sees a `PdRoleOverride`.
 */
export function PdRoleCard({
  role, value, onChange, gpuTypes,
}: {
  role: PdRole;
  value: PdRoleOverride;
  onChange: (next: PdRoleOverride) => void;
  gpuTypes?: string[];
}) {
  const t = useTranslations("servingRecipes");
  const [argsText, setArgsText] = useState(() => listToLines(value.vllm_extra_args ?? null));
  const [envText, setEnvText] = useState(() => mapToLines(value.env ?? null));
  const set = (patch: Partial<PdRoleOverride>) => onChange({ ...value, ...patch });
  const hasOverrides = !!(value.engine_args || (value.vllm_extra_args?.length ?? 0) > 0 || value.env);

  return (
    <Card data-testid={`pd-role-${role}`}>
      <CardHeader>
        <CardTitle className="text-sm">{t(role === "prefill" ? "pdRolePrefill" : "pdRoleDecode")}</CardTitle>
        <p className="font-mono text-[11px] text-muted-foreground">{t(role === "prefill" ? "pdRolePrefillHint" : "pdRoleDecodeHint")}</p>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <Field id={`pd-${role}-replicas`} label={t("pdRoleReplicas")}>
            <Input
              id={`pd-${role}-replicas`} type="number" min={0} step={1}
              value={value.replicas ?? 1}
              onChange={(e) => set({ replicas: e.target.value === "" ? 0 : Number(e.target.value) })}
            />
          </Field>
          <Field id={`pd-${role}-gpu-count`} label={t("pdRoleGpuCount")} hint={t("pdRoleBlank")}>
            <Input
              id={`pd-${role}-gpu-count`} type="number" min={0} step={1}
              value={value.gpu_count ?? ""}
              onChange={(e) => set({ gpu_count: e.target.value === "" ? null : Number(e.target.value) })}
            />
          </Field>
          <Field id={`pd-${role}-gpu-type`} label={t("pdRoleGpuType")} hint={t("pdRoleBlank")}>
            <Input
              id={`pd-${role}-gpu-type`} className="font-mono" list={`pd-${role}-gpu-type-options`}
              value={value.gpu_type ?? ""}
              onChange={(e) => set({ gpu_type: e.target.value.trim() || null })}
            />
            <datalist id={`pd-${role}-gpu-type-options`}>
              {(gpuTypes ?? []).map((g) => <option key={g} value={g} />)}
            </datalist>
          </Field>
        </div>
        <details className="rounded-md border p-3" open={hasOverrides}>
          <summary className="cursor-pointer text-sm font-medium">{t("pdRoleOverrides")}</summary>
          <div className="mt-3 space-y-4">
            <EngineArgsFields
              engine="vllm"
              values={value.engine_args ?? null}
              idPrefix={`pd-${role}-arg`}
              onChange={(key, v) => set({ engine_args: withEngineArg(value.engine_args, key, v) })}
            />
            <Field id={`pd-${role}-extra-args`} label={t("pdRoleExtraArgs")}>
              <Area
                id={`pd-${role}-extra-args`} rows={3} value={argsText}
                placeholder={role === "prefill" ? '--compilation-config={"cudagraph_mm_encoder": true}' : '--compilation-config={"cudagraph_mode":"FULL_DECODE_ONLY"}'}
                onChange={(v) => { setArgsText(v); set({ vllm_extra_args: linesToList(v) }); }}
              />
            </Field>
            <Field id={`pd-${role}-env`} label={t("pdRoleEnv")}>
              <Area
                id={`pd-${role}-env`} rows={3} value={envText} placeholder="VLLM_LOGGING_LEVEL=INFO"
                onChange={(v) => { setEnvText(v); set({ env: linesToMap(v) }); }}
              />
            </Field>
          </div>
        </details>
      </CardContent>
    </Card>
  );
}
