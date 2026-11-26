import type { UserRole } from "@/types";

export type NavigationGroup = "serving" | "models" | "people";
export interface NavigationItem {
  key: string;
  href: string;
  roles: UserRole[];
  group?: NavigationGroup;
  matches?: string[];
}
const everyone: UserRole[] = ["user", "team_admin", "super_user"];
const admin: UserRole[] = ["super_user"];

// Only link completed routes. New domain pages replace legacy entries as they ship.
export const navigation: NavigationItem[] = [
  { key: "guide", href: "/guide", roles: everyone },
  { key: "announcements", href: "/announcements", roles: everyone },
  { key: "modelsCalendar", href: "/models/calendar", roles: everyone },
  { key: "modelsDashboard", href: "/models/dashboard", roles: everyone },
  { key: "myTeams", href: "/teams", roles: everyone },
  { key: "discoverTeams", href: "/teams/discover", roles: everyone },
  { key: "myKeys", href: "/keys", roles: everyone },
  { key: "myRequests", href: "/requests", roles: everyone },
  { key: "servingHome", href: "/admin/serving", roles: admin, group: "serving" },
  { key: "adminDeployments", href: "/admin/deployments", matches: ["/admin/llmd"], roles: admin, group: "serving" },
  { key: "adminRecipes", href: "/admin/recipes", roles: admin, group: "serving" },
  { key: "adminBenchmarks", href: "/admin/benchmarks", roles: admin, group: "serving" },
  { key: "infrastructure", href: "/admin/infrastructure", matches: ["/admin/storages"], roles: admin, group: "serving" },
  { key: "adminModels", href: "/admin/models", roles: admin, group: "models" },
  { key: "routingGraph", href: "/admin/serving/topology", roles: admin, group: "models" },
  { key: "adminTeams", href: "/admin/teams", roles: admin, group: "people" },
  { key: "adminUsers", href: "/admin/users", roles: admin, group: "people" },
  { key: "adminAccessGroups", href: "/admin/access-groups", roles: admin, group: "people" },
  { key: "adminRequests", href: "/admin/requests", roles: ["team_admin", "super_user"], group: "people" },
  { key: "adminFinance", href: "/admin/finance", matches: ["/admin/budgets", "/admin/usage"], roles: admin, group: "people" },
  { key: "adminPolicies", href: "/admin/policies", roles: admin, group: "people" },
];

export function visibleNavigation(role: UserRole, servingAccess: boolean) {
  return navigation.filter(item => item.roles.includes(role) || (item.key === "servingHome" && servingAccess));
}

/** Longest matching path wins, including children and legacy aliases. */
export function activeNavigation(path: string, items: NavigationItem[]) {
  return items.flatMap(item => [item.href, ...(item.matches ?? [])].map(prefix => ({item, prefix})))
    .filter(({prefix}) => path === prefix || path.startsWith(prefix + "/"))
    .sort((a, b) => b.prefix.length - a.prefix.length)[0]?.item.key;
}
