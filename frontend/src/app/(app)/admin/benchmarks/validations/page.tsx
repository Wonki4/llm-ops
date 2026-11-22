"use client";
import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { ArrowUpDown, Plus } from "lucide-react";
import { useRecipeValidations } from "@/hooks/use-recipe-validations";
import { useK8sClusterSettings } from "@/hooks/use-api";
import { ServingScopeFilter, useServingScope, useClusterName } from "@/components/serving-scope-filter";
import { PORTAL_CLUSTER, scopeQuery } from "@/lib/serving-scope";
import { VALIDATIONS } from "@/lib/recipe-validation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";
import { FormSelect, FormSelectItem } from "@/components/ui/form-select";

export default function ValidationsPage() { return <Suspense><List /></Suspense>; }
function List() {
  const t = useTranslations("recipeValidation"), router = useRouter(), clusterName = useClusterName();
  const settings = useK8sClusterSettings();
  const [scope, setScope] = useServingScope(settings.data);
  const [q, setQ] = useState(""), [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0), [sort, setSort] = useState("created_at"), [direction, setDirection] = useState("desc");
  const params = new URLSearchParams({ offset: String(offset), limit: "20", sort, direction });
  if (q) params.set("q", q);
  if (status) params.set("status", status);
  if (scope.cluster) params.set("cluster_id", scope.cluster === PORTAL_CLUSTER ? "" : scope.cluster);
  if (scope.ns) params.set("namespace", scope.ns);
  const query = useRecipeValidations(params, !!settings.data);
  const rows = query.data?.items ?? [];
  function order(key: string) { setDirection(sort === key && direction === "desc" ? "asc" : "desc"); setSort(key); setOffset(0); }
  return <div className="space-y-5">
    <header className="flex flex-wrap items-start justify-between gap-3"><div><h1 className="text-2xl font-bold">{t("title")}</h1><p className="mt-1 text-muted-foreground">{t("subtitle")}</p></div>
      <Button asChild><Link href={`${VALIDATIONS}/new${scopeQuery(scope)}`}><Plus />{t("new")}</Link></Button></header>
    <div className="flex flex-wrap items-center gap-3">
      <ServingScopeFilter scope={scope} onChange={v => { setScope(v); setOffset(0); }} items={rows} settings={settings.data} />
      <Input aria-label={t("search")} placeholder={t("search")} className="max-w-64" value={q} onChange={e => { setQ(e.target.value); setOffset(0); }} />
      <FormSelect placeholder={t("allStatuses")} aria-label={t("status")} value={status} onValueChange={selectedValue => { setStatus(selectedValue); setOffset(0); }}>
        <FormSelectItem value="">{t("allStatuses")}</FormSelectItem>{["queued", "running", "awaiting_input", "cancelling", "succeeded", "failed", "cancelled"].map(s => <FormSelectItem value={s} key={s}>{t(`status_${s}`)}</FormSelectItem>)}
      </FormSelect>
    </div>
    {query.error && <p role="alert" className="text-destructive">{query.error.message}</p>}
    <div className="rounded-lg border"><Table><TableHeader><TableRow>
      {[["name", "job"], ["namespace", "target"], ["status", "stage"]].map(([key, label]) => <TableHead key={key} aria-sort={sort === key ? direction === "asc" ? "ascending" : "descending" : "none"}><button className="inline-flex items-center gap-1" onClick={() => order(key)}>{t(label)}<ArrowUpDown className="size-3" /></button></TableHead>)}
      <TableHead>{t("progress")}</TableHead><TableHead>{t("winner")}</TableHead><TableHead><button onClick={() => order("created_at")}>{t("created")} ↕</button></TableHead>
    </TableRow></TableHeader><TableBody>
      {rows.map(row => <TableRow key={row.id} className="cursor-pointer" onClick={() => router.push(`${VALIDATIONS}/${row.id}`)}>
        <TableCell><Link className="font-medium hover:underline" href={`${VALIDATIONS}/${row.id}`}>{row.name}</Link><p className="text-xs text-muted-foreground">{row.model_name}</p></TableCell>
        <TableCell>{clusterName(row.cluster_id)}<p className="text-xs text-muted-foreground">{row.namespace}</p></TableCell>
        <TableCell><Badge variant="outline">{t.has(`status_${row.status}`) ? t(`status_${row.status}`) : row.status}</Badge><p className="mt-1 text-xs">{t.has(`phase_${row.phase}`) ? t(`phase_${row.phase}`) : row.phase}</p></TableCell>
        <TableCell>{row.completed_count} / {row.candidate_count}</TableCell><TableCell>{row.selected_candidate ?? "—"}</TableCell>
        <TableCell className="whitespace-nowrap text-xs">{new Date(row.created_at).toLocaleString()}</TableCell>
      </TableRow>)}
      {!rows.length && <TableRow><TableCell colSpan={6} className="py-12 text-center text-muted-foreground">{t(query.isPending ? "loading" : "empty")}</TableCell></TableRow>}
    </TableBody></Table></div>
    <div className="flex items-center justify-end gap-3 text-sm"><span>{offset + (rows.length ? 1 : 0)}–{offset + rows.length} / {query.data?.total ?? 0}</span>
      <Button variant="outline" disabled={!offset} onClick={() => setOffset(offset - 20)}>{t("previous")}</Button>
      <Button variant="outline" disabled={offset + rows.length >= (query.data?.total ?? 0)} onClick={() => setOffset(offset + 20)}>{t("next")}</Button></div>
  </div>;
}
