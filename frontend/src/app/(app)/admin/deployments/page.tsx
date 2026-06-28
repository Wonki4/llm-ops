"use client";

import Link from "next/link";
import { Loader2, Server, ChevronRight, CheckCircle2 } from "lucide-react";
import { useTranslations } from "next-intl";

import { useModelDeployments } from "@/hooks/use-api";
import { Badge } from "@/components/ui/badge";
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

export default function DeploymentsPage() {
  const t = useTranslations("adminDeployments");
  const { data: deployments, isLoading } = useModelDeployments();

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2"><Server className="size-5" />{t("pageTitle")}</h1>
        <p className="text-muted-foreground mt-1">{t("pageDescription")}</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("listTitle")}</CardTitle>
          <CardDescription>{t("listHint")}</CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="flex justify-center py-12"><Loader2 className="size-5 animate-spin text-muted-foreground" /></div>
          ) : !deployments || deployments.length === 0 ? (
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
                    <TableHead className="text-right">{t("colGpu")}</TableHead>
                    <TableHead>{t("colNamespace")}</TableHead>
                    <TableHead>{t("colImage")}</TableHead>
                    <TableHead>{t("colRegistered")}</TableHead>
                    <TableHead></TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {deployments.map((d) => (
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
                      <TableCell className="text-right tabular-nums">{d.gpu_count}</TableCell>
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
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
