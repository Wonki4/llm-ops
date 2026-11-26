"use client";

import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { ChevronDown } from "lucide-react";

import { FormSelect, FormSelectItem } from "@/components/ui/form-select";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  useModelLatencyHistory, useModelLiveLatency, type LatencyHistoryRow, type LiveLatencyRow,
} from "@/hooks/use-api";
import {
  combineLatencySamples, formatLatency, LATENCY_HISTORY_MS, latencyAxis, latencyKey, latencyMetrics, latencyPeak, latencySegments,
  type LatencyMetric, type LatencySample, type LatencyScale, type LatencyTarget,
} from "@/lib/model-latency";

const COLORS = [
  "text-teal-700 dark:text-teal-400", "text-indigo-600 dark:text-indigo-400",
  "text-orange-700 dark:text-orange-400", "text-blue-700 dark:text-blue-400",
  "text-fuchsia-700 dark:text-fuchsia-400", "text-lime-700 dark:text-lime-400",
];
const DASHES = [undefined, "6 3", "2 3", "8 3 2 3"];

interface Series {
  target: LatencyTarget;
  label: string;
  key: string;
  live?: LiveLatencyRow;
  history?: LatencyHistoryRow;
  samples: LatencySample[];
}

function color(index: number) { return COLORS[index % COLORS.length]; }
function dash(index: number) { return DASHES[index % DASHES.length]; }

function LineKey({ index }: { index: number }) {
  return <svg aria-hidden="true" width="18" height="6" className={`shrink-0 ${color(index)}`}>
    <path d="M1 3 H17" fill="none" stroke="currentColor" strokeWidth="2.5" strokeDasharray={dash(index)} />
  </svg>;
}

function makeSeries(current: LiveLatencyRow[] | undefined, history: LatencyHistoryRow[], start: number, end: number) {
  const past = new Map(history.map((row) => [latencyKey(row), row]));
  const live = new Map(current?.map((row) => [latencyKey(row), row]));
  const rows = current ?? history;
  const clusterCounts = new Map<string, number>();
  for (const row of rows) clusterCounts.set(row.cluster_name, (clusterCounts.get(row.cluster_name) ?? 0) + 1);
  return rows.map((target): Series => {
    const key = latencyKey(target);
    const stored = past.get(key);
    const latest = live.get(key);
    const samples = (stored?.history ?? []).map((point) => ({ ...point, at: Date.parse(point.at) }));
    const label = clusterCounts.get(target.cluster_name) === 1 ? target.cluster_name
      : [target.cluster_name, target.namespace, target.deployment_name, target.engine, target.pool].filter(Boolean).join(" / ");
    return {
      target, key, label, live: latest, history: stored,
      samples: combineLatencySamples(samples, latest?.recent ?? [], start, end),
    };
  });
}

type Comparison = "latest" | "ttft" | "e2e" | number;

function LatencyFigure({ metric, series, start, end, step, loading, liveError, times, active, inspect, pin, reset }: {
  metric: LatencyMetric; series: Series[]; start: number; end: number; step: number; loading: boolean; liveError: boolean;
  times: number[]; active: number | null; inspect: (at: number | null) => void;
  pin: (at: number) => void; reset: () => void;
}) {
  const t = useTranslations("modelDetail.latency");
  const locale = useLocale();
  const segments = series.map((item) => latencySegments(item.samples, metric, step * 1.5));
  const bounds = series.map(item => item.samples.flatMap(p => {
    const bound = p.lower_bounds_ms?.[metric];
    return bound != null ? [{at: p.at, value: bound}] : [];
  }));
  const traces = segments.map((pieces, i) => [...pieces.flat(), ...bounds[i]].map(point => point.value));
  const hasValues = traces.some(trace => trace.length > 0);
  const [scaleMode, setScaleMode] = useState<LatencyScale>("auto");
  const axis = latencyAxis(traces, scaleMode);
  const x = (time: number) => 1000 * (time - start) / (end - start);
  const y = (value: number) => 1000 * (1 - axis.fraction(value));
  const nearest = (time: number) => times.reduce((closest, at) =>
    Math.abs(at - time) < Math.abs(closest - time) ? at : closest, times[0]);
  const clock = (time: number, seconds = false) => new Date(time).toLocaleTimeString(locale, {
    hour: "2-digit", minute: "2-digit", second: seconds ? "2-digit" : undefined, hour12: false,
  });
  const cursorSamples = series.map((item) => active === null ? undefined : item.samples.find((s) => s.at === active));
  const reading = (value?: number | null, bound?: number) =>
    bound != null ? t("exceedsValue", {value: formatLatency(bound, locale)})
      : value != null ? formatLatency(value, locale) : "—";
  const cursorValue = (i: number) => reading(cursorSamples[i]?.[metric], cursorSamples[i]?.lower_bounds_ms?.[metric]);
  const cursorLabel = active === null ? "" : [clock(active, true), ...series.map((s, i) =>
    `${s.label} ${cursorValue(i)}`)].join(", ");

  return <figure data-metric={metric} data-scale={axis.kind} data-compare-at={active ?? undefined} className="flex min-w-0 flex-col gap-3 rounded-lg border p-3.5 sm:p-4">
    <figcaption className="min-h-12">
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-2">
        <h4 className="text-sm font-semibold tracking-wide">{metric.toUpperCase()}</h4>
        {hasValues && <FormSelect value={scaleMode} aria-label={t("axisLabel", { metric: metric.toUpperCase() })}
          title={axis.kind === "log" ? t("logHint", { floor: formatLatency(axis.floor, locale) }) : t("linearHint")}
          className="h-6 gap-1 border-0 bg-transparent px-1 text-[11px] text-muted-foreground shadow-none"
          onValueChange={next => { if (next === "auto" || next === "linear" || next === "log") setScaleMode(next); }}>
          <FormSelectItem value="auto">{scaleMode === "auto" ? t("autoScale", { scale: t(axis.kind === "log" ? "logScale" : "linearScale") }) : t("auto")}</FormSelectItem>
          <FormSelectItem value="linear">{t("linearScale")}</FormSelectItem>
          <FormSelectItem value="log">{t("logScale")}</FormSelectItem>
        </FormSelect>}
      </div>
      <p className="mt-1 text-xs leading-snug text-muted-foreground">{t(metric)}</p>
    </figcaption>
    <div className="mt-2">
      {hasValues ? <>
        <div className="relative ms-12 h-28">
          {axis.ticks.map((value) => <div key={value} aria-hidden="true"
            className="absolute inset-x-0 border-t border-dashed border-border" style={{ top: `${y(value) / 10}%` }}>
            <span className="absolute end-full me-1.5 -translate-y-1/2 whitespace-nowrap text-[10px] text-muted-foreground">
              {formatLatency(value, locale)}
            </span>
          </div>)}
          <svg aria-hidden="true" viewBox="0 0 1000 1000" preserveAspectRatio="none"
            className="absolute inset-0 size-full overflow-visible">
            {segments.map((pieces, i) => <g key={series[i].key} className={color(i)}>
              {pieces.map((piece) => {
                const path = piece.map((p, n) => `${n ? "L" : "M"}${x(p.at).toFixed(2)},${y(p.value).toFixed(2)}`).join(" ");
                const single = piece.length === 1;
                return <path key={piece[0].at} data-cluster={series[i].target.cluster_name}
                  d={single ? path + " l0,0" : path} stroke="currentColor" fill="none" strokeLinecap="round" strokeLinejoin="round"
                  strokeWidth={single ? 4 : 2} strokeDasharray={single ? undefined : dash(i)} vectorEffect="non-scaling-stroke" />;
              })}
            </g>)}
          </svg>
          {bounds.map((points, i) => points.map(point => (
            <span key={`${series[i].key}-${point.at}`} aria-hidden="true" data-lower-bound={point.value}
              className={`pointer-events-none absolute -translate-x-1/2 -translate-y-1/2 text-[10px] font-bold ${color(i)}`}
              style={{ left: `${x(point.at) / 10}%`, top: `${y(point.value) / 10}%` }}>↑</span>
          )))}
          <div role="slider" tabIndex={0} aria-label={t("inspect", { metric: metric.toUpperCase() })}
            aria-valuemin={0} aria-valuemax={times.length - 1} aria-valuenow={active === null ? times.length - 1 : times.indexOf(active)}
            aria-valuetext={cursorLabel || clock(times[times.length - 1], true)}
            className="absolute inset-0 cursor-crosshair touch-pan-y rounded-sm focus-visible:outline-2 focus-visible:outline-ring"
            onPointerMove={(e) => {
              if (e.pointerType === "touch") return;
              const rect = e.currentTarget.getBoundingClientRect();
              inspect(nearest(start + (end - start) * (e.clientX - rect.left) / rect.width));
            }}
            onPointerDown={(e) => {
              const rect = e.currentTarget.getBoundingClientRect();
              pin(nearest(start + (end - start) * (e.clientX - rect.left) / rect.width));
            }}
            onPointerLeave={() => inspect(null)} onBlur={() => inspect(null)}
            onFocus={() => { if (active === null) inspect(times[times.length - 1]); }}
            onKeyDown={(e) => {
              const index = active === null ? times.length - 1 : times.indexOf(active);
              const next = { ArrowLeft: index - 1, ArrowRight: index + 1, Home: 0, End: times.length - 1 }[e.key];
              if (next !== undefined) { e.preventDefault(); pin(times[Math.max(0, Math.min(times.length - 1, next))]); }
              if (e.key === "Escape") reset();
            }}>
            {active !== null && <div aria-hidden="true" className="pointer-events-none absolute inset-y-0 border-s border-muted-foreground"
              style={{ left: `${x(active) / 10}%` }} />}
          </div>
          {cursorSamples.map((point, i) => {
            const value = point?.[metric];
            return !point || value == null ? null : <span key={series[i].key} aria-hidden="true"
              className={`pointer-events-none absolute size-2 -translate-x-1/2 -translate-y-1/2 rounded-full border border-background bg-current ${color(i)}`}
              style={{left: `${x(point.at) / 10}%`, top: `${y(value) / 10}%`}} />;
          })}
        </div>
        <div aria-hidden="true" className="ms-12 mt-2 flex justify-between text-[10px] tabular-nums text-muted-foreground">
          {[start, (start + end) / 2, end].map((at) => <span key={at}>{clock(at)}</span>)}
        </div>
      </> : <div className="flex h-[135px] items-center justify-center rounded-md bg-muted/35 text-center text-xs text-muted-foreground">
        {loading ? t("historyLoading") : t("emptyHistory")}
      </div>}
    </div>
    <ul aria-label={active === null ? t("latest") : t("valuesAt", {time: clock(active, true)})} className="mt-auto space-y-1.5 border-t pt-2.5 text-xs">
      {series.map((s, i) => {
        const current = s.live?.metrics[metric];
        const value = !liveError && current?.status === "ok" ? current.p50_ms : null;
        const state = liveError ? "error" : current?.status;
        return <li key={s.key} data-cluster={s.target.cluster_name} className="flex items-start justify-between gap-2">
          <span className="flex min-w-0 items-center gap-1.5"><LineKey index={i} /><span className="break-all">{s.label || t("unknownCluster")}</span></span>
          <span className={`shrink-0 text-right tabular-nums ${value == null ? "text-muted-foreground" : "font-semibold"}`}>
            {active !== null ? cursorValue(i) : state === "above_range" ? reading(null, current?.lower_bound_ms) : value != null ? formatLatency(value, locale) : state && t.has(`status.${state}`) ? t(`status.${state}`) : "—"}
          </span>
        </li>;
      })}
    </ul>
    {bounds.some(points => points.length) && <p className="text-xs text-muted-foreground">{t("boundHint")}</p>}
    {series.some((s) => s.history?.unavailable_metrics?.includes(metric)) &&
      <p className="text-xs text-muted-foreground">{t("partialHistory")}</p>}
  </figure>;
}

export function ModelLiveLatency({ modelName }: { modelName: string }) {
  const t = useTranslations("modelDetail.latency");
  const locale = useLocale();
  const live = useModelLiveLatency(modelName);
  const history = useModelLatencyHistory(modelName);
  const end = Math.max(Date.parse(history.data?.end_at ?? "") || 0, Date.parse(live.data?.evaluated_at ?? "") || live.dataUpdatedAt);
  const duration = history.data?.history_seconds ? history.data.history_seconds * 1000 : LATENCY_HISTORY_MS;
  const series = makeSeries(live.data?.rows, history.data?.rows ?? [], end - duration, end);
  const [comparison, setComparison] = useState<Comparison>("latest");
  const [hoveredAt, inspect] = useState<number | null>(null);
  const times = [...new Set(series.flatMap(item => item.samples.map(point => point.at)))].sort((a, b) => a - b);
  const peaks = { ttft: latencyPeak(series, "ttft"), e2e: latencyPeak(series, "e2e") };
  const pinned = typeof comparison === "number" ? comparison : comparison === "latest" ? null : peaks[comparison]?.at;
  const selectedAt = hoveredAt ?? pinned;
  const active = selectedAt != null && times.includes(selectedAt) ? selectedAt : null;
  const select = (value: Comparison) => { inspect(null); setComparison(value); };
  const stamps = series.flatMap((s) => s.live?.last_scraped_at ? [s.live.last_scraped_at] : []);
  const latest = stamps.length ? stamps.reduce((a, b) => Date.parse(a) > Date.parse(b) ? a : b) : null;
  const formatStamp = (at: string) => new Date(at).toLocaleTimeString(locale, { hour12: false });

  return <Card className="gap-4">
    <CardHeader className="px-3.5 sm:px-6">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <CardTitle className="text-base">{t("title")}</CardTitle>
        {latest && <span className="text-xs text-muted-foreground">
          {t("scraped")} <time dateTime={latest} className="tabular-nums">{formatStamp(latest)}</time>
        </span>}
      </div>
      <CardDescription>{t("description", {
        duration: duration / 60000, minutes: (live.data?.window_seconds ?? 300) / 60, seconds: live.data?.refresh_seconds ?? 15,
      })}</CardDescription>
    </CardHeader>
    <CardContent className="space-y-3 px-3.5 sm:px-6">
      {live.isError && <p role="status" className="text-sm text-destructive">{t("queryError")}</p>}
      {history.isError && <p role="status" className="text-sm text-muted-foreground">{t("historyError")}</p>}
      {series.length ? <>
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 text-xs">
          <div role="group" aria-label={t("compareTime")} className="inline-flex flex-wrap gap-1 rounded-md border p-1">
            {(["latest", "ttft", "e2e"] as const).map(mode => {
              const peak = mode === "latest" ? null : peaks[mode];
              const label = mode === "latest" ? t("livePoint") : t(peak?.aboveRange ? "rangePoint" : "peakPoint", {metric: mode.toUpperCase()});
              return <button key={mode} type="button" aria-pressed={comparison === mode && hoveredAt === null}
                disabled={mode !== "latest" && !peak} onClick={() => select(mode)}
                className="rounded px-2.5 py-1 font-medium text-muted-foreground hover:bg-muted focus-visible:outline-ring disabled:opacity-45 aria-pressed:bg-secondary aria-pressed:text-foreground">
                {label}
              </button>;
            })}
          </div>
          {active !== null && <span className="text-muted-foreground">{t("compareTime")}{" "}
            <time data-comparison-time dateTime={new Date(active).toISOString()} className="font-medium tabular-nums text-foreground">
              {new Date(active).toLocaleTimeString(locale, {hour12: false})}
            </time></span>}
        </div>
        <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,15rem),1fr))] gap-3">
          {latencyMetrics.map((metric) => <LatencyFigure key={metric} metric={metric} series={series} start={end-duration} end={end}
            step={(history.data?.step_seconds ?? 30) * 1000} loading={history.isPending} liveError={live.isError}
            times={times} active={active} inspect={inspect} pin={select} reset={() => select("latest")} />)}
        </div>
        <details className="group text-xs text-muted-foreground">
          <summary className="inline-flex cursor-pointer list-none items-center gap-1.5 py-1 font-medium text-foreground">
            <ChevronDown aria-hidden="true" className="size-3.5 transition-transform group-open:rotate-180" />{t("sources")}
          </summary>
          <p className="my-1.5">{t("coverage")}</p>
          <ul className="space-y-1">{series.map((s, i) => <li key={s.key} className="flex flex-wrap items-center gap-x-2">
            <LineKey index={i} /><span className="break-all">{[s.target.cluster_name, s.target.namespace, s.target.deployment_name,
              s.target.engine, s.target.pool].filter(Boolean).join(" / ")}</span>
            {s.target.shared && <span>{t("shared")}</span>}
            {s.live?.last_scraped_at && <time dateTime={s.live.last_scraped_at}>{formatStamp(s.live.last_scraped_at)}</time>}
          </li>)}</ul>
        </details>
      </> : live.isPending ? <p role="status" className="text-sm text-muted-foreground">{t("loading")}</p>
        : !live.isError && <p className="text-sm text-muted-foreground">{t("noDeployment")}</p>}
      {!!series.length && !!live.data?.unmapped_count && <p className="text-xs text-muted-foreground">
        {t("unmappedConnections", { count: live.data.unmapped_count })}
      </p>}
    </CardContent>
  </Card>;
}
