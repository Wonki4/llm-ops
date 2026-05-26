"use client";

import { use } from "react";
import Link from "next/link";
import { Loader2, ArrowLeft, Ban } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import { useLocaleTag } from "@/lib/locale";
import { useBenchmark, useCancelBenchmark } from "@/hooks/use-api";
import type { BenchmarkRun } from "@/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";

const STATUS_STYLES: Record<BenchmarkRun["status"], string> = {
  pending: "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300",
  running: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
  succeeded: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400",
  failed: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",
  cancelled: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400",
};

function formatDateTime(dateStr: string | null, localeTag: string): string {
  if (!dateStr) return "-";
  return new Date(dateStr).toLocaleString(localeTag, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function formatDuration(start: string | null, end: string | null): string {
  if (!start) return "-";
  const startMs = new Date(start).getTime();
  const endMs = end ? new Date(end).getTime() : Date.now();
  const sec = Math.max(0, Math.round((endMs - startMs) / 1000));
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}m ${s}s`;
}

function JsonBlock({ value }: { value: unknown }) {
  if (value == null) {
    return <p className="text-sm text-muted-foreground">-</p>;
  }
  return (
    <pre className="text-xs bg-muted/50 rounded-md p-3 overflow-x-auto whitespace-pre-wrap break-words">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

export default function AdminBenchmarkDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const t = useTranslations("adminBenchmarks");
  const ts = useTranslations("benchmarkStatus");
  const localeTag = useLocaleTag();

  const { data: run, isLoading } = useBenchmark(id);
  const cancelMutation = useCancelBenchmark();

  const canCancel = run && (run.status === "pending" || run.status === "running");

  const handleCancel = () => {
    if (!run) return;
    if (!confirm(t("confirmCancel"))) return;
    cancelMutation.mutate(run.id, {
      onSuccess: () => toast.success(t("cancelSuccess")),
      onError: (err) =>
        toast.error(err instanceof Error ? err.message : t("cancelFail")),
    });
  };

  if (isLoading || !run) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

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
          <h1 className="text-2xl font-bold mt-2 flex items-center gap-3">
            {run.model_name}
            <Badge className={STATUS_STYLES[run.status]}>{ts(run.status)}</Badge>
          </h1>
          <p className="text-sm text-muted-foreground mt-1 font-mono">
            {run.tool} · {run.kind} · {run.id}
          </p>
        </div>
        {canCancel && (
          <Button
            variant="outline"
            size="sm"
            onClick={handleCancel}
            disabled={cancelMutation.isPending}
          >
            <Ban className="size-4 mr-1" />
            {t("cancel")}
          </Button>
        )}
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("overview")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          <Row label={t("createdBy")} value={run.created_by} />
          <Row
            label={t("createdAt")}
            value={formatDateTime(run.created_at, localeTag)}
          />
          <Row
            label={t("startedAt")}
            value={formatDateTime(run.started_at, localeTag)}
          />
          <Row
            label={t("finishedAt")}
            value={formatDateTime(run.finished_at, localeTag)}
          />
          <Row
            label={t("duration")}
            value={formatDuration(run.started_at, run.finished_at)}
          />
          <Separator />
          <Row label={t("k8sJob")} value={run.k8s_job_name ?? "-"} mono />
          <Row label={t("k8sNamespace")} value={run.k8s_namespace ?? "-"} mono />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("params")}</CardTitle>
        </CardHeader>
        <CardContent>
          <JsonBlock value={run.params} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("result")}</CardTitle>
        </CardHeader>
        <CardContent>
          {run.status === "pending" || run.status === "running" ? (
            <p className="text-sm text-muted-foreground inline-flex items-center gap-2">
              <Loader2 className="size-3.5 animate-spin" />
              {t("pendingResult")}
            </p>
          ) : (
            <JsonBlock value={run.result} />
          )}
        </CardContent>
      </Card>

      {run.error_message && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base text-red-600 dark:text-red-400">
              {t("error")}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <pre className="text-xs bg-red-50 dark:bg-red-900/20 text-red-800 dark:text-red-300 rounded-md p-3 overflow-x-auto whitespace-pre-wrap break-words">
              {run.error_message}
            </pre>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function Row({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex justify-between gap-3">
      <span className="text-muted-foreground">{label}</span>
      <span className={mono ? "font-mono text-xs break-all" : "text-right break-all"}>
        {value}
      </span>
    </div>
  );
}
