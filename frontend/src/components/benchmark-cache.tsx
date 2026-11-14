"use client";

import { useTranslations } from "next-intl";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { BenchmarkRun } from "@/types";

export function experimentOf(run: BenchmarkRun) {
  return run.params.experiment === "prefix_cache" ? "prefix_cache" : run.kind;
}

interface Phase {
  median_ttft_ms?: number;
  p99_ttft_ms?: number;
  output_throughput?: number;
  completed?: number;
  cache?: { hits: number | null; queries: number | null; hit_rate: number | null };
}

function phasesOf(run: BenchmarkRun) {
  return (run.result?.phases ?? {}) as { individual?: Phase; shared?: Phase };
}

export function ResultSummary({ run }: { run: BenchmarkRun }) {
  const t = useTranslations("benchExperiment");
  if (run.status !== "succeeded") return <>—</>;
  if (experimentOf(run) === "prefix_cache") {
    const { individual, shared } = phasesOf(run);
    const base = individual?.median_ttft_ms;
    const reused = shared?.median_ttft_ms;
    if (!base || reused == null) return <>—</>;
    const delta = (reused - base) / base * 100;
    return <>TTFT {delta > 0 ? "+" : ""}{delta.toFixed(1)}%</>;
  }
  const metrics = run.result?.metrics as Record<string, unknown> | undefined;
  if (typeof metrics?.output_throughput === "number") return <>{metrics.output_throughput.toFixed(1)} tok/s</>;
  if (Array.isArray(metrics?.tasks)) return <>{metrics.tasks.join(", ")}</>;
  return <>{t("unavailable")}</>;
}

export function PerformanceResults({ run }: { run: BenchmarkRun }) {
  const t = useTranslations("benchExperiment");
  const metrics = run.result?.metrics as Record<string, unknown> | undefined;
  if (!metrics) return null;
  const rows = [
    [t("ttftMedian"), metrics.median_ttft_ms, "ms"],
    [t("ttftTail"), metrics.p99_ttft_ms, "ms"],
    [t("throughput"), metrics.output_throughput, "tok/s"],
    [t("completed"), metrics.completed, "/ " + (run.params.num_prompts ?? "—")],
  ] as const;
  return <Card><CardHeader><CardTitle>{t("result")}</CardTitle></CardHeader>
    <CardContent><dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">{rows.map(([label, value, unit]) =>
      <div key={label}><dt className="text-xs text-muted-foreground">{label}</dt>
        <dd className="mt-1 font-semibold tabular-nums">{typeof value === "number" ? value.toLocaleString(undefined, { maximumFractionDigits: 1 }) + " " + unit : "—"}</dd></div>
    )}</dl></CardContent>
  </Card>;
}

export function CacheResults({ run }: { run: BenchmarkRun }) {
  const t = useTranslations("benchExperiment");
  const { individual, shared } = phasesOf(run);
  const prefix = Number(run.params.prefix_len);
  const total = Number(run.params.random_input_len);
  const rows: [string, (p: Phase) => string][] = [
    [t("ttftMedian"), p => p.median_ttft_ms != null ? p.median_ttft_ms.toFixed(1) + " ms" : "—"],
    [t("ttftTail"), p => p.p99_ttft_ms != null ? p.p99_ttft_ms.toFixed(1) + " ms" : "—"],
    [t("throughput"), p => p.output_throughput != null ? p.output_throughput.toFixed(1) + " tok/s" : "—"],
    [t("observed"), p => p.cache?.hit_rate != null ? (p.cache.hit_rate * 100).toFixed(1) + "%" : t("unavailable")],
    [t("counts"), p => p.cache?.hits != null && p.cache.queries != null ? p.cache.hits + " / " + p.cache.queries : t("unavailable")],
    [t("completed"), p => String(p.completed ?? 0) + " / " + run.params.num_prompts],
  ];
  return <Card>
    <CardHeader><CardTitle>{t("prefix_cache")}</CardTitle>
      <p className="text-sm text-muted-foreground">{t("isolated")} · {t("prefixSummary", { prefix, total, percent: Math.round(100 * prefix / total) })}</p>
    </CardHeader>
    <CardContent className="overflow-x-auto">
      <table className="w-full text-sm tabular-nums">
        <thead><tr className="border-b text-muted-foreground"><th scope="col" className="text-left py-2">{t("metric")}</th>
          <th scope="col" className="text-right">{t("individual")}</th><th scope="col" className="text-right">{t("shared")}</th></tr></thead>
        <tbody>{rows.map(([name, value]) => <tr key={name} className="border-b last:border-0">
          <th scope="row" className="py-2.5 text-left font-normal">{name}</th>
          {[individual, shared].map((p, i) => <td className="text-right" key={i}>{p ? value(p) : "—"}</td>)}
        </tr>)}</tbody>
      </table>
    </CardContent>
  </Card>;
}
