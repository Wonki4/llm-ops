"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { Search, Loader2, FlaskConical, X, Plus, GitCompare, Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { useLocaleTag, parseServerDate } from "@/lib/locale";
import { useBenchmarks, useDeleteBenchmark } from "@/hooks/use-api";
import { experimentOf, ResultSummary } from "@/components/benchmark-cache";
import type { BenchmarkRun } from "@/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

const COMPARE_MAX = 5;
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const STATUS_STYLES: Record<BenchmarkRun["status"], string> = {
  provisioning: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
  pending: "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300",
  running: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
  succeeded: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400",
  failed: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",
  cancelled: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400",
};

const EXPERIMENTS = ["prefix_cache", "performance", "accuracy"] as const;

const STATUS_OPTIONS: BenchmarkRun["status"][] = [
  "provisioning",
  "pending",
  "running",
  "succeeded",
  "failed",
  "cancelled",
];

function formatDateTime(dateStr: string | null, localeTag: string): string {
  if (!dateStr) return "-";
  return parseServerDate(dateStr).toLocaleString(localeTag, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function AdminBenchmarksPage() {
  const t = useTranslations("adminBenchmarks");
  const ts = useTranslations("benchmarkStatus");
  const te = useTranslations("benchExperiment");
  const localeTag = useLocaleTag();

  const [modelInput, setModelInput] = useState("");
  const [model, setModel] = useState("");
  const [experiment, setExperiment] = useState<string>("");
  const [status, setStatus] = useState<string>("");
  const [selected, setSelected] = useState<Set<string>>(new Set());

  useEffect(() => {
    const timer = setTimeout(() => setModel(modelInput.trim()), 300);
    return () => clearTimeout(timer);
  }, [modelInput]);

  const { data: runs, isLoading } = useBenchmarks({
    model_name: model || undefined,
    experiment: experiment || undefined,
    status: status || undefined,
    limit: 200,
  });

  const toggleSelected = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else if (next.size < COMPARE_MAX) {
        next.add(id);
      }
      return next;
    });
  };
  const deleteMut = useDeleteBenchmark();
  const handleDelete = (r: BenchmarkRun) => {
    if (!confirm(t("confirmDelete", { name: r.label || r.model_name }))) return;
    deleteMut.mutate(r.id, {
      onSuccess: () => {
        setSelected(prev => { const next = new Set(prev); next.delete(r.id); return next; });
        toast.success(t("deleteSuccess"));
      },
      onError: (e) => toast.error(e instanceof Error ? e.message : t("deleteFail")),
    });
  };
  const compareHref = `/admin/benchmarks/compare?ids=${Array.from(selected).join(",")}`;

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">{t("pageTitle")}</h1>
          <p className="text-muted-foreground mt-1">{t("pageDescription")}</p>
        </div>
        <div className="flex items-center gap-2">
          {selected.size >= 2 && (
            <Link href={compareHref}>
              <Button variant="outline">
                <GitCompare className="size-4 mr-1" />
                {t("compareSelected", { count: selected.size })}
              </Button>
            </Link>
          )}
          <Link href="/admin/benchmarks/new">
            <Button>
              <Plus className="size-4 mr-1" />
              {t("runBenchmark")}
            </Button>
          </Link>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[240px] max-w-sm">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" />
          <Input
            placeholder={t("searchPlaceholder")}
            value={modelInput}
            onChange={(e) => setModelInput(e.target.value)}
            className="pl-8 h-9"
          />
        </div>
        <select
          className="h-9 rounded-md border border-input bg-background px-2 text-sm"
          aria-label={te("experiment")}
          value={experiment}
          onChange={(e) => setExperiment(e.target.value)}
        >
          <option value="">{te("allExperiments")}</option>
          {EXPERIMENTS.map((value) => (
            <option key={value} value={value}>
              {te(value)}
            </option>
          ))}
        </select>
        <select
          className="h-9 rounded-md border border-input bg-background px-2 text-sm"
          value={status}
          onChange={(e) => setStatus(e.target.value)}
        >
          <option value="">{t("allStatuses")}</option>
          {STATUS_OPTIONS.map((value) => (
            <option key={value} value={value}>
              {ts(value)}
            </option>
          ))}
        </select>
        {(experiment || status || model) && (
          <button
            type="button"
            className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1"
            onClick={() => {
              setExperiment("");
              setStatus("");
              setModelInput("");
              setModel("");
            }}
          >
            <X className="size-3" />
            {t("resetFilters")}
          </button>
        )}
      </div>

      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-10"></TableHead>
              <TableHead>{t("colModel")}</TableHead>
              <TableHead>{te("experiment")}</TableHead>
              <TableHead>{t("colStatus")}</TableHead>
              <TableHead>{te("result")}</TableHead>
              <TableHead>{t("colCreatedAt")}</TableHead>
              <TableHead className="w-10"></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              <TableRow>
                <TableCell colSpan={7}>
                  <div className="flex items-center justify-center py-10">
                    <Loader2 className="size-5 animate-spin text-muted-foreground" />
                  </div>
                </TableCell>
              </TableRow>
            ) : !runs || runs.length === 0 ? (
              <TableRow>
                <TableCell colSpan={7}>
                  <div className="flex flex-col items-center justify-center py-10 gap-2 text-muted-foreground">
                    <FlaskConical className="size-6" />
                    <span className="text-sm">{t("empty")}</span>
                  </div>
                </TableCell>
              </TableRow>
            ) : (
              runs.map((r) => {
                const isChecked = selected.has(r.id);
                const disableCheck = !isChecked && selected.size >= COMPARE_MAX;
                return (
                <TableRow key={r.id} className="hover:bg-muted/40">
                  <TableCell>
                    <input
                      type="checkbox"
                      className="size-4 rounded border-input cursor-pointer disabled:cursor-not-allowed disabled:opacity-50"
                      checked={isChecked}
                      disabled={disableCheck}
                      title={
                        disableCheck
                          ? t("compareMaxHint", { max: COMPARE_MAX })
                          : undefined
                      }
                      onChange={() => toggleSelected(r.id)}
                    />
                  </TableCell>
                  <TableCell className="font-medium">
                    <Link
                      href={`/admin/benchmarks/${r.id}`}
                      className="hover:underline"
                    >
                      {r.model_name}
                    </Link>
                    <p className="text-xs font-normal text-muted-foreground">{[r.k8s_namespace, r.label].filter(Boolean).join(" · ")}</p>
                  </TableCell>
                  <TableCell>{te(experimentOf(r))}</TableCell>
                  <TableCell>
                    <Badge className={STATUS_STYLES[r.status]}>
                      {ts(r.status)}
                    </Badge>
                  </TableCell>
                  <TableCell className="tabular-nums"><ResultSummary run={r}/></TableCell>
                  <TableCell className="text-sm">
                    {formatDateTime(r.created_at, localeTag)}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-muted-foreground hover:text-destructive"
                      onClick={() => handleDelete(r)}
                      disabled={deleteMut.isPending}
                      title={t("deleteAction")}
                    >
                      <Trash2 className="size-4" />
                    </Button>
                  </TableCell>
                </TableRow>
                );
              })
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
