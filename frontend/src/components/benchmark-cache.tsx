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
  const t = useTranslations("benchExperiment"), s = useTranslations("benchmarkSimple");
  const metrics = run.result?.metrics as Record<string, number> | undefined;
  if (!metrics) return null;
  const agentic = run.params.workload_type === "agentic_replay";
  const cache = (run.result?.metrics as { cache?: { hit_rate?: number | null; hits?: number; queries?: number } })?.cache;
  const fmt = (v: unknown, unit = "") => typeof v === "number" ? v.toLocaleString(undefined, { maximumFractionDigits: 1 }) + unit : t("unavailable");
  const rows = [
    [t("throughput"), fmt(metrics.output_throughput, " tok/s")],
    [t("ttftMedian"), fmt(metrics.median_ttft_ms, " ms")],
    [t("ttftTail"), fmt(metrics.p99_ttft_ms, " ms")],
    [t("completed"), `${metrics.completed ?? 0} / ${metrics.requested ?? run.params.num_prompts ?? "—"}`],
    ...(agentic ? [[s("completedSessions"), `${metrics.completed_sessions ?? 0} / ${metrics.requested_sessions ?? run.params.num_sessions}`]] : [[s("ratio"), fmt(run.params.cache_ratio, "%")]]),
    [t("observed"), cache?.hit_rate == null ? t("unavailable") : fmt(cache.hit_rate * 100, "%")],
  ];
  const turns = (run.result?.turns ?? []) as Record<string, number>[];
  const sessions = (run.result?.sessions ?? []) as { session: number; completed: boolean; duration_ms: number }[];
  return <Card><CardHeader><CardTitle>{t("result")}</CardTitle><p className="text-sm text-muted-foreground">{run.ephemeral ? t("isolated") : s("mode_direct")}{agentic ? ` · ${s("agentic_replay")}` : ""}</p></CardHeader>
    <CardContent className="space-y-4"><dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">{rows.map(([label, value]) => <div key={label}><dt className="text-xs text-muted-foreground">{label}</dt><dd className="mt-1 font-semibold tabular-nums">{value}</dd></div>)}</dl>
      {!!metrics.failed && <p className="text-sm text-destructive">{s("failedCalls", { failed: metrics.failed, skipped: metrics.skipped ?? 0 })}</p>}
      {(run.result?.input as { token_input?: boolean } | undefined)?.token_input === false && <p className="text-xs text-muted-foreground">{s((run.result?.input as { length_mode?: string })?.length_mode === "text_estimate" ? "estimatedInputHint" : "actualTokensHint")}</p>}
      {!!turns.length && <details><summary className="cursor-pointer text-sm">{s("turnDetails")}</summary><div className="mt-3 overflow-x-auto"><table className="w-full text-sm tabular-nums"><thead><tr className="border-b">{[s("turn"), s("inputTokens"), t("ttftTail"), t("throughput"), t("completed"), s("failedSkipped")].map(x => <th className="p-2 text-left" key={x}>{x}</th>)}</tr></thead><tbody>{turns.map(turn => <tr key={turn.turn} className="border-b"><td className="p-2">{turn.turn}</td><td>{turn.completed ? fmt(turn.total_input_tokens / turn.completed) : "—"}</td><td>{fmt(turn.p99_ttft_ms, " ms")}</td><td>{fmt(turn.output_throughput, " tok/s")}</td><td>{turn.completed} / {turn.requested}</td><td>{turn.failed} / {turn.skipped}</td></tr>)}</tbody></table></div><p className="my-3 text-xs text-muted-foreground">{s("turnRateHint")}</p><div className="max-h-64 overflow-y-auto text-sm">{sessions.map(session => <p key={session.session}>{s("sessionTime", { session: session.session })}: {fmt(session.duration_ms, " ms")} · {session.completed ? s("complete") : s("incomplete")}</p>)}</div></details>}
    </CardContent></Card>;
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
