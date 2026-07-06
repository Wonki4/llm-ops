"use client";

import { Fragment, useState } from "react";
import Link from "next/link";
import { AlertTriangle, CheckCircle2, ChevronRight, Loader2, Server } from "lucide-react";
import { useTranslations } from "next-intl";

import {
  ExternalServing,
  useExternalServings,
  useModelDeployments,
  useUnregisterExternalServing,
} from "@/hooks/use-api";
import { ExternalServingRegisterDialog } from "@/components/external-serving-register-dialog";
import { Badge } from "@/components/ui/badge";
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

export default function DeploymentsPage() {
  const t = useTranslations("adminDeployments");
  const { data: deployments, isLoading } = useModelDeployments();
  const { data: external, isLoading: externalLoading } = useExternalServings();
  const unregister = useUnregisterExternalServing();

  const [registerTarget, setRegisterTarget] = useState<ExternalServing | null>(null);
  const [expandedKey, setExpandedKey] = useState<string | null>(null);

  const loading = isLoading || externalLoading;
  const servings = external?.servings ?? [];
  const scanErrors = external?.errors ?? [];
  const isEmpty = (!deployments || deployments.length === 0) && servings.length === 0;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2"><Server className="size-5" />{t("pageTitle")}</h1>
        <p className="text-muted-foreground mt-1">{t("pageDescription")}</p>
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
          ) : isEmpty ? (
            <div className="flex flex-col items-center justify-center rounded-lg border border-dashed py-16">
              <Server className="size-10 text-muted-foreground mb-3" />
              <p className="text-muted-foreground">{t("empty")}</p>
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
                  {(deployments ?? []).map((d) => (
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
                      </TableCell>
                      <TableCell><StatusBadge status={d.status} /></TableCell>
                      <TableCell className="text-right tabular-nums">{d.ready_replicas}/{d.replicas}</TableCell>
                      <TableCell className="text-muted-foreground text-xs">—</TableCell>
                      <TableCell className="font-mono text-xs">{d.namespace}</TableCell>
                      <TableCell className="font-mono text-xs max-w-[200px] truncate" title={d.image}>{d.image}</TableCell>
                      <TableCell>
                        {d.litellm_model_id ? (
                          <CheckCircle2 className="size-4 text-green-600" />
                        ) : (
                          <span className="text-xs text-muted-foreground">—</span>
                        )}
                      </TableCell>
                      <TableCell><ChevronRight className="size-4 text-muted-foreground" /></TableCell>
                    </TableRow>
                  ))}
                  {servings.map((s) => {
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
                          <TableCell />
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
                              </div>
                            </TableCell>
                          </TableRow>
                        )}
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
