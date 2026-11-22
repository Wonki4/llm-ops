"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { useAdminTeams } from "@/hooks/use-admin-teams";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
export function AdminTeamList({modelName,accessGroup}: {modelName?:string;accessGroup?:string}) {
  const t=useTranslations("portalTeams");const params=useSearchParams();const router=useRouter();const prefix=modelName||accessGroup?"teams_":"";
  const q=params.get(prefix+"q")||"";const page=Math.max(1,Number(params.get(prefix+"page"))||1);
  const query=useAdminTeams({q,page,model_name:modelName,access_group:accessGroup});
  function change(key:string,value:string) {const next=new URLSearchParams(params.toString());next.set(prefix+key,value);if(key==="q")next.delete(prefix+"page");router.replace(`?${next}`,{scroll:false});}
  return <div className="space-y-4"><div className="flex gap-3"><Input className="max-w-sm" aria-label={t("search")} placeholder={t("search")} value={q} onChange={e=>change("q",e.target.value)} />{(modelName||accessGroup) && <Button variant="outline" asChild><Link href="/admin/teams">{t("assignTeam")}</Link></Button>}</div>
    {query.isError && <p role="alert">{query.error.message}</p>}
    <table className="w-full text-sm"><thead><tr className="border-b text-left">{["team","members","directAccess","effective"].map(k=><th className="py-3" key={k}>{t(k)}</th>)}</tr></thead><tbody>{query.data?.teams.map(team=><tr key={team.team_id} className="border-b"><td className="py-3"><Link className="font-medium underline" href={`/admin/teams/${encodeURIComponent(team.team_id)}${modelName||accessGroup?"?tab=models":""}`}>{team.team_alias || team.team_id}</Link><p className="text-xs text-muted-foreground">{team.team_id}</p></td><td>{team.member_count}</td><td>{t(team.access.mode)}</td><td>{modelName?team.access.effective_models.find(m=>m.model_name===modelName)?.via.map(v=>["all","direct"].includes(v)?t(v):v).join(", "):t("modelCount",{count:team.access.effective_models.length})}</td></tr>)}</tbody></table>
    {query.isPending && <p aria-busy="true">…</p>}{query.data && !query.data.teams.length && <p>{t("empty")}</p>}
    <div className="flex items-center gap-3"><Button variant="outline" disabled={page<=1} onClick={()=>change("page",String(page-1))}>{t("previous")}</Button><span>{page} / {Math.max(1,Math.ceil((query.data?.total||0)/(query.data?.page_size||25)))}</span><Button variant="outline" disabled={!query.data||page*query.data.page_size>=query.data.total} onClick={()=>change("page",String(page+1))}>{t("next")}</Button></div>
  </div>;
}
