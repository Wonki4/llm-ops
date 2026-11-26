"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { useMe } from "@/hooks/use-api";

export default function RecipeLayout({ children }: { children: ReactNode }) {
  const user = useMe();
  const t = useTranslations("portalNavigation");
  if (user.isPending) return <div aria-busy="true" className="h-32 animate-pulse rounded-lg bg-muted" />;
  if (user.isError) return <p role="alert" className="text-destructive">{user.error.message}</p>;
  if (user.data?.role !== "super_user") return <p role="alert">{t("adminOnly")}</p>;
  return children;
}
