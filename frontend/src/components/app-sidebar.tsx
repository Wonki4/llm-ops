"use client";

import Link from "next/link";
import { useState, useSyncExternalStore } from "react";
import { activeNavigation, visibleNavigation, type NavigationGroup } from "@/lib/navigation";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";
import {
  ChevronDown,
  ChevronRight,
  Network,
  BarChart3,
  BookOpen,
  HardDrive,
  Boxes,
  Calendar,
  DollarSign,
  FlaskConical,
  Globe,
  Inbox,
  Key,
  Layers,
  LayoutDashboard,
  LogOut,
  Megaphone,
  ScrollText,
  Server,
  Settings,
  ShieldCheck,
  UserCog,
  Users,
  Workflow,
} from "lucide-react";
import { useMe } from "@/hooks/use-api";
import { useTeamScopes } from "@/hooks/use-serving-access";
import { LanguageSwitcher } from "@/components/language-switcher";
import { ThemeToggle } from "@/components/theme-toggle";
import type { UserRole } from "@/types";

const storageKey = "portal-navigation-groups";
function subscribe(listener: () => void) {
  window.addEventListener("storage", listener);
  window.addEventListener(storageKey, listener);
  return () => { window.removeEventListener("storage", listener); window.removeEventListener(storageKey, listener); };
}
function snapshot() {
  try { return localStorage.getItem(storageKey) || "{}"; } catch { return "{}"; }
}

export function AppSidebar() {
  const pathname = usePathname();
  const { data: me } = useMe();
  const scopes = useTeamScopes();
  const t = useTranslations("sidebar");

  const userRole: UserRole = me?.role ?? "user";

  const stored = useSyncExternalStore(subscribe, snapshot, () => "{}");
  let collapsed: Partial<Record<NavigationGroup, boolean>> = {};
  try { collapsed = JSON.parse(stored) ?? {}; } catch { /* ignore invalid saved preferences */ }
  const [override, setOverride] = useState<{path: string; group: NavigationGroup; collapsed: boolean} | null>(null);
  const visible = visibleNavigation(userRole, !scopes.isError && !!scopes.data?.length);
  const active = activeNavigation(pathname, visible);
  const icons = {guide: BookOpen, announcements: Megaphone, modelsCalendar: Calendar, modelsDashboard: BarChart3,
    myTeams: Users, discoverTeams: Globe, myKeys: Key, myRequests: Inbox, servingHome: Workflow,
    adminRecipes: ScrollText, infrastructure: HardDrive, adminDeployments: Server, adminBenchmarks: FlaskConical,
    adminModels: Boxes, routingGraph: Network, adminDashboard: BarChart3,
    adminTeams: Users, adminRequests: ShieldCheck, adminBudgets: DollarSign, adminUsers: UserCog, adminAccessGroups: Layers,
    adminFinance: DollarSign, adminPolicies: Settings};
  function toggle(group: NavigationGroup, open: boolean) {
    const next = {...collapsed, [group]: open};
    setOverride({path: pathname, group, collapsed: open});
    try { localStorage.setItem(storageKey, JSON.stringify(next)); window.dispatchEvent(new Event(storageKey)); } catch { /* storage unavailable */ }
  }
  return (
    <aside className="flex h-full w-64 flex-col border-r bg-sidebar text-sidebar-foreground">
      <div className="flex h-14 items-center border-b px-4">
        <Link href="/" className="flex items-center gap-2 font-bold text-lg">
          <LayoutDashboard className="h-5 w-5" />
          LLM Ops
        </Link>
      </div>
      <nav className="flex-1 overflow-y-auto p-3">
        <div className="space-y-1">
          {[undefined, "serving", "models", "people"].map(section => {
            const group = section as NavigationGroup | undefined;
            const items = visible.filter(item => item.group === group);
            if (!items.length) return null;
            const current = items.some(item => item.key === active);
            const open = !group || (override?.path === pathname && override.group === group ? !override.collapsed : current || !collapsed[group]);
            return <div key={group ?? "common"}>
              {group && <button type="button" aria-expanded={open} aria-controls={`nav-${group}`}
                onClick={() => toggle(group, open)} className="mt-5 mb-2 flex w-full items-center gap-2 px-3 py-1 text-xs font-semibold text-muted-foreground">
                {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
                {t(`${group}SectionLabel`)}
              </button>}
              <div id={group ? `nav-${group}` : undefined} hidden={!open} className="space-y-1">
                {items.map(item => {
                  const Icon = icons[item.key as keyof typeof icons] ?? Boxes;
                  return <Link key={item.key} href={item.href} aria-current={active === item.key ? "page" : undefined}
                    className={cn("flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                      active === item.key ? "bg-primary/10 text-primary" : "text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground")}>
                    <Icon className="size-4" />{t(item.key)}
                  </Link>;
                })}
              </div>
            </div>;
          })}
        </div>
      </nav>
      <div className="border-t p-3 space-y-2">
        <p className="text-xs text-muted-foreground truncate px-1">
          {me?.user_id || ""}
        </p>
        <ThemeToggle />
        <LanguageSwitcher />
        <a
          href="/api/auth/logout"
          className="flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-sidebar-foreground/70 transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
        >
          <LogOut className="h-4 w-4" />
          {t("logout")}
        </a>
      </div>
    </aside>
  );
}
