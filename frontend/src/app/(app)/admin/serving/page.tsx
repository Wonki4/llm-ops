"use client";

import Link from "next/link";
import { CheckCircle2, Workflow } from "lucide-react";
import { useTranslations } from "next-intl";

import { useServingOverview } from "@/hooks/use-api";
import type { ServingOverviewRow } from "@/types";
import { ModelStatusBadge } from "@/components/model-status-badge";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";

function deployVariant(status: string): "default" | "secondary" | "destructive" {
  if (status === "Ready") return "default";
  if (status === "Failed" || status === "Missing") return "destructive";
  return "secondary";
}

function llmdVariant(health: string | undefined): "default" | "secondary" | "destructive" | "outline" {
  if (!health || health === "Unknown") return "outline";
  if (health === "Healthy") return "default";
  if (health === "Degraded" || health === "Missing") return "destructive";
  return "secondary";
}

function fmt(n: number | undefined, digits: number, unit: string): string | null {
  return typeof n === "number" ? `${n.toFixed(digits)}${unit}` : null;
}

/** Dashed link used when a stage is still empty: it points at where that stage starts. */
function NextStep({ href, label }: { href: string; label: string }) {
  return (
    <Link href={href} className="text-xs text-muted-foreground underline decoration-dashed underline-offset-4 hover:text-foreground">
      {label}
    </Link>
  );
}

export default function ServingHomePage() {
  const t = useTranslations("serving");
  const { data, isLoading } = useServingOverview();
  const rows = data?.models;
  const unlinked = data?.unlinked_stacks ?? [];

  const stats = {
    models: rows?.length ?? 0,
    ready: rows?.filter((r) => r.deployments.some((d) => d.status === "Ready")).length ?? 0,
    benchmarked: rows?.filter((r) => r.performance || r.accuracy).length ?? 0,
    catalogued: rows?.filter((r) => r.catalog).length ?? 0,
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2"><Workflow className="size-5" />{t("title")}</h1>
        <p className="text-muted-foreground mt-1">{t("subtitle")}</p>
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {(["models", "ready", "benchmarked", "catalogued"] as const).map((k) => (
          <Card key={k}>
            <CardContent className="pt-6">
              <div className="text-sm text-muted-foreground">{t(`stat.${k}`)}</div>
              <div className="text-2xl font-bold tabular-nums">{isLoading ? "–" : stats[k]}</div>
            </CardContent>
          </Card>
        ))}
      </div>

      {isLoading ? (
        <div className="h-40 animate-pulse rounded-lg bg-muted" />
      ) : !rows || rows.length === 0 ? (
        <div className="rounded-lg border border-dashed p-8 text-center text-muted-foreground">
          {t("empty")} <Link href="/admin/recipes/new" className="underline">{t("emptyCta")}</Link>
        </div>
      ) : (
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="min-w-[200px]">{t("colModel")}</TableHead>
                <TableHead>{t("colRecipe")}</TableHead>
                <TableHead>{t("colDeploy")}</TableHead>
                <TableHead>{t("colBenchmark")}</TableHead>
                <TableHead>{t("colCatalog")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((r) => <OverviewRow key={r.model_name} row={r} />)}
            </TableBody>
          </Table>
        </div>
      )}

      {unlinked.length > 0 && (
        <Card>
          <CardContent className="pt-6 space-y-3">
            <div>
              <div className="font-medium">{t("unlinkedTitle", { count: unlinked.length })}</div>
              <p className="text-sm text-muted-foreground">{t("unlinkedHint")}</p>
            </div>
            <div className="space-y-2">
              {unlinked.map((s) => (
                <Link key={s.id} href={`/admin/llmd/${s.id}`} className="flex flex-wrap items-center gap-2 rounded-md border p-3 text-sm hover:bg-muted/50">
                  <Badge variant="outline">llm-d</Badge>
                  <span className="font-medium">{s.name}</span>
                  <span className="text-xs text-muted-foreground">{t("unlinkedTarget", { model: s.target_model_name })}</span>
                  <code className="ml-auto text-[11px] text-muted-foreground">
                    {Object.keys(s.selector).length > 0
                      ? Object.entries(s.selector).map(([k, v]) => `${k}=${v}`).join(",")
                      : t("unlinkedNoSelector")}
                  </code>
                </Link>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function OverviewRow({ row }: { row: ServingOverviewRow }) {
  const t = useTranslations("serving");
  const engine = row.deployments[0]?.engine ?? row.recipes[0]?.engine;
  const gpuType = row.deployments.find((d) => d.gpu_type)?.gpu_type ?? null;
  const isPd = row.deployments.some((d) => d.serving_mode === "pd");
  const perf = row.performance;
  const perfLine = perf
    ? [fmt(perf.output_throughput, 0, " tok/s"), fmt(perf.mean_ttft_ms, 0, " ms TTFT")].filter(Boolean).join(" · ")
    : null;
  const acc = row.accuracy?.metric;

  return (
    <TableRow>
      <TableCell className="align-top">
        <div className="flex items-center gap-2">
          {row.catalog ? (
            <Link href={`/models/${row.model_name.split("/").map(encodeURIComponent).join("/")}`} className="font-medium hover:underline">
              {row.catalog.display_name || row.model_name}
            </Link>
          ) : (
            <span className="font-medium">{row.model_name}</span>
          )}
          {engine && <Badge variant="secondary" className="font-mono text-[10px] uppercase">{engine}</Badge>}
          {isPd && <Badge variant="outline" className="text-[10px]">P/D</Badge>}
          {gpuType && <Badge variant="outline" className="font-mono text-[10px]">{gpuType}</Badge>}
        </div>
        {row.catalog && row.catalog.display_name !== row.model_name && (
          <div className="font-mono text-xs text-muted-foreground">{row.model_name}</div>
        )}
      </TableCell>

      <TableCell className="align-top">
        {row.recipes.length > 0 ? (
          <div className="flex flex-wrap gap-1">
            {row.recipes.map((rc) => (
              <Link key={rc.id} href={`/admin/recipes/${rc.id}`}>
                <Badge variant="outline" className="hover:bg-muted">{rc.name}</Badge>
              </Link>
            ))}
          </div>
        ) : (
          <NextStep href="/admin/recipes/new" label={t("next.recipe")} />
        )}
      </TableCell>

      <TableCell className="align-top">
        {row.deployments.length === 0 && row.llmd_stacks.length === 0 ? (
          <NextStep href={row.recipes.length > 0 ? "/admin/recipes" : "/admin/recipes/new"} label={t("next.deploy")} />
        ) : (
          <div className="flex flex-col gap-1">
            {row.deployments.map((d) => (
              <Link key={d.id} href={`/admin/deployments/${d.id}`} className="inline-flex items-center gap-2 text-sm hover:underline">
                <Badge variant={deployVariant(d.status)}>{d.status}</Badge>
                <span className="tabular-nums text-muted-foreground">
                  {d.serving_mode === "pd" && d.pd_summary ? d.pd_summary : `${d.ready_replicas}/${d.replicas}`}
                </span>
                {d.litellm_model_id && <CheckCircle2 className="size-3.5 text-green-600" aria-label={t("registered")} />}
              </Link>
            ))}
            {row.llmd_stacks.map((s) => (
              <Link
                key={s.id}
                href={`/admin/llmd/${s.id}`}
                className="inline-flex items-center gap-2 text-sm hover:underline"
                title={Object.entries(s.selector).map(([k, v]) => `${k}=${v}`).join(",")}
              >
                <Badge variant={llmdVariant(s.health_status)}>llm-d</Badge>
                <span className="text-muted-foreground">{s.name}</span>
                {s.health_status && s.health_status !== "Healthy" && s.health_status !== "Unknown" && (
                  <span className="text-xs text-muted-foreground">{s.health_status}</span>
                )}
              </Link>
            ))}
          </div>
        )}
      </TableCell>

      <TableCell className="align-top">
        {!perf && !acc ? (
          <NextStep href="/admin/benchmarks/new" label={t("next.benchmark")} />
        ) : (
          <div className="flex flex-col gap-0.5 text-sm">
            {perf && (
              <Link href={`/admin/benchmarks/${perf.run_id}`} className="hover:underline">
                <span className="text-xs text-muted-foreground mr-1">{t("perf")}</span>
                <span className="font-mono text-xs">{perfLine || perf.tool}</span>
              </Link>
            )}
            {row.accuracy && (
              <Link href={`/admin/benchmarks/${row.accuracy.run_id}`} className="hover:underline">
                <span className="text-xs text-muted-foreground mr-1">{t("acc")}</span>
                <span className="font-mono text-xs">{acc ? `${acc.name} ${acc.value.toFixed(3)}` : row.accuracy.tool}</span>
              </Link>
            )}
          </div>
        )}
      </TableCell>

      <TableCell className="align-top">
        {row.catalog ? (
          <div className="flex items-center gap-2">
            <ModelStatusBadge status={row.catalog.status} />
            {!row.catalog.visible && <Badge variant="outline" className="text-[10px]">{t("hidden")}</Badge>}
          </div>
        ) : (
          <NextStep href="/admin/models" label={t("next.catalog")} />
        )}
      </TableCell>
    </TableRow>
  );
}
