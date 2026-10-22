"use client";

import { Fragment, Suspense, useState } from "react";
import Link from "next/link";
import { AlertTriangle, CheckCircle2, ChevronRight, Loader2, ScrollText, Server } from "lucide-react";
import { useTranslations } from "next-intl";

import {
  ExternalServing,
  useExternalServings,
  useModelDeployments,
  useUnregisterExternalServing,
} from "@/hooks/use-api";
import type { ModelDeployment } from "@/types";
import { ServingScopeFilter, useClusterName, useServingScope } from "@/components/serving-scope-filter";
import { clusterKey, inScope } from "@/lib/serving-scope";
import { ExternalServingRegisterDialog } from "@/components/external-serving-register-dialog";
import { Badge } from "@/components/ui/badge";
import { ServingDeployTabs } from "@/components/serving-deploy-tabs";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

/** Status → badge style. Ready=green, Failed/Missing=red, others=neutral. */
function StatusBadge({ status }: { status: string }) {
  const variant =
    status === "Ready" ? "default" : status === "Failed" || status === "Missing" ? "destructive" : "secondary";
  return <Badge variant={variant}>{status}</Badge>;
}

function externalKey(s: ExternalServing) {
  return `${s.cluster_id ?? "default"}/${s.namespace}/${s.deployment_name}`;
}

/** New-recipe page pre-filled by reverse-parsing this external Deployment. */
function externalRecipeHref(s: ExternalServing) {
  const qs = new URLSearchParams({ from: "external", namespace: s.namespace, name: s.deployment_name });
  if (s.cluster_id) qs.set("cluster", s.cluster_id);
  return `/admin/recipes/new?${qs.toString()}`;
}

/** GPUs a portal deployment requests in total (P/D: both pools). */
function deploymentGpus(d: ModelDeployment): number {
  if (d.serving_mode !== "pd") return d.gpu_count * d.replicas;
  return (["prefill", "decode"] as const).reduce((sum, role) => {
    const pool = d.pd_config?.[role];
    return sum + (pool?.gpu_count ?? d.gpu_count) * (pool?.replicas ?? 1);
  }, 0);
}

type Group = { key: string; clusterId: string | null; namespace: string; deployments: ModelDeployment[]; servings: ExternalServing[] };

/** Portal deployments and external servings bucketed by cluster → namespace. */
function groupByScope(deployments: ModelDeployment[], servings: ExternalServing[], clusterName: (id: string | null) => string): Group[] {
  const groups = new Map<string, Group>();
  const bucket = (clusterId: string | null, namespace: string) => {
    const key = `${clusterKey(clusterId)}/${namespace}`;
    if (!groups.has(key)) groups.set(key, { key, clusterId, namespace, deployments: [], servings: [] });
    return groups.get(key)!;
  };
  deployments.forEach((d) => bucket(d.cluster_id, d.namespace).deployments.push(d));
  servings.forEach((s) => bucket(s.cluster_id, s.namespace).servings.push(s));
  return [...groups.values()].sort(
    (a, b) => clusterName(a.clusterId).localeCompare(clusterName(b.clusterId)) || a.namespace.localeCompare(b.namespace),
  );
}

export default function DeploymentsPage() {
  // useSearchParams (the scope filter) needs a Suspense boundary for static prerendering.
  return (
    <Suspense fallback={<div className="h-64 animate-pulse rounded-lg bg-muted" />}>
      <DeploymentsList />
    </Suspense>
  );
}

function DeploymentsList() {
  const t = useTranslations("adminDeployments");
  const ts = useTranslations("serving");
  const [scope, setScope] = useServingScope();
  const clusterName = useClusterName();
  const { data: deployments, isLoading } = useModelDeployments();
  const { data: external, isLoading: externalLoading } = useExternalServings();
  const unregister = useUnregisterExternalServing();

  const [registerTarget, setRegisterTarget] = useState<ExternalServing | null>(null);
  const [expandedKey, setExpandedKey] = useState<string | null>(null);

  const loading = isLoading || externalLoading;
  const allServings = external?.servings ?? [];
  const scanErrors = external?.errors ?? [];
  const scopeItems = [...(deployments ?? []), ...allServings];
  const groups = groupByScope(
    (deployments ?? []).filter((d) => inScope(scope, d)),
    allServings.filter((s) => inScope(scope, s)),
    clusterName,
  );
  const isEmpty = scopeItems.length === 0;
  const scopeEmpty = !isEmpty && groups.length === 0;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2"><Server className="size-5" />{t("pageTitle")}</h1>
        <p className="text-muted-foreground mt-1">{t("pageDescription")}</p>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <ServingDeployTabs />
        <ServingScopeFilter scope={scope} onChange={setScope} items={scopeItems} />
      </div>

      {scanErrors.length > 0 && (
        <div className="flex items-center gap-2 rounded-md border border-yellow-300 bg-yellow-50 dark:bg-yellow-950/30 px-4 py-3 text-sm">
          <AlertTriangle className="size-4 shrink-0 text-yellow-600" />
          <span>{t("scanErrors", { clusters: scanErrors.map((e) => e.cluster).join(", ") })}</span>
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("listTitle")}</CardTitle>
          <CardDescription>{t("listHint")}</CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="flex justify-center py-12"><Loader2 className="size-5 animate-spin text-muted-foreground" /></div>
          ) : isEmpty || scopeEmpty ? (
            <div className="flex flex-col items-center justify-center rounded-lg border border-dashed py-16">
              <Server className="size-10 text-muted-foreground mb-3" />
              <p className="text-muted-foreground">{isEmpty ? t("empty") : ts("scopeEmpty")}</p>
            </div>
          ) : (
            <div className="rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("colModel")}</TableHead>
                    <TableHead>{t("colStatus")}</TableHead>
                    <TableHead className="text-right">{t("colReplicas")}</TableHead>
                    <TableHead>{t("colCluster")}</TableHead>
                    <TableHead>{t("colNamespace")}</TableHead>
                    <TableHead>{t("colImage")}</TableHead>
                    <TableHead>{t("colRegistered")}</TableHead>
                    <TableHead></TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {groups.map((g) => {
                    const ready = g.deployments.filter((d) => d.status === "Ready").length + g.servings.filter((s) => s.status === "Ready").length;
                    const gpus = g.deployments.reduce((sum, d) => sum + deploymentGpus(d), 0);
                    return (
                      <Fragment key={g.key}>
                        <TableRow className="bg-muted/40 hover:bg-muted/40" data-testid="scope-group">
                          <TableCell colSpan={8} className="py-1.5">
                            <span className="font-mono text-xs font-medium">{clusterName(g.clusterId)} / {g.namespace}</span>
                            <span className="ml-3 text-xs text-muted-foreground" title={ts("groupGpuHint")}>
                              {ts("groupSummary", { count: g.deployments.length + g.servings.length, ready, gpus })}
                            </span>
                          </TableCell>
                        </TableRow>
                  {g.deployments.map((d) => (
                    <TableRow
                      key={d.id}
                      className="cursor-pointer hover:bg-muted/50"
                      onClick={() => { window.location.href = `/admin/deployments/${d.id}`; }}
                    >
                      <TableCell className="font-medium">
                        <Link
                          href={`/admin/deployments/${d.id}`}
                          className="hover:underline"
                          onClick={(e) => e.stopPropagation()}
                        >
                          {d.model_name}
                        </Link>
                        <Badge variant="secondary" className="ml-2 font-mono text-[10px] uppercase">{d.engine}</Badge>
                        {d.serving_mode === "pd" && <Badge variant="outline" className="ml-1 text-[10px]" data-testid="pd-badge">{t("pdBadge")}</Badge>}
                        {d.gpu_type && <Badge variant="outline" className="ml-1 font-mono text-[10px]">{d.gpu_type}</Badge>}
                        {(d.llmd_stack_count ?? 0) > 0 && (
                          <Badge variant="outline" className="ml-1 text-[10px]" title={t("llmdLinkedHint", { count: d.llmd_stack_count ?? 0 })}>llm-d ×{d.llmd_stack_count}</Badge>
                        )}
                        {d.recipe_id && (
                          <Badge asChild variant="outline" className="ml-1 gap-1 text-[10px]">
                            <Link href={`/admin/recipes/${d.recipe_id}`} onClick={(e) => e.stopPropagation()} title={t("recipeLinkedHint")}>
                              <ScrollText className="size-3" />{d.recipe_name ?? t("recipeLabel")}
                            </Link>
                          </Badge>
                        )}
                      </TableCell>
                      <TableCell><StatusBadge status={d.status} /></TableCell>
                      <TableCell className="text-right tabular-nums">
                        {d.serving_mode === "pd" && d.pd_summary ? d.pd_summary : `${d.ready_replicas}/${d.replicas}`}
                      </TableCell>
                      <TableCell className="font-mono text-xs">{clusterName(d.cluster_id)}</TableCell>
                      <TableCell className="font-mono text-xs">{d.namespace}</TableCell>
                      <TableCell className="font-mono text-xs max-w-[200px] truncate" title={d.image}>{d.image}</TableCell>
                      <TableCell>
                        {d.litellm_model_id ? (
                          <CheckCircle2 className="size-4 text-green-600" />
                        ) : (
                          <span className="text-xs text-muted-foreground">—</span>
                        )}
                      </TableCell>
                      <TableCell>
                        <span className="flex items-center justify-end gap-1">
                          <Button asChild variant="ghost" size="sm" className="h-7 text-xs" title={t("saveAsRecipe")}>
                            <Link href={`/admin/recipes/new?from=deployment&id=${d.id}`} onClick={(e) => e.stopPropagation()}>
                              <ScrollText className="size-3.5" />
                            </Link>
                          </Button>
                          <ChevronRight className="size-4 text-muted-foreground" />
                        </span>
                      </TableCell>
                    </TableRow>
                  ))}
                  {g.servings.map((s) => {
                    const key = externalKey(s);
                    const expanded = expandedKey === key;
                    return (
                      <Fragment key={key}>
                        <TableRow
                          className="cursor-pointer hover:bg-muted/50"
                          onClick={() => setExpandedKey(expanded ? null : key)}
                        >
                          <TableCell className="font-medium">
                            <span className="flex items-center gap-2">
                              {s.registration?.model_name ?? s.deployment_name}
                              <Badge variant="outline">{t("externalBadge")}</Badge>
                              <Badge variant="secondary">{s.engine}</Badge>
                            </span>
                          </TableCell>
                          <TableCell><StatusBadge status={s.status} /></TableCell>
                          <TableCell className="text-right tabular-nums">{s.ready_replicas}/{s.replicas}</TableCell>
                          <TableCell className="font-mono text-xs">{s.cluster_name}</TableCell>
                          <TableCell className="font-mono text-xs">{s.namespace}</TableCell>
                          <TableCell className="font-mono text-xs max-w-[200px] truncate" title={s.image}>{s.image}</TableCell>
                          <TableCell>
                            {s.registration ? (
                              <span className="flex items-center gap-2">
                                <CheckCircle2 className="size-4 text-green-600" />
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  className="h-7 text-xs"
                                  disabled={unregister.isPending}
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    unregister.mutate(s.registration!.id);
                                  }}
                                >
                                  {t("unregister")}
                                </Button>
                              </span>
                            ) : (
                              <Button
                                variant="outline"
                                size="sm"
                                className="h-7 text-xs"
                                onClick={(e) => { e.stopPropagation(); setRegisterTarget(s); }}
                              >
                                {t("register")}
                              </Button>
                            )}
                          </TableCell>
                          <TableCell>
                            <span className="flex items-center justify-end">
                              <Button asChild variant="ghost" size="sm" className="h-7 text-xs" title={t("saveAsRecipe")}>
                                <Link href={externalRecipeHref(s)} onClick={(e) => e.stopPropagation()}>
                                  <ScrollText className="size-3.5" />
                                </Link>
                              </Button>
                            </span>
                          </TableCell>
                        </TableRow>
                        {expanded && (
                          <TableRow className="bg-muted/30 hover:bg-muted/30">
                            <TableCell colSpan={8}>
                              <div className="space-y-1 py-2 text-xs font-mono">
                                <p className="text-muted-foreground font-sans">{t("externalHint")}</p>
                                {s.model_path && <p>--model: {s.model_path}</p>}
                                {s.args.length > 0 && <p>args: {s.args.join(" ")}</p>}
                                {Object.keys(s.labels).length > 0 && (
                                  <p>labels: {Object.entries(s.labels).map(([k, v]) => `${k}=${v}`).join(", ")}</p>
                                )}
                                {s.registration && <p>api_base: {s.registration.api_base}</p>}
                                <p className="pt-1 font-sans">
                                  <Link href={externalRecipeHref(s)} className="inline-flex items-center gap-1 text-primary hover:underline">
                                    <ScrollText className="size-3.5" />{t("saveAsRecipeExternal")}
                                  </Link>
                                </p>
                              </div>
                            </TableCell>
                          </TableRow>
                        )}
                      </Fragment>
                    );
                  })}
                      </Fragment>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <ExternalServingRegisterDialog serving={registerTarget} onClose={() => setRegisterTarget(null)} />
    </div>
  );
}
