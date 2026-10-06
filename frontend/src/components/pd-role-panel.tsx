"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { Copy } from "lucide-react";

import type { PdRoleOverride } from "@/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Area, EngineArgsFields, Field, withEngineArg } from "@/components/engine-args-fields";
import { linesToList, linesToMap, listToLines, mapToLines } from "@/lib/placement";
import type { PdRole } from "@/lib/pd-serving";

type RoleText = "cpu_request" | "cpu_limit" | "memory_request" | "memory_limit";

/**
 * One pool of a prefill/decode recipe: its complete config (replicas,
 * GPUs, CPU/memory, engine args, extra args, env). Text areas keep their own
 * string state and emit the parsed value; the parent remounts the panel
 * (new `key`) when it copies the other pool's settings in.
 */
export function PdRolePanel({
  role, value, onChange, gpuTypes, onCopyFrom,
}: {
  role: PdRole;
  value: PdRoleOverride;
  onChange: (next: PdRoleOverride) => void;
  gpuTypes?: string[];
  /** Copies the other pool's settings into this one. */
  onCopyFrom?: () => void;
}) {
  const t = useTranslations("servingRecipes");
  const other: PdRole = role === "prefill" ? "decode" : "prefill";
  const [argsText, setArgsText] = useState(() => listToLines(value.vllm_extra_args ?? null));
  const [envText, setEnvText] = useState(() => mapToLines(value.env ?? null));
  const set = (patch: Partial<PdRoleOverride>) => onChange({ ...value, ...patch });
  const text = (k: RoleText) => (e: React.ChangeEvent<HTMLInputElement>) => set({ [k]: e.target.value || null });

  return (
    <div className="space-y-6" data-testid={`pd-role-${role}`}>
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <CardTitle className="text-base">{t(role === "prefill" ? "pdRolePrefill" : "pdRoleDecode")}</CardTitle>
              <p className="mt-1 font-mono text-[11px] text-muted-foreground">{t(role === "prefill" ? "pdRolePrefillHint" : "pdRoleDecodeHint")}</p>
            </div>
            {onCopyFrom && (
              <Button type="button" variant="outline" size="sm" onClick={onCopyFrom} data-testid={`pd-copy-${role}`}>
                <Copy className="size-3.5" />{t("pdCopyFrom", { role: other })}
              </Button>
            )}
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <Field id={`pd-${role}-replicas`} label={t("pdRoleReplicas")}>
              <Input
                id={`pd-${role}-replicas`} type="number" min={0} step={1}
                value={value.replicas ?? 1}
                onChange={(e) => set({ replicas: e.target.value === "" ? 0 : Number(e.target.value) })}
              />
            </Field>
            <Field id={`pd-${role}-gpu-count`} label={t("gpuCount")}>
              <Input
                id={`pd-${role}-gpu-count`} type="number" min={0} step={1}
                value={value.gpu_count ?? 0}
                onChange={(e) => set({ gpu_count: e.target.value === "" ? 0 : Number(e.target.value) })}
              />
            </Field>
            <Field id={`pd-${role}-gpu-type`} label={t("gpuType")} hint={t("pdRoleBlank")}>
              <Input
                id={`pd-${role}-gpu-type`} className="font-mono" list={`pd-${role}-gpu-type-options`} placeholder="a100-80g"
                value={value.gpu_type ?? ""}
                onChange={(e) => set({ gpu_type: e.target.value.trim() || null })}
              />
              <datalist id={`pd-${role}-gpu-type-options`}>
                {(gpuTypes ?? []).map((g) => <option key={g} value={g} />)}
              </datalist>
            </Field>
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Field id={`pd-${role}-cpu-req`} label={t("cpuRequest")}>
              <Input id={`pd-${role}-cpu-req`} placeholder="4" value={value.cpu_request ?? ""} onChange={text("cpu_request")} />
            </Field>
            <Field id={`pd-${role}-cpu-limit`} label={t("cpuLimit")}>
              <Input id={`pd-${role}-cpu-limit`} placeholder="8" value={value.cpu_limit ?? ""} onChange={text("cpu_limit")} />
            </Field>
            <Field id={`pd-${role}-mem-req`} label={t("memoryRequest")}>
              <Input id={`pd-${role}-mem-req`} placeholder="32Gi" value={value.memory_request ?? ""} onChange={text("memory_request")} />
            </Field>
            <Field id={`pd-${role}-mem-limit`} label={t("memoryLimit")}>
              <Input id={`pd-${role}-mem-limit`} placeholder="64Gi" value={value.memory_limit ?? ""} onChange={text("memory_limit")} />
            </Field>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("sectionEngineArgs")}</CardTitle>
          <p className="text-xs text-muted-foreground">{t("pdRoleHint")}</p>
        </CardHeader>
        <CardContent className="space-y-4">
          <EngineArgsFields
            engine="vllm"
            values={value.engine_args ?? null}
            idPrefix={`pd-${role}-arg`}
            onChange={(key, v) => set({ engine_args: withEngineArg(value.engine_args, key, v) })}
          />
          <Field id={`pd-${role}-extra-args`} label={t("pdRoleExtraArgs")}>
            <Area
              id={`pd-${role}-extra-args`} rows={4} value={argsText}
              placeholder={role === "prefill" ? '--compilation-config={"cudagraph_mm_encoder": true}' : '--compilation-config={"cudagraph_mode":"FULL_DECODE_ONLY"}'}
              onChange={(v) => { setArgsText(v); set({ vllm_extra_args: linesToList(v) }); }}
            />
          </Field>
          <Field id={`pd-${role}-env`} label={t("pdRoleEnv")}>
            <Area
              id={`pd-${role}-env`} rows={4} value={envText} placeholder={"VLLM_ENGINE_READY_TIMEOUT_S=3600\nVLLM_KV_CACHE_LAYOUT=HND"}
              onChange={(v) => { setEnvText(v); set({ env: linesToMap(v) }); }}
            />
          </Field>
        </CardContent>
      </Card>
    </div>
  );
}
