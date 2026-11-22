"use client";
import { Suspense } from "react";
import { useTranslations } from "next-intl";
import { AdminPage } from "@/components/admin-page";
import { AdminTeamList } from "@/components/admin-team-list";
export default function Page(){const t=useTranslations("portalTeams");return <AdminPage><div className="space-y-6"><h1 className="text-2xl font-bold">{t("title")}</h1><Suspense><AdminTeamList /></Suspense></div></AdminPage>;}
