"use client";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { ModelConnectionDialog } from "@/components/model-connection-dialog";
import { ModelApiBaseEditor } from "@/components/model-api-base-editor";
import type { ModelConnection } from "@/hooks/use-model-connections";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useModelConnections } from "@/hooks/use-model-connections";
import { Table, TableHeader, TableHead, TableRow, TableBody, TableCell } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";

export function ModelConnectionList({modelName}: {modelName: string}) {
  const t = useTranslations("portalModels");
  const query = useModelConnections(modelName);
  const cache = useQueryClient();
  const [editing, setEditing] = useState<ModelConnection | "new" | null>(null);
  const [deleting, setDeleting] = useState(false);
  if (query.isPending) return <p aria-busy="true">{t("loading")}</p>;
  if (query.isError) return <p role="alert">{query.error.message}</p>;
  return <div className="space-y-3"><Button onClick={()=>setEditing("new")}>{t("newConnection")}</Button>{editing && <ModelConnectionDialog modelName={modelName} connection={editing === "new" ? undefined : editing} onClose={()=>setEditing(null)} />}<p className="text-sm text-muted-foreground">{t("connectionHint")}</p>
    {!query.data.length ? <p>{t("noConnections")}</p> : <Table className="min-w-[800px] table-fixed"><TableHeader><TableRow>
      {["connectionId", "provider", "upstream", "endpoint", "source", "actions"].map(k => <TableHead key={k} className={k === "endpoint" ? "w-[24%]" : k === "provider" ? "w-[10%]" : undefined}>{t(k)}</TableHead>)}
    </TableRow></TableHeader><TableBody>{query.data.map(c => <TableRow key={c.id}>
      <TableCell className="whitespace-normal break-all font-mono text-xs">{c.id}</TableCell><TableCell className="whitespace-normal break-all">{c.provider}</TableCell>
      <TableCell className="whitespace-normal break-all font-mono">{c.model}</TableCell><TableCell className="whitespace-normal break-all">{c.api_base || t("providerDefault")}</TableCell>
      <TableCell>{c.managed ? <Link className="underline" href={c.managed.kind === "deployment" ? `/admin/deployments/${c.managed.id}` : `/admin/deployments?cluster=${c.managed.cluster_id || "default"}&ns=${encodeURIComponent(c.managed.namespace)}`}>{t("serving")}</Link> : <Badge variant="outline">{t(c.db_model ? "database" : "configuration")}</Badge>}</TableCell>
      <TableCell>{c.editable ? <div className="flex flex-wrap gap-2"><Button variant="outline" size="sm" onClick={()=>setEditing(c)}>{t("editConnection")}</Button><Button variant="destructive" size="sm" disabled={deleting} onClick={async()=>{if(!window.confirm(t("deleteConnectionConfirm",{id:c.id}))) return;setDeleting(true);try{await apiFetch(`/api/admin/model-connections/${encodeURIComponent(c.id)}`,{method:"DELETE"});await cache.invalidateQueries();}catch(e){toast.error((e as Error).message);}finally{setDeleting(false);}}}>{t("deleteConnection")}</Button></div> : <ModelApiBaseEditor connection={c} />}</TableCell>
    </TableRow>)}</TableBody></Table>}
  </div>;
}
