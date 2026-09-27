"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Network, Server } from "lucide-react";
import { useTranslations } from "next-intl";

import { cn } from "@/lib/utils";

const TABS = [
  { href: "/admin/deployments", key: "tabDeployments", icon: Server },
  { href: "/admin/llmd", key: "tabLlmd", icon: Network },
] as const;

/** Segmented links shared by the two "deploy" surfaces (K8s deployments, llm-d stacks). */
export function ServingDeployTabs() {
  const pathname = usePathname();
  const t = useTranslations("serving");
  return (
    <div className="inline-flex rounded-md border bg-muted/40 p-0.5">
      {TABS.map((tab) => {
        const active = pathname === tab.href || pathname.startsWith(tab.href + "/");
        return (
          <Link
            key={tab.href}
            href={tab.href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "inline-flex items-center gap-1.5 rounded px-3 py-1 text-sm transition-colors",
              active ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
            )}
          >
            <tab.icon className="size-3.5" />
            {t(tab.key)}
          </Link>
        );
      })}
    </div>
  );
}
