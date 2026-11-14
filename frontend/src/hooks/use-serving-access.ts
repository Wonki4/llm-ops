"use client";

import { useQuery } from "@tanstack/react-query";
import { useMe } from "@/hooks/use-api";
import { apiFetch } from "@/lib/api";
import { clusterKey } from "@/lib/serving-scope";

export type ServingRole = "viewer" | "operator";
export interface TeamScope {
  cluster_id: string | null;
  cluster_name: string | null;
  namespace: string;
  role: ServingRole;
}

export function useTeamScopes() {
  const { data: me } = useMe();
  return useQuery({
    queryKey: ["team-serving-scopes", me?.user_id],
    queryFn: () => apiFetch<{scopes: TeamScope[]}>("/api/serving/scopes").then(r => r.scopes),
    enabled: !!me && me.role !== "super_user",
    staleTime: 0,
  });
}

export function scopeKey(scope: TeamScope) {
  return clusterKey(scope.cluster_id) + "/" + scope.namespace;
}

export function teamScopeParams(scope: TeamScope) {
  const params = new URLSearchParams({ namespace: scope.namespace });
  if (scope.cluster_id) params.set("cluster_id", scope.cluster_id);
  return params;
}
