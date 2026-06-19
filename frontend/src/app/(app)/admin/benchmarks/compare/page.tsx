"use client";

import { Suspense } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ArrowLeft, Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";

import { useBenchmarkBulk } from "@/hooks/use-api";
import { useLocaleTag } from "@/lib/locale";
import type { BenchmarkRun, ServingSnapshot } from "@/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

const STATUS_STYLES: Record<BenchmarkRun["status"], string> = {
  provisioning: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
  pending: "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300",
  running: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
  succeeded: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400",
  failed: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",
  cancelled: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400",
};

// Perf metrics with direction. "higher" = bigger is better, "lower" = smaller is better.
type Direction = "higher" | "lower";
type MetricSpec = { key: string; path: (string | number)[]; direction: Direction };

// Keys emitted by `vllm bench serve` result.json (throughput in req|tok/s,
// latencies in ms). Flat schema — one path segment each.
const PERF_METRICS: MetricSpec[] = [
  { key: "request_throughput", path: ["request_throughput"], direction: "higher" },
  { key: "output_throughput", path: ["output_throughput"], direction: "higher" },
  { key: "total_token_throughput", path: ["total_token_throughput"], direction: "higher" },
  { key: "completed", path: ["completed"], direction: "higher" },
  { key: "total_output_tokens", path: ["total_output_tokens"], direction: "higher" },
  { key: "duration", path: ["duration"], direction: "lower" },
  { key: "mean_ttft_ms", path: ["mean_ttft_ms"], direction: "lower" },
  { key: "median_ttft_ms", path: ["median_ttft_ms"], direction: "lower" },
  { key: "p99_ttft_ms", path: ["p99_ttft_ms"], direction: "lower" },
  { key: "mean_tpot_ms", path: ["mean_tpot_ms"], direction: "lower" },
  { key: "median_tpot_ms", path: ["median_tpot_ms"], direction: "lower" },
  { key: "p99_tpot_ms", path: ["p99_tpot_ms"], direction: "lower" },
  { key: "mean_itl_ms", path: ["mean_itl_ms"], direction: "lower" },
  { key: "p99_itl_ms", path: ["p99_itl_ms"], direction: "lower" },
  { key: "mean_e2el_ms", path: ["mean_e2el_ms"], direction: "lower" },
  { key: "p99_e2el_ms", path: ["p99_e2el_ms"], direction: "lower" },
];

// Serving config attributes shown side-by-side (the "same resources" axis).
const SERVING_ATTRS: { key: string; get: (s: ServingSnapshot) => string }[] = [
  { key: "engine", get: (s) => s.engine },
  { key: "image", get: (s) => s.image },
  { key: "model_path", get: (s) => s.model_path },
  {
    key: "gpu",
    get: (s) =>
      `${s.resources.gpu_count}× ${s.node_selector?.["gpu-type"] ?? s.resources.gpu_resource_key}`,
  },
  { key: "cpu", get: (s) => s.resources.cpu_limit ?? s.resources.cpu_request ?? "-" },
  { key: "memory", get: (s) => s.resources.memory_limit ?? s.resources.memory_request ?? "-" },
  { key: "replicas", get: (s) => String(s.replicas) },
  { key: "vllm_args", get: (s) => (s.vllm_extra_args || []).join(" ") || "-" },
];

function getAt(obj: unknown, path: (string | number)[]): unknown {
  let cur: unknown = obj;
  for (const k of path) {
    if (cur == null || typeof cur !== "object") return undefined;
    cur = (cur as Record<string, unknown>)[k as string];
  }
  return cur;
}

function fmt(value: unknown): string {
  if (value == null) return "-";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return String(value);
    if (Number.isInteger(value)) return value.toString();
    return value.toFixed(4);
  }
  return String(value);
}

function pickBestWorst(
  values: (number | null)[],
  direction: Direction,
): { bestIdx: number | null; worstIdx: number | null } {
  const nums = values
    .map((v, i) => ({ v, i }))
    .filter((x): x is { v: number; i: number } => x.v !== null && Number.isFinite(x.v));
  if (nums.length < 2) return { bestIdx: null, worstIdx: null };
  const best = nums.reduce((acc, x) =>
    direction === "higher" ? (x.v > acc.v ? x : acc) : x.v < acc.v ? x : acc,
  );
  const worst = nums.reduce((acc, x) =>
    direction === "higher" ? (x.v < acc.v ? x : acc) : x.v > acc.v ? x : acc,
  );
  if (best.i === worst.i) return { bestIdx: null, worstIdx: null };
  return { bestIdx: best.i, worstIdx: worst.i };
}

export default function CompareBenchmarksPage() {
  return (
    <Suspense
      fallback={
        <div className="flex items-center justify-center py-20">
          <Loader2 className="size-6 animate-spin text-muted-foreground" />
        </div>
      }
    >
      <CompareInner />
    </Suspense>
  );
}

function CompareInner() {
  const t = useTranslations("benchmarkCompare");
  const ts = useTranslations("benchmarkStatus");
  const localeTag = useLocaleTag();
  const sp = useSearchParams();
  const idsParam = sp.get("ids") ?? "";
  const ids = idsParam.split(",").map((s) => s.trim()).filter(Boolean);

  const { runs, isLoading } = useBenchmarkBulk(ids);

  const formatDateTime = (s: string | null) =>
    s
      ? new Date(s).toLocaleString(localeTag, {
          year: "numeric",
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
        })
      : "-";

  if (ids.length < 2) {
    return (
      <div className="space-y-4">
        <Link
          href="/admin/benchmarks"
          className="text-sm text-muted-foreground hover:text-foreground inline-flex items-center gap-1"
        >
          <ArrowLeft className="size-3.5" />
          {t("backToList")}
        </Link>
        <p className="text-sm text-muted-foreground">{t("needAtLeastTwo")}</p>
      </div>
    );
  }

  if (isLoading || runs.length < ids.length) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  // Sort runs to match the order from the URL.
  const runsById = new Map(runs.map((r) => [r.id, r]));
  const ordered: BenchmarkRun[] = ids
    .map((id) => runsById.get(id))
    .filter((r): r is BenchmarkRun => !!r);

  const allPerf = ordered.every((r) => r.kind === "performance");
  const allAcc = ordered.every((r) => r.kind === "accuracy");
  const mixed = !allPerf && !allAcc;

  // For accuracy we union metric keys across runs (tasks differ per run).
  const accuracyMetricKeys: string[] = allAcc
    ? Array.from(
        new Set(
          ordered.flatMap((r) => {
            const metrics = (r.result as Record<string, unknown> | null)?.metrics;
            if (!metrics || typeof metrics !== "object") return [];
            // lm-eval emits {"results": {"task": {"metric_name": value, ...}}}
            const results = (metrics as Record<string, unknown>).results;
            if (!results || typeof results !== "object") return Object.keys(metrics);
            const keys: string[] = [];
            for (const [task, tm] of Object.entries(results as Record<string, unknown>)) {
              if (tm && typeof tm === "object") {
                for (const m of Object.keys(tm as Record<string, unknown>)) {
                  keys.push(`${task}.${m}`);
                }
              }
            }
            return keys;
          }),
        ),
      ).sort()
    : [];

  // All params keys union (so we can show every param across all runs).
  const allParamKeys = Array.from(
    new Set(ordered.flatMap((r) => Object.keys(r.params ?? {}))),
  ).sort();

  return (
    <div className="space-y-6">
      <div>
        <Link
          href="/admin/benchmarks"
          className="text-sm text-muted-foreground hover:text-foreground inline-flex items-center gap-1"
        >
          <ArrowLeft className="size-3.5" />
          {t("backToList")}
        </Link>
        <h1 className="text-2xl font-bold mt-2">{t("pageTitle")}</h1>
        <p className="text-muted-foreground mt-1">
          {t("pageDescription", { count: ordered.length })}
        </p>
      </div>

      {mixed && (
        <div className="rounded-md border border-yellow-300 bg-yellow-50 dark:bg-yellow-900/20 dark:border-yellow-700 px-3 py-2 text-sm text-yellow-800 dark:text-yellow-200">
          {t("mixedKindsWarning")}
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("runs")}</CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b">
                <th className="text-left py-2 pr-4 font-medium text-muted-foreground w-40">
                  {t("attribute")}
                </th>
                {ordered.map((r) => (
                  <th key={r.id} className="text-left py-2 px-3 font-medium align-bottom">
                    <Link
                      href={`/admin/benchmarks/${r.id}`}
                      className="hover:underline"
                    >
                      {r.model_name}
                    </Link>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              <Row label={t("status")}>
                {ordered.map((r) => (
                  <Cell key={r.id}>
                    <Badge className={STATUS_STYLES[r.status]}>{ts(r.status)}</Badge>
                  </Cell>
                ))}
              </Row>
              <Row label={t("tool")}>
                {ordered.map((r) => (
                  <Cell key={r.id}>
                    <span className="font-mono text-xs">{r.tool}</span>
                  </Cell>
                ))}
              </Row>
              <Row label={t("kind")}>
                {ordered.map((r) => (
                  <Cell key={r.id}>{r.kind}</Cell>
                ))}
              </Row>
              <Row label={t("createdAt")}>
                {ordered.map((r) => (
                  <Cell key={r.id}>{formatDateTime(r.created_at)}</Cell>
                ))}
              </Row>
              <Row label={t("createdBy")}>
                {ordered.map((r) => (
                  <Cell key={r.id}>{r.created_by}</Cell>
                ))}
              </Row>
            </tbody>
          </table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("params")}</CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b">
                <th className="text-left py-2 pr-4 font-medium text-muted-foreground w-40">
                  {t("param")}
                </th>
                {ordered.map((r) => (
                  <th key={r.id} className="text-left py-2 px-3 font-medium font-mono text-xs">
                    {r.model_name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {allParamKeys.map((key) => {
                const values = ordered.map((r) => (r.params ?? {})[key]);
                const allSame = values.every(
                  (v) => JSON.stringify(v) === JSON.stringify(values[0]),
                );
                return (
                  <Row key={key} label={<span className="font-mono text-xs">{key}</span>}>
                    {ordered.map((r, i) => (
                      <Cell key={r.id}>
                        <span
                          className={cn(
                            "font-mono text-xs break-all",
                            !allSame && "font-semibold",
                          )}
                        >
                          {values[i] === undefined ? "-" : JSON.stringify(values[i])}
                        </span>
                      </Cell>
                    ))}
                  </Row>
                );
              })}
            </tbody>
          </table>
        </CardContent>
      </Card>

      {ordered.some((r) => r.serving_snapshot) && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t("serving")}</CardTitle>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b">
                  <th className="text-left py-2 pr-4 font-medium text-muted-foreground w-40">
                    {t("attribute")}
                  </th>
                  {ordered.map((r) => (
                    <th key={r.id} className="text-left py-2 px-3 font-medium font-mono text-xs">
                      {r.model_name}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {SERVING_ATTRS.map(({ key, get }) => {
                  const values = ordered.map((r) =>
                    r.serving_snapshot ? get(r.serving_snapshot) : "-",
                  );
                  const allSame = values.every((v) => v === values[0]);
                  return (
                    <Row key={key} label={<span className="font-mono text-xs">{key}</span>}>
                      {ordered.map((r, i) => (
                        <Cell key={r.id}>
                          <span
                            className={cn(
                              "font-mono text-xs break-all",
                              !allSame && "font-semibold text-primary",
                            )}
                          >
                            {values[i]}
                          </span>
                        </Cell>
                      ))}
                    </Row>
                  );
                })}
              </tbody>
            </table>
            <p className="text-xs text-muted-foreground mt-3">{t("servingLegend")}</p>
          </CardContent>
        </Card>
      )}

      {allPerf && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t("perfMetrics")}</CardTitle>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b">
                  <th className="text-left py-2 pr-4 font-medium text-muted-foreground w-44">
                    {t("metric")}
                  </th>
                  {ordered.map((r) => (
                    <th key={r.id} className="text-left py-2 px-3 font-medium font-mono text-xs">
                      {r.model_name}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {PERF_METRICS.map(({ key, path, direction }) => {
                  const values = ordered.map((r) => {
                    const metrics = (r.result as { metrics?: unknown } | null)?.metrics ?? null;
                    const v = getAt(metrics, path);
                    return typeof v === "number" ? v : null;
                  });
                  const { bestIdx, worstIdx } = pickBestWorst(values, direction);
                  return (
                    <Row key={key} label={<span className="font-mono text-xs">{key}</span>}>
                      {values.map((v, i) => (
                        <Cell key={ordered[i].id}>
                          <span
                            className={cn(
                              "font-mono text-xs",
                              i === bestIdx &&
                                "text-green-700 dark:text-green-400 font-semibold",
                              i === worstIdx &&
                                "text-red-700 dark:text-red-400 font-semibold",
                            )}
                          >
                            {fmt(v)}
                          </span>
                        </Cell>
                      ))}
                    </Row>
                  );
                })}
              </tbody>
            </table>
            <p className="text-xs text-muted-foreground mt-3">{t("perfLegend")}</p>
          </CardContent>
        </Card>
      )}

      {allAcc && accuracyMetricKeys.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t("accuracyMetrics")}</CardTitle>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b">
                  <th className="text-left py-2 pr-4 font-medium text-muted-foreground w-44">
                    {t("metric")}
                  </th>
                  {ordered.map((r) => (
                    <th key={r.id} className="text-left py-2 px-3 font-medium font-mono text-xs">
                      {r.model_name}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {accuracyMetricKeys.map((mk) => {
                  const [task, metric] = mk.split(".");
                  const values = ordered.map((r) => {
                    const metrics = (r.result as Record<string, unknown> | null)?.metrics;
                    if (!metrics || typeof metrics !== "object") return null;
                    const results = (metrics as Record<string, unknown>).results;
                    if (results && typeof results === "object") {
                      const tm = (results as Record<string, unknown>)[task];
                      if (tm && typeof tm === "object") {
                        const v = (tm as Record<string, unknown>)[metric];
                        return typeof v === "number" ? v : null;
                      }
                      return null;
                    }
                    // Fallback: flat metrics object
                    const v = (metrics as Record<string, unknown>)[mk];
                    return typeof v === "number" ? v : null;
                  });
                  // Higher-is-better assumption for accuracy.
                  const { bestIdx, worstIdx } = pickBestWorst(values, "higher");
                  return (
                    <Row key={mk} label={<span className="font-mono text-xs">{mk}</span>}>
                      {values.map((v, i) => (
                        <Cell key={ordered[i].id}>
                          <span
                            className={cn(
                              "font-mono text-xs",
                              i === bestIdx &&
                                "text-green-700 dark:text-green-400 font-semibold",
                              i === worstIdx &&
                                "text-red-700 dark:text-red-400 font-semibold",
                            )}
                          >
                            {fmt(v)}
                          </span>
                        </Cell>
                      ))}
                    </Row>
                  );
                })}
              </tbody>
            </table>
            <p className="text-xs text-muted-foreground mt-3">
              {t("accuracyLegend")}
            </p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function Row({
  label,
  children,
}: {
  label: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <tr className="border-b last:border-0 hover:bg-muted/40">
      <td className="py-2 pr-4 text-muted-foreground align-top">{label}</td>
      {children}
    </tr>
  );
}

function Cell({ children }: { children: React.ReactNode }) {
  return <td className="py-2 px-3 align-top">{children}</td>;
}
