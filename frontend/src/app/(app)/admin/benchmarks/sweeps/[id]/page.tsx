"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, Loader2, OctagonX } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import { useBenchmarkSweep, useCancelBenchmarkSweep } from "@/hooks/use-api";
import { getAt, fmt, pickBestWorst, type MetricSpec } from "@/lib/bench-metrics";
import type { BenchmarkRun } from "@/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

const STATUS_STYLES: Record<BenchmarkRun["status"], string> = {
  provisioning: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
  queued: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  pending: "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300",
  running: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
  succeeded: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400",
  failed: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",
  cancelled: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400",
};

// The sweep table's headline metrics (subset of PERF_METRICS, same key/paths).
const SWEEP_METRICS: MetricSpec[] = [
  { key: "p99_ttft_ms", path: ["p99_ttft_ms"], direction: "lower" },
  { key: "p99_tpot_ms", path: ["p99_tpot_ms"], direction: "lower" },
  { key: "output_throughput", path: ["output_throughput"], direction: "higher" },
  { key: "request_throughput", path: ["request_throughput"], direction: "higher" },
  { key: "completed", path: ["completed"], direction: "higher" },
  { key: "duration", path: ["duration"], direction: "lower" },
];

function metricValue(run: BenchmarkRun, spec: MetricSpec): number | null {
  const metrics = (run.result as { metrics?: Record<string, unknown> } | null)?.metrics ?? null;
  const v = getAt(metrics, spec.path);
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function comboLabel(run: BenchmarkRun): string {
  const combo = run.sweep_combo ?? {};
  return Object.entries(combo)
    .map(([flag, value]) => `${flag}=${value}`)
    .join("  ");
}

export default function SweepDetailPage() {
  const t = useTranslations("benchmarkSweeps");
  const ts = useTranslations("benchmarkStatus");
  const params = useParams();
  const id = String(params.id);
  const { data: sweep, isLoading } = useBenchmarkSweep(id);
  const cancelMut = useCancelBenchmarkSweep();

  if (isLoading || !sweep) {
    return (
      <div className="flex justify-center py-16">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const runs = sweep.runs ?? [];
  const bestWorst = SWEEP_METRICS.map((spec) =>
    pickBestWorst(runs.map((r) => metricValue(r, spec)), spec.direction),
  );

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <Link
            href="/admin/benchmarks"
            className="text-sm text-muted-foreground hover:text-foreground inline-flex items-center gap-1"
          >
            <ArrowLeft className="size-3.5" />
            {t("backToList")}
          </Link>
          <h1 className="text-2xl font-bold mt-2">
            {t("detailTitle")}: {sweep.name || comboHeader(sweep.variables)}
          </h1>
          <p className="text-muted-foreground mt-1 text-sm">
            {t("colPreset")}: <span className="font-mono">{sweep.preset}</span>
            {" · "}
            {sweep.external_source
              ? `${sweep.external_source.deployment_name} (${t("external")})`
              : sweep.deployment_id}
          </p>
        </div>
        {sweep.status === "running" && (
          <Button
            variant="destructive"
            disabled={cancelMut.isPending}
            onClick={() =>
              cancelMut.mutate(sweep.id, { onSuccess: () => toast.success(t("cancelSuccess")) })
            }
          >
            <OctagonX className="size-4 mr-1" />
            {t("cancelSweep")}
          </Button>
        )}
      </div>

      <div className="rounded-lg border overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("colCombo")}</TableHead>
              <TableHead>{t("colStatus")}</TableHead>
              {SWEEP_METRICS.map((m) => (
                <TableHead key={m.key} className="text-right font-mono text-xs">
                  {m.key}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {runs.map((run) => (
              <TableRow key={run.id}>
                <TableCell className="font-mono text-xs">
                  <Link href={`/admin/benchmarks/${run.id}`} className="hover:underline">
                    {comboLabel(run) || `#${run.sweep_index}`}
                  </Link>
                  {run.status === "failed" && run.error_message && (
                    <p className="text-xs text-destructive mt-1 max-w-[320px] truncate">
                      {run.error_message}
                    </p>
                  )}
                </TableCell>
                <TableCell>
                  <Badge className={STATUS_STYLES[run.status]}>{ts(run.status)}</Badge>
                </TableCell>
                {SWEEP_METRICS.map((spec, mi) => {
                  const v = metricValue(run, spec);
                  const runIdx = runs.indexOf(run);
                  const { bestIdx, worstIdx } = bestWorst[mi];
                  return (
                    <TableCell
                      key={spec.key}
                      className={cn(
                        "text-right font-mono text-xs",
                        runIdx === bestIdx && "text-green-700 dark:text-green-400 font-semibold",
                        runIdx === worstIdx && "text-red-700 dark:text-red-400",
                      )}
                    >
                      {fmt(v)}
                    </TableCell>
                  );
                })}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

function comboHeader(variables: { flag: string }[]): string {
  return variables.map((v) => v.flag).join(" × ");
}
