"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";
import {
  Users,
  Globe,
  ShieldCheck,
  Boxes,
  LayoutDashboard,
  LogOut,
  Calendar,
  BarChart3,
  Key,
  DollarSign,
  Settings,
  Inbox,
  UserCog,
  Megaphone,
} from "lucide-react";
import { useMe } from "@/hooks/use-api";
import { LanguageSwitcher } from "@/components/language-switcher";
import type { UserRole } from "@/types";

type NavItem = {
  key: string;
  href: string;
  icon: typeof Users;
  roles: UserRole[];
};

const navigation: NavItem[] = [
  { key: "announcements", href: "/announcements", icon: Megaphone, roles: ["user", "team_admin", "super_user"] },
  { key: "modelsCalendar", href: "/models/calendar", icon: Calendar, roles: ["user", "team_admin", "super_user"] },
  { key: "modelsDashboard", href: "/models/dashboard", icon: BarChart3, roles: ["user", "team_admin", "super_user"] },
  { key: "myTeams", href: "/teams", icon: Users, roles: ["user", "team_admin", "super_user"] },
  { key: "discoverTeams", href: "/teams/discover", icon: Globe, roles: ["user", "team_admin", "super_user"] },
  { key: "myKeys", href: "/keys", icon: Key, roles: ["user", "team_admin", "super_user"] },
  { key: "myRequests", href: "/requests", icon: Inbox, roles: ["user", "team_admin", "super_user"] },
  { key: "adminRequests", href: "/admin/requests", icon: ShieldCheck, roles: ["team_admin", "super_user"] },
  { key: "adminDashboard", href: "/admin/models/dashboard", icon: BarChart3, roles: ["super_user"] },
  { key: "adminModels", href: "/admin/models", icon: Boxes, roles: ["super_user"] },
  { key: "adminBudgets", href: "/admin/budgets", icon: DollarSign, roles: ["super_user"] },
  { key: "adminUsers", href: "/admin/users", icon: UserCog, roles: ["super_user"] },
  { key: "adminSettings", href: "/admin/settings", icon: Settings, roles: ["super_user"] },
];

export function AppSidebar() {
  const pathname = usePathname();
  const { data: me } = useMe();
  const t = useTranslations("sidebar");

  const userRole: UserRole = me?.role ?? "user";

  return (
    <aside className="flex h-full w-64 flex-col border-r bg-white">
      <div className="flex h-14 items-center border-b px-4">
        <Link href="/" className="flex items-center gap-2 font-bold text-lg">
          <LayoutDashboard className="h-5 w-5" />
          LLM Ops
        </Link>
      </div>
      <nav className="flex-1 overflow-y-auto p-3">
        {(() => {
          const visible = navigation.filter((item) => item.roles.includes(userRole));
          const regular = visible.filter((item) => item.roles.includes("user"));
          const admin = visible.filter((item) => !item.roles.includes("user"));

          const renderLink = (item: NavItem) => {
            const isActive =
              pathname === item.href ||
              (item.href !== "/admin/models" &&
                item.href !== "/keys" &&
                pathname.startsWith(item.href + "/") &&
                !navigation.some(
                  (other) =>
                    other.href !== item.href &&
                    other.href.startsWith(item.href + "/") &&
                    pathname.startsWith(other.href),
                ));
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                  isActive
                    ? "bg-gray-100 text-gray-900"
                    : "text-gray-600 hover:bg-gray-50 hover:text-gray-900",
                )}
              >
                <item.icon className="h-4 w-4" />
                {t(item.key)}
              </Link>
            );
          };

          return (
            <>
              <div className="space-y-1">{regular.map(renderLink)}</div>
              {admin.length > 0 && (
                <>
                  <div className="mt-5 mb-2 flex items-center gap-2 px-3">
                    <span className="text-xs font-semibold uppercase tracking-wider text-gray-400">
                      {t("adminSectionLabel")}
                    </span>
                    <div className="h-px flex-1 bg-gray-200" />
                  </div>
                  <div className="space-y-1">{admin.map(renderLink)}</div>
                </>
              )}
            </>
          );
        })()}
      </nav>
      <div className="border-t p-3 space-y-2">
        <p className="text-xs text-gray-500 truncate px-1">
          {me?.user_id || ""}
        </p>
        <LanguageSwitcher />
        <a
          href="/api/auth/logout"
          className="flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-gray-600 transition-colors hover:bg-gray-50 hover:text-gray-900"
        >
          <LogOut className="h-4 w-4" />
          {t("logout")}
        </a>
      </div>
    </aside>
  );
}
