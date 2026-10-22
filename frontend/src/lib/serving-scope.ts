/**
 * Cluster → namespace scope shared by the serving pages (home, deployments,
 * llm-d). Kept in the URL (`?cluster=&ns=`) so a filtered view can be linked.
 * "" means "all"; the portal's own cluster (cluster_id null) is PORTAL_CLUSTER.
 */
export const PORTAL_CLUSTER = "default";

export type ServingScope = { cluster: string; ns: string };
export type ScopedItem = { cluster_id?: string | null; namespace?: string | null };

export function clusterKey(clusterId: string | null | undefined): string {
  return clusterId || PORTAL_CLUSTER;
}

export function inScope(scope: ServingScope, item: ScopedItem): boolean {
  if (scope.cluster && clusterKey(item.cluster_id) !== scope.cluster) return false;
  if (scope.ns && item.namespace !== scope.ns) return false;
  return true;
}

export function isScoped(scope: ServingScope): boolean {
  return !!(scope.cluster || scope.ns);
}

/** `?cluster=…&ns=…` (empty string when unscoped), for links that keep the scope. */
export function scopeQuery(scope: ServingScope): string {
  const qs = new URLSearchParams();
  if (scope.cluster) qs.set("cluster", scope.cluster);
  if (scope.ns) qs.set("ns", scope.ns);
  const s = qs.toString();
  return s ? `?${s}` : "";
}

/** Namespaces that occur in `items` within the chosen cluster, sorted. */
export function namespacesIn(items: ScopedItem[], cluster: string): string[] {
  const out = new Set<string>();
  for (const it of items) {
    if (it.namespace && (!cluster || clusterKey(it.cluster_id) === cluster)) out.add(it.namespace);
  }
  return [...out].sort();
}
