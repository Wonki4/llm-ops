"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { cn } from "@/lib/utils";

export function usePageTab(allowed: readonly string[], fallback: string, param = "tab") {
  const params = useSearchParams();
  const pathname = usePathname();
  const router = useRouter();
  const requested = params.get(param) ?? "";
  const tab = allowed.includes(requested) ? requested : fallback;
  function setTab(value: string) {
    const query = new URLSearchParams(params.toString());
    query.set(param, value);
    router.push(`${pathname}?${query}`, { scroll: false });
  }
  return [tab, setTab] as const;
}

export function PageTabs({ label, tabs, active }: {
  label: string; tabs: {id: string; label: string; href: string}[]; active: string;
}) {
  return <nav aria-label={label} className="flex flex-wrap gap-x-5 border-b text-sm">
    {tabs.map(tab => <Link key={tab.id} href={tab.href} aria-current={active === tab.id ? "page" : undefined}
      className={cn("border-b-2 px-1 py-3", active === tab.id ? "border-primary font-semibold" : "border-transparent text-muted-foreground")}>{tab.label}</Link>)}
  </nav>;
}
