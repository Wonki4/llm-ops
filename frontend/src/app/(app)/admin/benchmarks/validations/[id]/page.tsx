"use client";
import { Fragment, use, useState } from "react";
import Link from "next/link";
import { useTranslations, useNow } from "next-intl";
import { ArrowUpDown, ChevronDown, ChevronRight, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { useRecipeValidation, useValidationAction } from "@/hooks/use-recipe-validations";
import { VALIDATIONS, validationTerminal, type ValidationCandidate, type ValidationAttempt } from "@/lib/recipe-validation";
import { useClusterName } from "@/components/serving-scope-filter";
import { scopeQuery, clusterKey } from "@/lib/serving-scope";
import { CacheResults, PerformanceResults, ResultSummary } from "@/components/benchmark-cache";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from "@/components/ui/sheet";

export default function ValidationDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params), query = useRecipeValidation(id), cancel = useValidationAction(id, "cancel");
  const finish = useValidationAction(id, "finish-search");
  const t = useTranslations("recipeValidation"), clusterName = useClusterName();
  const currentTime = useNow({ updateInterval: 1000 });
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [diagnostic, setDiagnostic] = useState<{ candidate: ValidationCandidate; attempt: ValidationAttempt } | null>(null);
  const [sort, setSort] = useState("position"), [ascending, setAscending] = useState(true);
  if (query.error) return <p role="alert">{query.error.message}</p>;
  if (!query.data) return <Loader2 className="animate-spin" />;
  const job = query.data, active = job.candidates.flatMap(c => c.attempts.map(a => ({ c, a }))).find(({ a }) => !a.finished_at);
  const last = (c: ValidationCandidate) => c.attempts.at(-1);
  const metric = (c: ValidationCandidate, key: string) => {
    if (key === "name" || key === "status") return c[key];
    if (key === "position") return c.position;
    if (key === "startup") return last(c)?.state.startup_seconds;
    return c.assessment?.metrics[key];
  };
  const candidates = [...job.candidates].sort((a, b) => {
    const x = metric(a, sort), y = metric(b, sort);
    if (x == null) return y == null ? 0 : 1;
    if (y == null) return -1;
    const compare = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), undefined, { numeric: true });
    return compare * (ascending ? 1 : -1);
  });
  const displayStatus = (s: string) => t.has(`status_${s}`) ? t(`status_${s}`) : t.has(`phase_${s}`) ? t(`phase_${s}`) : s;
  const format = (value: number | undefined, unit = "") => value == null ? t("unmeasured") : `${value.toLocaleString(undefined, { maximumFractionDigits: 1 })}${unit}`;
  const seconds = job.started_at ? Math.max(0, Math.round(((job.finished_at ? new Date(job.finished_at).getTime() : currentTime.getTime()) - new Date(job.started_at).getTime()) / 1000)) : 0;
  const limitKey = active && ({ preflight: "assets", applying: "scheduling", scheduling: "scheduling", starting: "startup", smoke: "measurement", measuring: "measurement", publishing: "measurement" } as const)[active.a.status as "preflight"];
  const stageSeconds = active ? Math.max(0, Math.round((currentTime.getTime() - new Date(active.a.stage_started_at).getTime()) / 1000)) : 0;
  const winner = job.candidates.find(c => c.id === job.selected_candidate_id), final = winner?.attempts.find(a => a.state.publication);
  return <div className="space-y-5">
    <Link href={`${VALIDATIONS}${scopeQuery({ cluster: clusterKey(job.cluster_id), ns: job.namespace })}`} className="text-sm text-muted-foreground hover:underline">← {t("title")}</Link>
    <header className="flex items-start justify-between gap-3"><div><h1 className="text-2xl font-bold">{job.name}</h1><p className="mt-1 text-muted-foreground">{job.model_name} · {clusterName(job.cluster_id)} / {job.namespace}</p></div>
      {!validationTerminal(job.status) && <Button variant="outline" disabled={cancel.isPending || job.status === "cancelling"} onClick={() => cancel.mutate({}, { onError: e => toast.error(e.message) })}>{t("cancel")}</Button>}</header>
    <section className="rounded-lg border bg-card p-5" aria-live="polite"><dl className="grid gap-5 sm:grid-cols-4">
      <div><dt className="text-xs text-muted-foreground">{t("stage")}</dt><dd className="mt-1 font-semibold">{displayStatus(job.phase)}</dd></div>
      <div><dt className="text-xs text-muted-foreground">{t("activeCandidate")}</dt><dd className="mt-1">{active?.c.name ?? "—"}</dd></div>
      <div><dt className="text-xs text-muted-foreground">{t("elapsed")}</dt><dd className="mt-1 tabular-nums">{seconds.toLocaleString()} s / {job.conditions.time_limits.total.toLocaleString()} s</dd></div>
      <div><dt className="text-xs text-muted-foreground">{t("selectionMetric")}</dt><dd className="mt-1">{t(`metric_${job.conditions.selection_metric}`)}</dd></div>
    </dl>{active && limitKey && <p className="mt-4 text-sm tabular-nums">{t("stageTime")}: {stageSeconds.toLocaleString()} s / {job.conditions.time_limits[limitKey].toLocaleString()} s</p>}{job.message && <p className="mt-4 border-t pt-3 text-sm">{job.message}</p>}
      {job.status === "awaiting_input" && <p className="mt-3 text-sm">{t(job.conditions.search ? "searchAwaiting" : "awaitingHint")}</p>}
    </section>
    {job.conditions.search && job.search_progress && <section className="space-y-4 rounded-lg border p-5">
      <h2 className="font-semibold">{t("searchMode")}</h2>
      <dl className="grid gap-4 sm:grid-cols-3"><div><dt className="text-xs text-muted-foreground">{t("searchTrials")}</dt><dd>{job.search_progress.candidate_count} / {job.conditions.search.max_candidates}</dd></div><div><dt className="text-xs text-muted-foreground">{t("searchBest")}</dt><dd>{job.candidates.find(c => c.id === job.search_progress?.best_candidate_id)?.name ?? "—"} · {format(job.search_progress.best_value ?? undefined)}</dd></div><div><dt className="text-xs text-muted-foreground">{t("searchRemaining")}</dt><dd>{job.search_progress.remaining_search_seconds.toLocaleString()} s · {job.search_progress.stale_candidates} / {job.conditions.search.patience} {t("searchStale")}</dd></div></dl>
      {job.search_progress.closed && job.search_progress.stop_reason && <p className="text-sm">{t.has(`stop_${job.search_progress.stop_reason}`) ? t(`stop_${job.search_progress.stop_reason}`) : job.search_progress.stop_reason}</p>}
      {job.search_state?.explanation && <p className="whitespace-pre-wrap text-sm">{job.search_state.explanation}</p>}
      {!job.search_progress.closed && !validationTerminal(job.status) && <><p className="text-sm text-muted-foreground">{t("searchClientHint")}</p><div className="flex flex-wrap gap-3"><Button variant="outline" onClick={async () => { try { await navigator.clipboard.writeText(t("searchPrompt", { id })); toast.success(t("searchCopied")); } catch { toast.error(t("searchCopyFailed")); } }}>{t("searchCopy")}</Button>
      {job.status === "awaiting_input" && <Button disabled={finish.isPending} onClick={() => finish.mutate({ expected_candidate_count: job.candidates.length, reason: "Administrator ended exploration from the portal" }, { onError: e => toast.error(e.message) })}>{t("searchFinish")}</Button>}</div></>}
      {job.search_state?.saved_recipe_id && <Link className="inline-block text-sm underline" href={`/admin/recipes/${job.search_state.saved_recipe_id}`}>{t("searchSavedRecipe")}</Link>}
    </section>}
    {job.status === "succeeded" && final && <section className="space-y-3 rounded-lg border border-emerald-600/30 bg-emerald-600/5 p-5">
      <h2 className="font-semibold">{t("published")}: {winner?.name}</h2><p className="text-sm">{t("publishedHint")}</p>
      <div className="flex flex-wrap gap-4 text-sm underline"><Link href={`/admin/deployments/${final.deployment_id}`}>{t("deployment")}</Link><Link href="/admin/models">{t("catalog")}</Link><Link href={`/admin/serving/topology${scopeQuery({ cluster: clusterKey(job.cluster_id), ns: job.namespace })}`}>{t("routing")}</Link></div>
    </section>}
    <p className="text-xs text-muted-foreground">{t("tableHint")}</p>
    <div className="rounded-lg border"><Table><TableHeader><TableRow>
      {[["name", "candidate"], ["status", "status"], ["startup", "startup"], ["p99_ttft_ms", "ttft"], ["output_throughput", "throughput"]].map(([key, label]) => <TableHead key={key} aria-sort={sort === key ? ascending ? "ascending" : "descending" : "none"}><button className="inline-flex items-center gap-1 whitespace-nowrap" onClick={() => { setAscending(sort === key ? !ascending : true); setSort(key); }}>{t(label)}<ArrowUpDown className="size-3" /></button></TableHead>)}
      {(job.conditions.version ?? 1) === 1 && <TableHead>{t("quality")}</TableHead>}<TableHead>{t("selection")}</TableHead>
    </TableRow></TableHeader><TableBody>{candidates.map(c => <Fragment key={c.id}>
      <TableRow><TableCell><button className="flex items-start gap-2 text-left" aria-expanded={expanded.has(c.id)} onClick={() => setExpanded(old => { const next = new Set(old); if (next.has(c.id)) next.delete(c.id); else next.add(c.id); return next; })}>{expanded.has(c.id) ? <ChevronDown className="mt-1 size-4" /> : <ChevronRight className="mt-1 size-4" />}<span><span className="font-medium">{c.name}</span><span className="mt-1 block break-all text-xs text-muted-foreground">{c.weights} · {c.gpu_count} GPU</span></span></button></TableCell>
        <TableCell><Badge variant={c.status === "failed" ? "destructive" : "outline"}>{displayStatus(c.status)}</Badge>{last(c)?.error_code && <button className="mt-2 block text-xs underline" onClick={() => setDiagnostic({ candidate: c, attempt: last(c)! })}>{t("failure")}</button>}</TableCell>
        <TableCell>{format(last(c)?.state.startup_seconds, " s")}</TableCell><TableCell>{format(c.assessment?.metrics.p99_ttft_ms, " ms")}</TableCell><TableCell>{format(c.assessment?.metrics.output_throughput, " tok/s")}</TableCell>
        {(job.conditions.version ?? 1) === 1 && <TableCell>{c.assessment?.quality.length ? t(c.assessment.quality.every(q => q.passed) ? "passed" : "rejected") : t("unmeasured")}</TableCell>}
        <TableCell>{c.status === "selected" ? t("selected") : c.status === "failed" ? t("rejected") : c.assessment ? t(c.assessment.passed ? "eligible" : "rejected") : "—"}</TableCell>
      </TableRow>
      {expanded.has(c.id) && <TableRow><TableCell colSpan={(job.conditions.version ?? 1) === 1 ? 7 : 6} className="bg-muted/20 p-5"><div className="space-y-5">
        <div className="flex flex-wrap gap-3 text-sm">{job.status === "awaiting_input" && <Link className="underline" href={`${VALIDATIONS}/${id}/candidates/new?source=${c.id}`}>{t("addCorrection")}</Link>}{c.recipe_id && <Link className="underline" href={`/admin/recipes/${c.recipe_id}`}>{t("sharedRecipe")}</Link>}</div>
        {c.snapshot.hypothesis && <p className="whitespace-pre-wrap text-sm"><span className="font-semibold">{t("searchHypothesis")}: </span>{c.snapshot.hypothesis}</p>}
        {c.parent_candidate_id && <p className="text-xs text-muted-foreground">{t("searchParent")}: {job.candidates.find(parent => parent.id === c.parent_candidate_id)?.name ?? c.parent_candidate_id}</p>}
        {c.assessment?.errors.length ? <ul className="space-y-1 text-sm text-destructive">{c.assessment.errors.map((e, i) => <li key={i}>{e}</li>)}</ul> : null}
        {!c.attempts.length && <p className="text-muted-foreground">{t("noAttempts")}</p>}
        {c.attempts.map(a => <section key={a.id} className="space-y-3 rounded-lg border bg-background p-4">
          <div className="flex flex-wrap items-center gap-3"><h3 className="font-semibold">{t("attempt", { number: a.number })} · {t(a.purpose === "final" ? "finalCheck" : "comparison")}</h3><Badge variant="outline">{displayStatus(a.status)}</Badge>{a.deployment_id && <Link className="text-sm underline" href={`/admin/deployments/${a.deployment_id}`}>{t("deployment")}</Link>}<button className="text-sm underline" onClick={() => setDiagnostic({ candidate: c, attempt: a })}>{t("diagnostics")}</button></div>
          {a.error_message && <p className="text-sm text-destructive">{a.error_message}</p>}
          {a.state.assessment && <div className="text-sm"><p>{t(a.state.assessment.passed ? "passed" : "rejected")} · TTFT P99 {format(a.state.assessment.metrics.p99_ttft_ms, " ms")} · {format(a.state.assessment.metrics.output_throughput, " tok/s")}</p>{a.state.assessment.quality.map(q => <p key={`${q.task}:${q.metric}`}>{q.task} / {q.metric}: {q.value ?? "—"} ≥ {q.minimum} · {t(q.passed ? "passed" : "rejected")}</p>)}</div>}
          <div className="divide-y">{a.benchmarks.map(run => <details key={run.id} className="py-2"><summary className="cursor-pointer text-sm">{run.label} · {run.status} · <ResultSummary run={run} /></summary><div className="space-y-3 py-3"><Link className="text-sm underline" href={`/admin/benchmarks/${run.id}`}>{t("measurement")}</Link>{run.params.experiment === "prefix_cache" ? <CacheResults run={run} /> : run.kind === "performance" ? <PerformanceResults run={run} /> : <pre className="max-h-64 overflow-auto text-xs">{JSON.stringify(run.result, null, 2)}</pre>}</div></details>)}</div>
          {a.benchmarks.length > 1 && <Link className="inline-block text-sm underline" href={`/admin/benchmarks/compare?ids=${a.benchmarks.filter(r => r.kind === "performance" && !r.label?.endsWith(": smoke")).slice(0, 5).map(r => r.id).join(",")}`}>{t("compareMeasurements")}</Link>}
        </section>)}
      </div></TableCell></TableRow>}
    </Fragment>)}</TableBody></Table></div>
    {job.status === "awaiting_input" && <Button asChild><Link href={`${VALIDATIONS}/${id}/candidates/new`}>{t("addCandidate")}</Link></Button>}
    <details className="rounded-lg border p-4"><summary className="cursor-pointer text-sm">{t("frozenConditions")}</summary><pre className="mt-3 max-h-96 overflow-auto text-xs">{JSON.stringify(job.conditions, null, 2)}</pre></details>
    <Sheet open={!!diagnostic} onOpenChange={open => { if (!open) setDiagnostic(null); }}><SheetContent className="overflow-y-auto sm:max-w-2xl"><SheetHeader><SheetTitle>{t("diagnostics")}</SheetTitle><SheetDescription>{diagnostic?.candidate.name} · {t("retainedHint")}</SheetDescription></SheetHeader>
      {diagnostic && <div className="space-y-5 px-5 pb-6"><p className="text-sm text-destructive">{diagnostic.attempt.error_code}: {diagnostic.attempt.error_message}</p>
        <section><h3 className="mb-2 font-semibold">{t("llmAnalysis")}</h3><p className="whitespace-pre-wrap text-sm">{diagnostic.attempt.analysis?.explanation ?? t("analysisPending")}</p>{diagnostic.attempt.analysis && <pre className="mt-3 overflow-auto rounded bg-muted p-3 text-xs">{JSON.stringify(diagnostic.attempt.analysis.proposed_patch, null, 2)}</pre>}</section>
        {job.status === "awaiting_input" && <Button asChild><Link href={`${VALIDATIONS}/${id}/candidates/new?source=${diagnostic.candidate.id}&attempt=${diagnostic.attempt.id}`}>{t("addCorrection")}</Link></Button>}
        <section><h3 className="mb-2 font-semibold">{t("eventsLogs")}</h3><pre className="whitespace-pre-wrap break-all rounded-md bg-muted p-3 text-xs">{JSON.stringify(diagnostic.attempt.diagnostics, null, 2)}</pre></section>
        <details><summary>{t("assetsAndConfig")}</summary><pre className="max-h-96 overflow-auto text-xs">{JSON.stringify(diagnostic.attempt.state.assets, null, 2)}</pre><pre className="max-h-96 overflow-auto text-xs">{JSON.stringify(diagnostic.attempt.state.effective_manifests, null, 2)}</pre></details>
      </div>}
    </SheetContent></Sheet>
  </div>;
}
