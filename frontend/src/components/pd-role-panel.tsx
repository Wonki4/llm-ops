"use client";

import { useTranslations } from "next-intl";
import { Copy } from "lucide-react";

import type { GpuTypeOption, PdRoleOverride, PoolConfig } from "@/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Field } from "@/components/engine-args-fields";
import { PoolConfigCards } from "@/components/pool-config-cards";
import { poolOf, type PdRole } from "@/lib/pd-serving";

/**
 * One pool of a prefill/decode recipe: its replica count plus the complete
 * pool config (image, model, resources, storage, args, probes, runtime,
 * placement). The parent remounts the panel (new `key`) after copying the
 * other pool's settings in so the text areas re-read their values.
 */
export function PdRolePanel({
  role, value, onChange, gpuTypes, onCopyFrom,
}: {
  role: PdRole;
  value: PdRoleOverride;
  onChange: (next: PdRoleOverride) => void;
  gpuTypes: GpuTypeOption[];
  onCopyFrom?: () => void;
}) {
  const t = useTranslations("servingRecipes");
  const other: PdRole = role === "prefill" ? "decode" : "prefill";
  const pool = poolOf(value as unknown as PoolConfig);

  return (
    <div className="space-y-6" data-testid={`pd-role-${role}`}>
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <CardTitle className="text-base">{t(role === "prefill" ? "pdRolePrefill" : "pdRoleDecode")}</CardTitle>
              <p className="mt-1 font-mono text-[11px] text-muted-foreground">{t(role === "prefill" ? "pdRolePrefillHint" : "pdRoleDecodeHint")}</p>
              <p className="mt-1 text-xs text-muted-foreground">{t("pdPoolHint")}</p>
            </div>
            {onCopyFrom && (
              <Button type="button" variant="outline" size="sm" onClick={onCopyFrom} data-testid={`pd-copy-${role}`}>
                <Copy className="size-3.5" />{t("pdCopyFrom", { role: other })}
              </Button>
            )}
          </div>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <Field id={`pd-${role}-replicas`} label={t("pdRoleReplicas")}>
              <Input
                id={`pd-${role}-replicas`} type="number" min={0} step={1}
                value={value.replicas ?? 1}
                onChange={(e) => onChange({ ...value, replicas: e.target.value === "" ? 0 : Number(e.target.value) })}
              />
            </Field>
          </div>
        </CardContent>
      </Card>
      <PoolConfigCards
        idPrefix={`pd-${role}`}
        engine="vllm"
        value={pool}
        gpuTypes={gpuTypes}
        pdHints
        onChange={(patch) => onChange({ ...value, ...patch })}
      />
    </div>
  );
}
