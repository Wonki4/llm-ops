import { redirect } from "next/navigation";

export default async function LegacyModelHistoryPage({
  searchParams,
}: {
  searchParams: Promise<{ tab?: string; q?: string }>;
}) {
  const { tab, q } = await searchParams;
  if (tab === "history") {
    redirect(q ? `/admin/models?${new URLSearchParams({ q })}` : "/admin/models");
  }
  redirect("/models/calendar");
}
