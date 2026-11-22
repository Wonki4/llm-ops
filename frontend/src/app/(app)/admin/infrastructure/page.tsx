"use client";
import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { AdminPage } from "@/components/admin-page";
import { PageTabs, usePageTab } from "@/components/page-tabs";
import { ClusterSettingsTab } from "@/components/cluster-settings-tab";
import { GpuProfilesCard } from "@/components/gpu-profiles-card";
import { PodLabelsCard } from "@/components/pod-labels-card";
import { StorageList } from "@/components/storage-list";

const tabs = ["clusters", "storages", "gpu", "pods"] as const;
function Infrastructure() {
  const t = useTranslations("portalNavigation");
  const params = useSearchParams();
  const [tab] = usePageTab(tabs, "clusters");
  return <div className="space-y-6">
    <h1 className="text-2xl font-bold">{t("infrastructure")}</h1>
    <PageTabs label={t("infrastructure")} active={tab} tabs={tabs.map(id => {
      const query = new URLSearchParams(params.toString()); query.set("tab", id);
      return {id, label: t(id), href: `/admin/infrastructure?${query}`};
    })} />
    {tab === "clusters" && <ClusterSettingsTab />}
    {tab === "storages" && <StorageList />}
    {tab === "gpu" && <GpuProfilesCard />}
    {tab === "pods" && <PodLabelsCard />}
  </div>;
}
export default function Page() {
  return <AdminPage><Suspense><Infrastructure /></Suspense></AdminPage>;
}
