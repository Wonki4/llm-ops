"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";
import { VALIDATIONS } from "@/lib/recipe-validation";

export function BenchmarkNavigation() {
  const pathname = usePathname();
  const t = useTranslations("recipeValidation");
  const validation = pathname.startsWith(VALIDATIONS);
  return <nav aria-label={t("title")} className="flex gap-5 border-b text-sm">
    {[["/admin/benchmarks", "measurements", !validation], [VALIDATIONS, "title", validation]].map(([href, label, selected]) =>
      <Link key={String(href)} href={String(href)} aria-current={selected ? "page" : undefined}
        className={`border-b-2 px-1 py-3 ${selected ? "border-primary font-semibold" : "border-transparent text-muted-foreground"}`}>{t(String(label))}</Link>)}
  </nav>;
}
