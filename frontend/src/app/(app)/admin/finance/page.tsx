"use client";
import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { AdminPage } from "@/components/admin-page";
import { PageTabs, usePageTab } from "@/components/page-tabs";
import { BudgetManagement } from "@/components/budgets-management";
import { UsageManagement } from "@/components/usage-management";
function Finance(){const t=useTranslations("portalPolicies");const q=useSearchParams();const [tab]=usePageTab(["budget","usage"],"budget");return <div className="space-y-6"><PageTabs label={t("finance")} active={tab} tabs={["budget","usage"].map(id=>{const p=new URLSearchParams(q.toString());p.set("tab",id);return {id,label:t(id),href:`?${p}`};})} />{tab==="budget"?<BudgetManagement />:<UsageManagement />}</div>;}
export default function Page(){return <AdminPage><Suspense><Finance /></Suspense></AdminPage>;}
