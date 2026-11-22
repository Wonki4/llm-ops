"use client";
import { Suspense } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { useModels, useModelPersonalUsers } from "@/hooks/use-api";
import { useModelConnections } from "@/hooks/use-model-connections";
import { AdminTeamList } from "@/components/admin-team-list";
import { AdminPage } from "@/components/admin-page";
import { PageTabs, usePageTab } from "@/components/page-tabs";
import { ModelCatalogEditor } from "@/components/model-catalog-editor";
import { ModelConnectionList } from "@/components/model-connection-list";
import { ModelCacheSection } from "@/components/model-cache-section";
import { ModelCostScheduleSection } from "@/components/model-cost-schedule-section";
import { formatModelPrice } from "@/lib/model-pricing";
import { accessGroupsOf } from "@/lib/access-groups";

const tabs = ["connections", "catalog", "pricing", "cache", "assignments"];
function Pricing({name}: {name:string}) {
  const t = useTranslations("portalModels");
  const query = useModelConnections(name);
  return <div className="space-y-6"><p className="text-sm text-muted-foreground">{t("pricingHint")}</p>
    {query.isError && <p role="alert">{query.error.message}</p>}
    {query.data?.map(c => <div key={c.id} className="rounded border p-4"><p className="font-mono text-sm">{c.id} · {c.model}</p>
      <div className="mt-2 flex flex-wrap gap-6">{["input_cost_per_token", "output_cost_per_token", "cache_read_input_token_cost"].map(k => <span key={k}>{t(k)}: {formatModelPrice(c.prices[k])}</span>)}</div></div>)}
    <ModelCostScheduleSection modelName={name} /></div>;
}
function ModelAssignments({name, groups}: {name:string; groups:string[]}) {
  const t = useTranslations("portalModels"); const users = useModelPersonalUsers(name);
  return <div className="space-y-5"><p>{t("assignmentHint")}</p>
    <div><h2 className="font-semibold">{t("groups")}</h2>{groups.map(g => <Link className="mr-3 underline" key={g} href={`/admin/access-groups?group=${encodeURIComponent(g)}`}>{g}</Link>)}</div>
    <AdminTeamList modelName={name} />
    <div><h2 className="font-semibold">{t("personalUsers")}</h2>{users.isError && <p role="alert">{users.error.message}</p>}
      {users.data?.map(u => <div key={u.user_id} className="flex gap-4 border-b py-2"><Link className="underline" href={`/admin/users/${encodeURIComponent(u.user_id)}`}>{u.user_id}</Link><span>{u.via.join(", ")}</span></div>)}</div>
  </div>;
}
function Detail() {
  const t = useTranslations("portalModels");
  const {modelName} = useParams<{modelName:string[]}>(); const name = modelName.join("/");
  const params = useSearchParams(); const [tab] = usePageTab(tabs, "connections");
  const query = useModels(); const model = query.data?.find(m => m.model_name === name);
  if (query.isPending) return <p aria-busy="true">{t("loading")}</p>;
  if (query.isError) return <p role="alert">{query.error.message}</p>;
  if (!model) return <p>{t("notFound")}</p>;
  const base = `/admin/models/${modelName.map(encodeURIComponent).join("/")}`;
  return <div className="space-y-6"><Link className="text-sm underline" href="/admin/models">← {t("title")}</Link>
    <div><h1 className="text-2xl font-bold">{model.catalog?.display_name || name}</h1><p className="font-mono text-sm text-muted-foreground">{name}</p></div>
    <PageTabs label={t("title")} active={tab} tabs={tabs.map(id => {const q = new URLSearchParams(params.toString());q.set("tab",id);return {id,label:t(id),href:`${base}?${q}`};})} />
    {tab === "connections" && <ModelConnectionList modelName={name} />}
    {tab === "catalog" && <ModelCatalogEditor key={model.catalog?.updated_at || "new"} model={model} />}
    {tab === "pricing" && <Pricing name={name} />}
    {tab === "cache" && <ModelCacheSection modelName={name} />}
    {tab === "assignments" && <ModelAssignments name={name} groups={accessGroupsOf(model)} />}
  </div>;
}
export default function Page() {return <AdminPage><Suspense><Detail /></Suspense></AdminPage>;}
