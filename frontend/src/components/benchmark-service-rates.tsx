"use client";

import { useTranslations } from "next-intl";
import type { BenchmarkRun, ServiceObjectives } from "@/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

type Rates = Partial<Record<"rpm" | "input_tpm" | "output_tpm", number>>;
type Probe = {
  target_rate: number; step: string; passed: boolean; reasons: string[];
  actual_arrivals_per_sec?: number; p99_runner_delay_ms?: number; error_percent?: number;
  all_rates?: Rates; slo_rates?: Rates; p99_ttft_ms?: number; p99_tpot_ms?: number; p99_e2el_ms?: number;
  timeouts?: number; requested?: number; completed?: number;
};
type Search = {
  confirmed: boolean; scope: string; stop_reason: string;
  best: { target_rate: number; repetitions: number; all_rates: Rates; slo_rates: Rates } | null;
  last_passing: Probe | null; probes: Probe[];
};

export function BenchmarkServiceRates({ run }: { run: BenchmarkRun }) {
  const t = useTranslations("capacityBenchmark");
  const search = run.result?.capacity_search as Search | undefined;
  const m = run.result?.metrics as {
    all_rates?: Rates; slo_rates?: Rates; requested?: number; completed?: number; error_percent?: number;
    actual_arrivals_per_sec?: number; p99_runner_delay_ms?: number; target_arrivals_per_sec?: number; timeouts?: number;
    p99_tpot_ms?: number; p99_e2el_ms?: number;
  } | undefined;
  if (!search && !m) return null;
  const fmt = (value?: number | null) => typeof value === "number"
    ? value.toLocaleString(undefined, { maximumFractionDigits: 2 }) : t("unmeasured");
  const bounds = run.params.capacity_search as {max_lag_ms?: number} | undefined;
  const objectives = run.params.slo as Partial<ServiceObjectives> | undefined;
  const scope = search?.scope === "deployment" || run.ephemeral ? "deployment" : "endpoint";
  const unit = run.params.workload_type === "agentic_replay" ? t("sessionsPerSec") : t("requestsPerSec");
  const errorPercent = m?.error_percent ?? (m?.requested && m.completed != null ? 100 * (1 - m.completed / m.requested) : undefined);
  return <Card><CardHeader><CardTitle>{t("rates")}</CardTitle><p className="text-sm text-muted-foreground">{t(scope)}</p></CardHeader>
    <CardContent className="space-y-4">
      <table className="w-full text-sm tabular-nums"><thead><tr><th aria-label={t("rates")} />
        {["rpm", "input_tpm", "output_tpm"].map(k => <th className="p-2 text-right" key={k} scope="col">{t(k)}</th>)}
      </tr></thead><tbody>{(["all_rates", "slo_rates"] as const).map(key => <tr key={key} className="border-t">
        <th scope="row" className="py-2 text-left font-medium">{t(key)}</th>
        {(["rpm", "input_tpm", "output_tpm"] as const).map(k => <td className="p-2 text-right" key={k}>{fmt(m?.[key]?.[k])}</td>)}
      </tr>)}</tbody></table>
      {objectives && <p className="text-xs text-muted-foreground">{t("objectives")} · {(["ttft_ms", "tpot_ms", "e2el_ms", "max_error_percent", "min_slo_percent"] as const).filter(k => objectives[k] != null).map(k => t(k) + " " + fmt(objectives[k])).join(" · ")}</p>}
      {m && <dl className="grid gap-2 text-sm sm:grid-cols-3">{[
        [t("error"), fmt(errorPercent)], [t("timeouts"), fmt(m.timeouts)],
        [t("tpotP99"), fmt(m.p99_tpot_ms)], [t("e2elP99"), fmt(m.p99_e2el_ms)],
        [unit + " · " + t("targetActual"), fmt(m.target_arrivals_per_sec) + " / " + fmt(m.actual_arrivals_per_sec)],
        [t("runnerLag"), fmt(m.p99_runner_delay_ms)],
      ].map(([key, value]) => <div key={key}><dt className="text-muted-foreground">{key}</dt><dd>{value}</dd></div>)}</dl>}
      {search && <><p className="text-xs text-muted-foreground">{t("offerRule", { lag: fmt(bounds?.max_lag_ms ?? 100) })}</p><p className="text-sm">
        {t("endReason")} · {t("stop_" + search.stop_reason)}{search.best && <> · {fmt(search.best.target_rate)} {unit} · {t("repeatCount", { count: search.best.repetitions })}</>}
        {!search.confirmed && search.last_passing && <> · {t("lastPassing")} {fmt(search.last_passing.target_rate)} {unit}</>}
      </p><details><summary className="cursor-pointer text-sm">{t("searchPoints")}</summary>
        <div className="overflow-x-auto"><table className="my-3 w-full text-left text-xs tabular-nums">
          <thead><tr>{[unit, t("actual"), t("runnerLag"), t("error"), t("rates"), t("decision")].map(x => <th className="p-2" key={x}>{x}</th>)}</tr></thead>
          <tbody>{search.probes.map((p, i) => <tr key={i} className="border-t">
            <td className="p-2">{fmt(p.target_rate)} · {t(p.step)}</td><td>{fmt(p.actual_arrivals_per_sec)}</td>
            <td>{fmt(p.p99_runner_delay_ms)}</td><td>{fmt(p.error_percent)}</td>
            <td>{fmt(p.slo_rates?.rpm)} RPM · {fmt(p.slo_rates?.input_tpm)} / {fmt(p.slo_rates?.output_tpm)} TPM</td>
            <td>{p.passed ? t("passed") : p.reasons.map(r => t("reason_" + r)).join(", ")}</td></tr>)}</tbody>
        </table></div></details></>}
    </CardContent></Card>;
}
