"use client";

import { ModelDemandPanel } from "@/components/model-demand";
import { Suspense } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { useTeamDetail } from "@/hooks/use-api";
import { AdminPage } from "@/components/admin-page";
import { PageTabs, usePageTab } from "@/components/page-tabs";
import { MembersTab, UsageTab, TeamSettingsTab } from "@/components/team-detail";
import { TeamServingPermissions } from "@/components/serving-access-tab";
import { TeamModelAccess } from "@/components/team-model-access";
const tabs=["members","models","serving","budget","usage","settings"];
function Detail(){
  const {teamId}=useParams<{teamId:string}>();const t=useTranslations("portalTeams");const params=useSearchParams();const [tab]=usePageTab(tabs,"members");const query=useTeamDetail(teamId);
  if(query.isError)return <p role="alert">{query.error.message}</p>;if(!query.data)return <p aria-busy="true">…</p>;
  const data=query.data;const team=data.team;
  return <div className="space-y-6"><Link className="underline" href="/admin/teams">← {t("title")}</Link><h1 className="text-2xl font-bold">{team.team_alias || teamId}</h1><p className="text-sm text-muted-foreground">{teamId}</p>
    <PageTabs label={t("title")} active={tab} tabs={tabs.map(id=>{const q=new URLSearchParams(params.toString());q.set("tab",id);return {id,label:t(id),href:`?${q}`};})} />
    {tab==="members" && <MembersTab teamId={teamId} />}{tab==="models" && <TeamModelAccess teamId={teamId} />}{tab==="serving" && <TeamServingPermissions teamId={teamId} name={team.team_alias} />}{tab==="usage" && <UsageTab teamId={teamId} />}
    {tab==="budget" && <p>{t("teamBudget")}: ${team.spend.toFixed(2)} / {team.max_budget==null?t("unlimited"):`$${team.max_budget}`}</p>}
    {tab==="budget" && <ModelDemandPanel scope="team" subject={teamId} admin />}
    {(tab==="settings"||tab==="budget") && <TeamSettingsTab key={`${teamId}-${tab}`} section={tab==="budget"?"budget":"general"} teamId={teamId} description={team.description??null} defaultMemberBudget={data.default_member_budget??null} defaultMemberTpm={data.default_member_tpm_limit??null} defaultMemberRpm={data.default_member_rpm_limit??null} membershipDuration={data.membership_duration??null} defaultTpmLimit={data.default_tpm_limit??null} defaultRpmLimit={data.default_rpm_limit??null} budgetRequestMaxAmount={data.budget_request_max_amount} budgetRequestAllowedDays={data.budget_request_allowed_days} />}
  </div>;
}
export default function Page(){return <AdminPage><Suspense><Detail /></Suspense></AdminPage>;}
