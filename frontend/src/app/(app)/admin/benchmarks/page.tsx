"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Search, Loader2, FlaskConical, X, Plus, GitCompare, Trash2, ChevronLeft, ChevronRight } from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { useLocaleTag, parseServerDate } from "@/lib/locale";
import { BenchmarkModelNavigation, benchmarkModelTitle } from "@/components/benchmark-model-navigation";
import { useBenchmarks, useBenchmarkModelGroups, useDeleteBenchmark } from "@/hooks/use-api";
import { BenchmarkPresetManager } from "@/components/benchmark-preset-manager";
import { BenchmarkWorkloadSummary } from "@/components/benchmark-workload-summary";
import { experimentOf, ResultSummary } from "@/components/benchmark-cache";
import type { BenchmarkRun } from "@/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { FormSelect, FormSelectItem } from "@/components/ui/form-select";

const COMPARE_MAX = 5;
const PAGE_SIZE = 25;
const STATUS_STYLES: Record<BenchmarkRun["status"], string> = {
  provisioning: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
  pending: "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300",
  running: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
  succeeded: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400",
  failed: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",
  cancelled: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400",
};
const EXPERIMENTS = ["prefix_cache", "performance", "accuracy"] as const;
const STATUS_OPTIONS: BenchmarkRun["status"][] = ["provisioning", "pending", "running", "succeeded", "failed", "cancelled"];

function formatDateTime(dateStr: string | null, localeTag: string): string {
  if (!dateStr) return "-";
  return parseServerDate(dateStr).toLocaleString(localeTag, {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

export default function AdminBenchmarksPage() {
  return <Suspense fallback={<div className="h-32 animate-pulse rounded-lg bg-muted" />}><BenchmarkLibrary /></Suspense>;
}

function BenchmarkLibrary() {
  const t = useTranslations("adminBenchmarks");
  const ts = useTranslations("benchmarkStatus");
  const te = useTranslations("benchExperiment");
  const localeTag = useLocaleTag();
  const searchParams = useSearchParams();
  const search = searchParams.get("q") || "";
  const experiment = searchParams.get("experiment") || "";
  const status = searchParams.get("status") || "";
  const requestedModel = searchParams.get("model");
  const [selected, setSelected] = useState<Set<string>>(new Set());

  function updateQuery(changes: Record<string, string | null>, replace = false) {
    const url = new URL(window.location.href);
    for (const [key, value] of Object.entries(changes)) {
      if (value) url.searchParams.set(key, value);
      else url.searchParams.delete(key);
    }
    if (replace) window.history.replaceState(null, "", url);
    else window.history.pushState(null, "", url);
  }

  const groupsQuery = useBenchmarkModelGroups({ experiment: experiment || undefined, status: status || undefined });
  const groups = (groupsQuery.data ?? []).filter(group => group.model_name.toLowerCase().includes(search.trim().toLowerCase()));
  const current = groups.find(group => group.model_name === requestedModel) ?? groups[0];
  const totalPages = Math.max(1, Math.ceil((current?.run_count ?? 0) / PAGE_SIZE));
  const requestedPage = Number(searchParams.get("page") || 1);
  const page = Number.isSafeInteger(requestedPage) && requestedPage > 0 ? Math.min(requestedPage, totalPages) : 1;
  const runsQuery = useBenchmarks({
    model_name: current?.model_name, experiment: experiment || undefined, status: status || undefined,
    limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE,
  }, !!current);
  const runs = runsQuery.data ?? [];
  const detailQuery = new URLSearchParams(searchParams);
  if (current) detailQuery.set("model", current.model_name);
  detailQuery.set("page", String(page));

  const toggleSelected = (id: string) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else if (next.size < COMPARE_MAX) next.add(id);
      return next;
    });
  };
  const deleteMut = useDeleteBenchmark();
  const handleDelete = (run: BenchmarkRun) => {
    if (!confirm(t("confirmDelete", { name: run.label || run.model_name }))) return;
    deleteMut.mutate(run.id, {
      onSuccess: () => {
        setSelected(prev => { const next = new Set(prev); next.delete(run.id); return next; });
        toast.success(t("deleteSuccess"));
      },
      onError: (error) => toast.error(error instanceof Error ? error.message : t("deleteFail")),
    });
  };
  const error = groupsQuery.error || runsQuery.error;

  return <div className="space-y-6">
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div>
        <h1 className="text-2xl font-bold">{t("pageTitle")}</h1>
        <p className="mt-1 text-muted-foreground">{t("pageDescription")}</p>
      </div>
      <div className="flex items-center gap-2">
        <BenchmarkPresetManager />
        <Button asChild><Link href="/admin/benchmarks/new"><Plus className="size-4" />{t("runBenchmark")}</Link></Button>
      </div>
    </header>

    <div className="flex flex-wrap items-center gap-3">
      <div className="relative min-w-[240px] max-w-sm flex-1">
        <Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
        <Input aria-label={t("searchPlaceholder")} placeholder={t("searchPlaceholder")} value={search}
          onChange={event => updateQuery({ q: event.target.value, model: null, page: null }, true)} className="h-9 pl-8" />
      </div>
      <FormSelect placeholder={te("allExperiments")} aria-label={te("experiment")} value={experiment}
        onValueChange={value => updateQuery({ experiment: value, model: null, page: null })}>
        <FormSelectItem value="">{te("allExperiments")}</FormSelectItem>
        {EXPERIMENTS.map(value => <FormSelectItem key={value} value={value}>{te(value)}</FormSelectItem>)}
      </FormSelect>
      <FormSelect placeholder={t("allStatuses")} aria-label={t("allStatuses")} value={status}
        onValueChange={value => updateQuery({ status: value, model: null, page: null })}>
        <FormSelectItem value="">{t("allStatuses")}</FormSelectItem>
        {STATUS_OPTIONS.map(value => <FormSelectItem key={value} value={value}>{ts(value)}</FormSelectItem>)}
      </FormSelect>
      {(experiment || status || search) && <Button variant="ghost" size="sm"
        onClick={() => updateQuery({ experiment: null, status: null, q: null, model: null, page: null })}>
        <X className="size-3" />{t("resetFilters")}
      </Button>}
    </div>

    {selected.size > 0 && <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-muted/20 px-4 py-3">
      <span className="text-sm">{t("selectionSummary", { count: selected.size, max: COMPARE_MAX })}</span>
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}>{t("clearSelection")}</Button>
        {selected.size >= 2 && <Button asChild size="sm" variant="outline">
          <Link href={`/admin/benchmarks/compare?ids=${Array.from(selected).join(",")}`}><GitCompare className="size-4" />{t("compareSelected", { count: selected.size })}</Link>
        </Button>}
      </div>
    </div>}

    {error ? <p role="alert" className="rounded-lg border p-4 text-destructive">{error.message}</p>
      : groupsQuery.isPending ? <div className="flex justify-center py-12"><Loader2 className="size-5 animate-spin text-muted-foreground" /></div>
      : !current ? <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed py-12 text-muted-foreground">
        <FlaskConical className="size-6" /><p>{search || experiment || status ? t("noMatchingModels") : t("empty")}</p>
      </div>
      : <div className="grid items-start gap-4 lg:grid-cols-[minmax(13rem,17rem)_minmax(0,1fr)]">
        <BenchmarkModelNavigation groups={groups} selected={current.model_name} searching={!!search.trim()}
          onSelect={name => updateQuery({ model: name, page: null })} />

        <section className="min-w-0 overflow-hidden rounded-lg border bg-card" aria-label={t("modelRuns")}>
          <div className="space-y-2 border-b px-4 py-4">
            <h2 className="flex flex-wrap items-baseline gap-x-3 gap-y-1 font-semibold">
              <span className="break-all">{benchmarkModelTitle(current.model_name)}</span>
              <span className="text-sm font-normal text-muted-foreground">{t("runCount", { count: current.run_count })}</span>
            </h2>
            {benchmarkModelTitle(current.model_name) !== current.model_name && <p className="break-all text-xs text-muted-foreground">{current.model_name}</p>}
            <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
              {current.active_count > 0 && <span>{t("activeCount", { count: current.active_count })}</span>}
              <span>{t("succeededCount", { count: current.succeeded_count })}</span>
              <span>{t("failedCount", { count: current.failed_count })}</span>
            </div>
          </div>
          <Table>
            <TableHeader><TableRow>
              <TableHead className="w-10"><span className="sr-only">{t("chooseRun")}</span></TableHead>
              <TableHead>{t("colRun")}</TableHead><TableHead>{te("experiment")}</TableHead>
              <TableHead>{t("colStatus")}</TableHead><TableHead>{te("result")}</TableHead>
              <TableHead>{t("colCreatedAt")}</TableHead><TableHead className="w-10" />
            </TableRow></TableHeader>
            <TableBody>
              {runsQuery.isPending ? <TableRow><TableCell colSpan={7} className="py-10 text-center"><Loader2 className="mx-auto size-5 animate-spin" /></TableCell></TableRow>
                : runs.length === 0 ? <TableRow><TableCell colSpan={7} className="py-10 text-center text-muted-foreground">{t("empty")}</TableCell></TableRow>
                : runs.map(run => {
                  const checked = selected.has(run.id);
                  const disabled = !checked && selected.size >= COMPARE_MAX;
                  return <TableRow key={run.id} className="hover:bg-muted/40">
                    <TableCell><input type="checkbox" aria-label={`${t("chooseRun")}: ${run.label || run.id}`}
                      className="size-4 cursor-pointer rounded border-input disabled:cursor-not-allowed disabled:opacity-50"
                      checked={checked} disabled={disabled} title={disabled ? t("compareMaxHint", { max: COMPARE_MAX }) : undefined}
                      onChange={() => toggleSelected(run.id)} /></TableCell>
                    <TableCell className="min-w-64 font-medium">
                      <Link href={`/admin/benchmarks/${run.id}?${detailQuery}`} className="hover:underline">{run.label || `#${run.id.slice(0, 8)}`}</Link>
                      {run.k8s_namespace && <p className="mt-1 text-xs font-normal text-muted-foreground">{run.k8s_namespace}</p>}
                      <div className="mt-2"><BenchmarkWorkloadSummary run={run} /></div>
                    </TableCell>
                    <TableCell className="whitespace-nowrap">{te(experimentOf(run))}</TableCell>
                    <TableCell><Badge className={STATUS_STYLES[run.status]}>{ts(run.status)}</Badge></TableCell>
                    <TableCell className="whitespace-nowrap tabular-nums"><ResultSummary run={run} /></TableCell>
                    <TableCell className="whitespace-nowrap text-sm">
                      {formatDateTime(run.created_at, localeTag)}
                      <p className="mt-1 font-mono text-xs text-muted-foreground" title={run.id}>#{run.id.slice(0, 8)}</p>
                    </TableCell>
                    <TableCell className="text-right"><Button variant="ghost" size="sm" className="text-muted-foreground hover:text-destructive"
                      onClick={() => handleDelete(run)} disabled={deleteMut.isPending} title={t("deleteAction")}><Trash2 className="size-4" /></Button></TableCell>
                  </TableRow>;
                })}
            </TableBody>
          </Table>
          {totalPages > 1 && <div className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3">
            <span className="text-xs text-muted-foreground">{t("pageSummary", { page, pages: totalPages, count: current.run_count })}</span>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => updateQuery({ model: current.model_name, page: String(page - 1) })}><ChevronLeft className="size-4" />{t("previousPage")}</Button>
              <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => updateQuery({ model: current.model_name, page: String(page + 1) })}>{t("nextPage")}<ChevronRight className="size-4" /></Button>
            </div>
          </div>}
        </section>
      </div>}
  </div>;
}
