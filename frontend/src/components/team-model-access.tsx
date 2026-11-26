"use client";
import { useState } from "react";
import Link from "next/link";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { useTeamAccess, type TeamAccess } from "@/hooks/use-admin-teams";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { FormSelect, FormSelectItem } from "@/components/ui/form-select";

export function TeamModelAccess({teamId}: {teamId:string}) {
  const query = useTeamAccess(teamId); const t=useTranslations("portalTeams"); const [edit,setEdit]=useState(false);
  if(query.isError) return <p role="alert">{query.error.message}</p>;
  if(!query.data) return <p aria-busy="true">…</p>;
  const data=query.data;
  return <div className="space-y-5"><div className="flex justify-between gap-4"><div><h2 className="font-semibold">{t("directAccess")}: {t(data.mode)}</h2><p className="text-sm text-muted-foreground">{t("accessHint")}</p></div><Button onClick={()=>setEdit(true)}>{t("edit")}</Button></div>
    <div className="flex flex-wrap gap-2">{data.selection.map(s=><span className="rounded border p-2 text-sm" key={s}>{s}{s in data.available_groups && ` (${t("group")})`}</span>)}</div>
    <section><h3 className="font-semibold">{t("inherited")}</h3><p className="text-sm text-muted-foreground">{t("inheritedHint")}</p>{data.inherited_groups.map(g=><p key={g.access_group_id}>{g.access_group_name}: {g.models.join(", ")}</p>)}</section>
    <section><h3 className="font-semibold">{t("effective")}</h3>{!data.effective_models.length && <p>{t("noneEffective")}</p>}
    <table className="w-full text-sm"><thead><tr className="border-b text-left"><th className="py-2">{t("model")}</th><th>{t("source")}</th></tr></thead><tbody>{data.effective_models.map(m=><tr className="border-b" key={m.model_name}><td className="py-2"><Link className="underline" href={`/admin/models/detail/${m.model_name.split("/").map(encodeURIComponent).join("/")}?tab=assignments`}>{m.model_name}</Link></td><td>{m.via.map(v=>v==="direct"||v==="all"?t(v):v.startsWith("inherited:")?`${t("inherited")}: ${v.slice(10)}`:`${t("group")}: ${v.slice(6)}`).join(", ")}</td></tr>)}</tbody></table></section>
    {edit && <AccessEditor teamId={teamId} initial={data} onClose={()=>setEdit(false)} />}
  </div>;
}
function AccessEditor({teamId,initial,onClose}:{teamId:string;initial:TeamAccess;onClose:()=>void}) {
  const t=useTranslations("portalTeams");const cache=useQueryClient();const [mode,setMode]=useState(initial.mode);const [selection,setSelection]=useState(initial.selection);const [q,setQ]=useState("");const [pending,setPending]=useState(false);
  function toggle(name:string) {setSelection(v=>v.includes(name)?v.filter(n=>n!==name):[...v,name]);}
  return <Dialog open onOpenChange={o=>{if(!o&&!pending)onClose();}}><DialogContent className="max-h-[85vh] overflow-y-auto"><DialogHeader><DialogTitle>{t("modelAccess")}</DialogTitle><DialogDescription>{t("accessHint")}</DialogDescription></DialogHeader>
    <FormSelect aria-label={t("directAccess")} value={mode} onValueChange={selectedValue=>setMode(selectedValue as TeamAccess["mode"])}>{["none","custom","all"].map(m=><FormSelectItem value={m} key={m}>{t(m)}</FormSelectItem>)}</FormSelect>
    {mode==="custom" && <><Input placeholder={t("search")} value={q} onChange={e=>setQ(e.target.value)} />{[["model",initial.available_models],["group",Object.keys(initial.available_groups)]] .map(([kind,names])=><fieldset key={String(kind)}><legend className="font-semibold">{t(String(kind))}</legend><div className="max-h-44 overflow-y-auto">{(names as string[]).filter(n=>n.toLowerCase().includes(q.toLowerCase())).map(n=><label className="flex items-center gap-2 py-1" key={n}><input type="checkbox" checked={selection.includes(n)} onChange={()=>toggle(n)} />{n}</label>)}</div></fieldset>)}</>}
    <Button disabled={pending || (mode==="custom"&&!selection.length)} onClick={async()=>{setPending(true);try{await apiFetch(`/api/admin/teams/${encodeURIComponent(teamId)}/access`,{method:"PUT",body:JSON.stringify({mode,selection,expected_models:initial.models})});await cache.invalidateQueries();onClose();}catch(e){toast.error((e as Error).message);}finally{setPending(false);}}}>{t("save")}</Button>
  </DialogContent></Dialog>;
}
