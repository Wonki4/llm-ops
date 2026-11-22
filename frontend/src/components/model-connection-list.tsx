"use client";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useModelConnections } from "@/hooks/use-model-connections";
import { Table, TableHeader, TableHead, TableRow, TableBody, TableCell } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";

export function ModelConnectionList({modelName}: {modelName: string}) {
  const t = useTranslations("portalModels");
  const query = useModelConnections(modelName);
  if (query.isPending) return <p aria-busy="true">{t("loading")}</p>;
  if (query.isError) return <p role="alert">{query.error.message}</p>;
  return <div className="space-y-3"><p className="text-sm text-muted-foreground">{t("connectionHint")}</p>
    {!query.data.length ? <p>{t("noConnections")}</p> : <Table><TableHeader><TableRow>
      {["connectionId", "provider", "upstream", "endpoint", "source"].map(k => <TableHead key={k}>{t(k)}</TableHead>)}
    </TableRow></TableHeader><TableBody>{query.data.map(c => <TableRow key={c.id}>
      <TableCell className="font-mono text-xs">{c.id}</TableCell><TableCell>{c.provider}</TableCell>
      <TableCell className="font-mono">{c.model}</TableCell><TableCell className="break-all">{c.api_base || t("providerDefault")}</TableCell>
      <TableCell>{c.managed ? <Link className="underline" href={c.managed.kind === "deployment" ? `/admin/deployments/${c.managed.id}` : `/admin/deployments?cluster=${c.managed.cluster_id || "default"}&ns=${encodeURIComponent(c.managed.namespace)}`}>{t("serving")}</Link> : <Badge variant="outline">{t(c.db_model ? "database" : "configuration")}</Badge>}</TableCell>
    </TableRow>)}</TableBody></Table>}
  </div>;
}
