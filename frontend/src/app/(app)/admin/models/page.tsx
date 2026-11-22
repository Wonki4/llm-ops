"use client";
import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { useModels, useCreateCatalogEntry } from "@/hooks/use-api";
import { useModelConnections } from "@/hooks/use-model-connections";
import { ModelConnectionDialog } from "@/components/model-connection-dialog";
import { CommonCacheSettings } from "@/components/common-cache-settings";
import { AdminPage } from "@/components/admin-page";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Table, TableHeader, TableHead, TableRow, TableBody, TableCell } from "@/components/ui/table";

function Models() {
  const t = useTranslations("portalModels"); const statusLabel = useTranslations("modelStatus");
  const query = useModels(); const connections = useModelConnections();
  const params = useSearchParams(); const router = useRouter();
  const search = params.get("q") || ""; const source = params.get("source") || "all";
  const status = params.get("status") || "all";
  const [connecting, setConnecting] = useState(false);
  const [creating, setCreating] = useState(false); const [name, setName] = useState("");
  const create = useCreateCatalogEntry();
  function filter(key: string, value: string) {const q = new URLSearchParams(params.toString());q.set(key,value);router.replace(`?${q}`, {scroll:false});}
  const origins = new Map<string, Set<string>>();
  for (const c of connections.data || []) {const values = origins.get(c.model_name) || new Set<string>(); values.add(c.origin);origins.set(c.model_name, values);}
  const models = query.data || [];
  const rows = models.filter(m => (!search || `${m.model_name} ${m.catalog?.display_name || ""}`.toLowerCase().includes(search.toLowerCase())) &&
    (status === "all" || m.catalog?.status === status) && (source === "all" || (source === "document" ? !m.litellm_info : origins.get(m.model_name)?.has(source))));
  return <div className="space-y-6">
    <div className="flex flex-wrap items-center justify-between gap-3"><h1 className="text-2xl font-bold">{t("title")}</h1><div className="flex gap-2"><Button onClick={()=>setConnecting(true)}>{t("newConnection")}</Button><Button variant="outline" onClick={() => setCreating(true)}>{t("newDocument")}</Button></div></div>
    <details className="rounded border p-3"><summary className="cursor-pointer text-sm">{t("commonCache")}</summary><div className="mt-3 max-w-2xl"><CommonCacheSettings /></div></details>
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">{[["total",models.length],["registered",models.filter(m=>m.litellm_info).length],["catalog",models.filter(m=>m.catalog).length],["connections",connections.data?.length ?? "—"]].map(([label,value]) => <div key={label} className="rounded-lg border bg-background p-4"><p className="text-sm text-muted-foreground">{t(String(label))}</p><p className="text-2xl font-semibold">{query.isPending ? "—" : value}</p></div>)}</div>
    {(query.isError || connections.isError) && <p role="alert">{query.error?.message || connections.error?.message}</p>}
    <div className="flex flex-wrap gap-3"><Input className="max-w-sm" aria-label={t("search")} placeholder={t("search")} value={search} onChange={e=>filter("q",e.target.value)} />
      <select className="rounded border bg-background p-2" aria-label={t("source")} value={source} onChange={e=>filter("source",e.target.value)}>{["all","cluster","cloud","unknown","document"].map(s=><option key={s} value={s}>{t(s)}</option>)}</select>
      <select className="rounded border bg-background p-2" aria-label={t("status")} value={status} onChange={e=>filter("status",e.target.value)}><option value="all">{t("all")}</option>{["testing","prerelease","lts","deprecating","deprecated"].map(s=><option key={s} value={s}>{statusLabel(s)}</option>)}</select>
    </div>
    <Table><TableHeader><TableRow>{["model","connections","catalog","source"].map(k=><TableHead key={k}>{t(k)}</TableHead>)}</TableRow></TableHeader>
      <TableBody>{rows.map(m=><TableRow key={m.model_name}><TableCell><Link className="font-medium underline" href={`/admin/models/detail/${m.model_name.split("/").map(encodeURIComponent).join("/")}`}>{m.catalog?.display_name || m.model_name}</Link><p className="font-mono text-xs text-muted-foreground">{m.model_name}</p></TableCell>
        <TableCell>{m.registrations?.length ?? (m.litellm_info ? 1 : 0)}</TableCell><TableCell>{m.catalog ? <Badge variant="secondary">{statusLabel(m.catalog.status)}</Badge> : t("unregistered")}</TableCell>
        <TableCell>{m.litellm_info ? [...(origins.get(m.model_name) || [])].map(o=><Badge className="mr-1" variant="outline" key={o}>{t(o)}</Badge>) : <Badge variant="outline">{t("document")}</Badge>}</TableCell></TableRow>)}</TableBody></Table>
    {!query.isPending && !rows.length && <p className="text-muted-foreground">{t("empty")}</p>}
    {connecting && <ModelConnectionDialog onClose={()=>setConnecting(false)} />}
    <Dialog open={creating} onOpenChange={setCreating}><DialogContent><DialogHeader><DialogTitle>{t("newDocument")}</DialogTitle></DialogHeader><p className="text-sm text-muted-foreground">{t("documentHint")}</p><Input aria-label={t("model")} placeholder={t("model")} value={name} onChange={e=>setName(e.target.value)} /><Button disabled={!name.trim() || create.isPending} onClick={async()=>{try {await create.mutateAsync({model_name:name.trim(),display_name:name.trim(),status:"testing",is_external:true});router.push(`/admin/models/detail/${name.trim().split("/").map(encodeURIComponent).join("/")}?tab=catalog`);} catch(e){toast.error((e as Error).message);}}}>{t("save")}</Button></DialogContent></Dialog>
  </div>;
}
export default function Page() {return <AdminPage><Suspense><Models /></Suspense></AdminPage>;}
