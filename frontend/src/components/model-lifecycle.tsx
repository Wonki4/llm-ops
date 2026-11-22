"use client";
import { useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { useAllModelStatusHistory } from "@/hooks/use-api";
import { PageTabs, usePageTab } from "@/components/page-tabs";
import { ModelSchedule } from "@/components/model-schedule";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useLocaleTag, parseServerDate } from "@/lib/locale";

function Changes() {
  const t = useTranslations("portalModels"); const status = useTranslations("modelStatus"); const locale = useLocaleTag();
  const params = useSearchParams(); const router = useRouter(); const q = params.get("q") || "";
  const page = Math.max(1, Number(params.get("page")) || 1);
  const query = useAllModelStatusHistory({limit: 25, offset: (page-1)*25, model_name:q || undefined});
  function update(key:string,value:string) {const next = new URLSearchParams(params.toString());next.set(key,value);if(key==="q")next.set("page","1");router.replace(`?${next}`,{scroll:false});}
  return <div className="space-y-4"><Input className="max-w-sm" aria-label={t("search")} placeholder={t("search")} value={q} onChange={e=>update("q",e.target.value)} />
    {query.isError && <p role="alert">{query.error.message}</p>}
    <Table><TableHeader><TableRow>{["model","previous","status","actor","time"].map(k=><TableHead key={k}>{t(k)}</TableHead>)}</TableRow></TableHeader><TableBody>{query.data?.history.map(h=><TableRow key={h.id}><TableCell>{h.model_name}</TableCell><TableCell>{h.previous_status ? status(h.previous_status) : "—"}</TableCell><TableCell>{status(h.new_status)}</TableCell><TableCell>{h.changed_by}</TableCell><TableCell>{parseServerDate(h.changed_at).toLocaleString(locale)}</TableCell></TableRow>)}</TableBody></Table>
    <div className="flex gap-3"><Button variant="outline" disabled={page===1} onClick={()=>update("page",String(page-1))}>{t("previous")}</Button><span>{page}</span><Button variant="outline" disabled={page*25 >= (query.data?.total || 0)} onClick={()=>update("page",String(page+1))}>{t("next")}</Button></div>
  </div>;
}
export function ModelLifecycle() {
  const t=useTranslations("portalModels"); const params=useSearchParams(); const [tab]=usePageTab(["schedule","history"],"schedule");
  return <div className="space-y-6"><h1 className="text-2xl font-bold">{t("lifecycle")}</h1><PageTabs label={t("lifecycle")} active={tab} tabs={["schedule","history"].map(id=>{const q=new URLSearchParams(params.toString());q.set("tab",id);return {id,label:t(id),href:`?${q}`};})} />{tab==="schedule"?<ModelSchedule />:<Changes />}</div>;
}
