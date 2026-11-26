"use client";

import { useTranslations } from "next-intl";
import { useLocaleTag } from "@/lib/locale";
import type { BenchmarkRun } from "@/types";

function number(value: unknown): number | null {
  if (typeof value !== "number" && (typeof value !== "string" || !value.trim())) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

/** Configured load, not achieved throughput or observed cache hits. */
export function BenchmarkWorkloadSummary({ run }: { run: BenchmarkRun }) {
  const t = useTranslations("benchmarkSimple");
  const locale = useLocaleTag();
  if (run.kind !== "performance") return null;

  const params = run.params;
  const agentic = params.workload_type === "agentic_replay";
  const pairedCache = params.experiment === "prefix_cache";
  const input = number(params.random_input_len);
  const prefix = number(params.prefix_len);
  const ratio = pairedCache
    ? input && prefix != null ? prefix / input * 100 : null
    : number(params.cache_ratio);
  const estimated = params.input_length_mode === "text_estimate"
    || (run.result?.input as { length_mode?: string } | undefined)?.length_mode === "text_estimate";
  const fmt = (value: unknown) => {
    const parsed = number(value);
    return parsed == null ? "—" : parsed.toLocaleString(locale, { maximumFractionDigits: 2 });
  };
  const output = number(params.random_output_len);
  const items = [
    { label: t("summaryInput"), value: agentic ? t("summaryVariableInput")
      : estimated && input != null ? t("summaryEstimated", { value: fmt(input) }) : fmt(input) },
    { label: t("summaryOutput"), value: output == null ? "—"
      : t(agentic ? "summaryOutputPerTurn" : "summaryMax", { value: fmt(output) }) },
    { label: t(agentic ? "summarySessionConcurrency" : "summaryConcurrency"), value: fmt(params.max_concurrency) },
    { label: t("summaryCache"), value: agentic ? t("summaryScenarioCache")
      : ratio == null || ratio > 100 ? "—" : `${pairedCache ? "0% → " : ""}${fmt(ratio)}%`, hint: t("ratioHint") },
  ];

  return <dl aria-label={t("summaryConditions")} className="flex flex-wrap gap-x-4 gap-y-1 text-xs font-normal text-muted-foreground">
    {items.map(item => <div key={item.label} className="inline-flex gap-1 whitespace-nowrap" title={item.hint}>
      <dt>{item.label}</dt><dd className="font-medium tabular-nums text-foreground">{item.value}</dd>
    </div>)}
  </dl>;
}
