// frontend/src/app/(app)/admin/benchmarks/sweeps/new/page.tsx
"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, Loader2, Play, Plus, X } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import {
  useBenchmarkPresets,
  useCreateBenchmarkSweep,
  useExternalServings,
  useModelDeployments,
} from "@/hooks/use-api";
import type { ExternalServing } from "@/hooks/use-api";
import type { CreateBenchmarkSweepRequest, SweepVariable } from "@/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const FLAG_RE = /^--[a-z0-9][a-z0-9-]*$/;
const PRESET_LABEL_KEY: Record<string, string> = {
  chat: "presetChat",
  long_input: "presetLongInput",
  long_output: "presetLongOutput",
};

type VarRow = { flag: string; values: string };

function parseValues(raw: string): (number | string)[] {
  return raw
    .split(",")
    .map((v) => v.trim())
    .filter(Boolean)
    .map((v) => (v !== "" && !Number.isNaN(Number(v)) ? Number(v) : v));
}

export default function NewSweepPage() {
  const t = useTranslations("benchmarkSweeps");
  const router = useRouter();
  const { data: deployments } = useModelDeployments();
  const { data: external } = useExternalServings();
  const { data: presets } = useBenchmarkPresets();
  const createMut = useCreateBenchmarkSweep();

  const [name, setName] = useState("");
  const [target, setTarget] = useState("");
  const [preset, setPreset] = useState("chat");
  const [rows, setRows] = useState<VarRow[]>([{ flag: "", values: "" }]);

  const servings = external?.servings ?? [];
  const readyDeployments = (deployments ?? []).filter((d) => d.ready_replicas > 0);
  const extKey = (s: ExternalServing) => `ext::${s.cluster_id ?? ""}::${s.namespace}::${s.deployment_name}`;

  const variables: SweepVariable[] = rows
    .filter((r) => r.flag.trim() && parseValues(r.values).length > 0)
    .map((r) => ({ flag: r.flag.trim(), values: parseValues(r.values) }));
  const comboCount = variables.length
    ? variables.reduce((n, v) => n * v.values.length, 1)
    : 0;
  const blankRows = rows.filter((r) => !r.flag.trim() && !r.values.trim());
  const hasIncompleteRow = variables.length + blankRows.length !== rows.length;
  const flagsValid =
    variables.length >= 1 &&
    variables.every((v) => FLAG_RE.test(v.flag)) &&
    new Set(variables.map((v) => v.flag)).size === variables.length;
  const combosValid = comboCount >= 2 && comboCount <= 12;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!target) return toast.error(t("targetRequired"));
    if (hasIncompleteRow) return toast.error(t("rowIncomplete"));
    if (!flagsValid) return toast.error(t("flagInvalid"));
    if (!combosValid) return toast.error(t("comboInvalid"));
    const body: CreateBenchmarkSweepRequest = {
      name: name.trim() || undefined,
      preset,
      variables,
    };
    if (target.startsWith("ext::")) {
      const s = servings.find((x) => extKey(x) === target);
      if (!s) return toast.error(t("targetRequired"));
      body.external_target = {
        cluster_id: s.cluster_id ?? null,
        namespace: s.namespace,
        deployment_name: s.deployment_name,
      };
    } else {
      body.deployment_id = target;
    }
    createMut.mutate(body, {
      onSuccess: (sweep) => router.push(`/admin/benchmarks/sweeps/${sweep.id}`),
      onError: (err) => toast.error(err instanceof Error ? err.message : t("createError")),
    });
  };

  return (
    <div className="space-y-6 max-w-3xl">
      <div>
        <Link
          href="/admin/benchmarks"
          className="text-sm text-muted-foreground hover:text-foreground inline-flex items-center gap-1"
        >
          <ArrowLeft className="size-3.5" />
          {t("backToList")}
        </Link>
        <h1 className="text-2xl font-bold mt-2">{t("newTitle")}</h1>
        <p className="text-muted-foreground mt-1">{t("newDescription")}</p>
      </div>

      <form onSubmit={handleSubmit}>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t("newTitle")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-5">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="sweep-name">{t("nameLabel")}</Label>
                <Input id="sweep-name" value={name} onChange={(e) => setName(e.target.value)} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="sweep-target">{t("targetLabel")}</Label>
                <select
                  id="sweep-target"
                  className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                  value={target}
                  onChange={(e) => setTarget(e.target.value)}
                >
                  <option value="">{t("targetPlaceholder")}</option>
                  <optgroup label={t("deploymentGroup")}>
                    {readyDeployments.map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.model_name} ({d.namespace})
                      </option>
                    ))}
                  </optgroup>
                  <optgroup label={t("externalGroup")}>
                    {servings.map((s) => (
                      <option key={extKey(s)} value={extKey(s)}>
                        {s.deployment_name} ({s.namespace})
                      </option>
                    ))}
                  </optgroup>
                </select>
              </div>
            </div>

            <div className="space-y-2">
              <Label>{t("presetLabel")}</Label>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                {Object.entries(presets ?? {}).map(([key, p]) => (
                  <button
                    key={key}
                    type="button"
                    onClick={() => setPreset(key)}
                    className={`rounded-lg border p-3 text-left text-sm transition-colors ${
                      preset === key ? "border-primary ring-2 ring-primary/30" : "hover:bg-muted/40"
                    }`}
                  >
                    <div className="font-medium">{t(PRESET_LABEL_KEY[key] ?? key)}</div>
                    <div className="text-xs text-muted-foreground mt-1">
                      {t("presetDetail", {
                        input: p.random_input_len,
                        output: p.random_output_len,
                        prompts: p.num_prompts,
                        conc: p.max_concurrency,
                      })}
                    </div>
                  </button>
                ))}
              </div>
            </div>

            <div className="space-y-2">
              <Label>{t("variablesLabel")}</Label>
              {rows.map((row, i) => (
                <div key={i} className="flex items-center gap-2">
                  <Input
                    className="font-mono"
                    placeholder={t("flagPlaceholder")}
                    value={row.flag}
                    onChange={(e) =>
                      setRows(rows.map((r, j) => (j === i ? { ...r, flag: e.target.value } : r)))
                    }
                  />
                  <Input
                    className="font-mono"
                    placeholder={t("valuesPlaceholder")}
                    value={row.values}
                    onChange={(e) =>
                      setRows(rows.map((r, j) => (j === i ? { ...r, values: e.target.value } : r)))
                    }
                  />
                  {rows.length > 1 && (
                    <Button type="button" variant="ghost" size="sm" onClick={() => setRows(rows.filter((_, j) => j !== i))}>
                      <X className="size-4" />
                    </Button>
                  )}
                </div>
              ))}
              <div className="flex items-center justify-between">
                <p className="text-xs text-muted-foreground">{t("valuesHint")}</p>
                {rows.length < 2 && (
                  <Button type="button" variant="outline" size="sm" onClick={() => setRows([...rows, { flag: "", values: "" }])}>
                    <Plus className="size-3.5 mr-1" />
                    {t("addVariable")}
                  </Button>
                )}
              </div>
              <p className={`text-sm ${hasIncompleteRow || (comboCount > 0 && !combosValid) ? "text-destructive" : "text-muted-foreground"}`}>
                {hasIncompleteRow
                  ? t("rowIncomplete")
                  : comboCount > 0 && (combosValid ? t("comboCount", { count: comboCount }) : t("comboInvalid"))}
              </p>
            </div>

            <div className="flex justify-end">
              <Button type="submit" disabled={createMut.isPending}>
                {createMut.isPending ? (
                  <Loader2 className="size-4 mr-1 animate-spin" />
                ) : (
                  <Play className="size-4 mr-1" />
                )}
                {createMut.isPending ? t("submitting") : t("submit")}
              </Button>
            </div>
          </CardContent>
        </Card>
      </form>
    </div>
  );
}
