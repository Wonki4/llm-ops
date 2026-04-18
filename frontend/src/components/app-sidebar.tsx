"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { Users, Globe, ShieldCheck, Boxes, LayoutDashboard, LogOut, Calendar, BarChart3, Key, DollarSign, Settings, Database, Inbox, UserCog, Megaphone } from "lucide-react";
import { useMe } from "@/hooks/use-api";
import type { UserRole } from "@/types";

const navigation: { name: string; href: string; icon: typeof Users; roles: UserRole[] }[] = [
  { name: "공지사항", href: "/announcements", icon: Megaphone, roles: ["user", "team_admin", "super_user"] },
  { name: "모델 캘린더", href: "/models/calendar", icon: Calendar, roles: ["user", "team_admin", "super_user"] },
  { name: "모델 대시보드", href: "/models/dashboard", icon: BarChart3, roles: ["user", "team_admin", "super_user"] },
  { name: "내 팀", href: "/teams", icon: Users, roles: ["user", "team_admin", "super_user"] },
  { name: "팀 탐색", href: "/teams/discover", icon: Globe, roles: ["user", "team_admin", "super_user"] },
  { name: "내 전체 키", href: "/keys", icon: Key, roles: ["user", "team_admin", "super_user"] },
  { name: "내 요청", href: "/requests", icon: Inbox, roles: ["user", "team_admin", "super_user"] },
  { name: "요청 관리", href: "/admin/requests", icon: ShieldCheck, roles: ["team_admin", "super_user"] },
  { name: "관리자 대시보드", href: "/admin/models/dashboard", icon: BarChart3, roles: ["super_user"] },
  { name: "모델 관리", href: "/admin/models", icon: Boxes, roles: ["super_user"] },
  { name: "모델 캐시 관리", href: "/admin/catalog", icon: Database, roles: ["super_user"] },
  { name: "예산 관리", href: "/admin/budgets", icon: DollarSign, roles: ["super_user"] },
  { name: "사용자 관리", href: "/admin/users", icon: UserCog, roles: ["super_user"] },
  { name: "포털 설정", href: "/admin/settings", icon: Settings, roles: ["super_user"] },
];

export function AppSidebar() {
  const pathname = usePathname();
  const { data: me } = useMe();

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

          const renderLink = (item: (typeof navigation)[number]) => {
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
                {item.name}
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
                      관리자
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
        <a
          href="/api/auth/logout"
          className="flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-gray-600 transition-colors hover:bg-gray-50 hover:text-gray-900"
        >
          <LogOut className="h-4 w-4" />
          로그아웃
        </a>
      </div>
    </aside>
  );
}
