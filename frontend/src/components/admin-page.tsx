"use client";
import type { ReactNode } from "react";
import { useTranslations } from "next-intl";
import { useMe } from "@/hooks/use-api";

/** UI gate complements (never replaces) the API's require_super_user dependency. */
export function AdminPage({ children }: {children: ReactNode}) {
  const me = useMe();
  const t = useTranslations("portalNavigation");
  if (me.isLoading) return <div className="h-48 animate-pulse rounded bg-muted" aria-busy="true" />;
  if (me.isError) return <p role="alert">{me.error.message}</p>;
  if (me.data?.role !== "super_user") return <p role="alert">{t("adminOnly")}</p>;
  return children;
}
