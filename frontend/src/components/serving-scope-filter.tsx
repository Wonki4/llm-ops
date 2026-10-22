"use client";

import { useCallback, useMemo } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { X } from "lucide-react";
import { useTranslations } from "next-intl";

import { useK8sClusters } from "@/hooks/use-api";
import { Button } from "@/components/ui/button";
import {
  PORTAL_CLUSTER, type ScopedItem, type ServingScope, clusterKey, isScoped, namespacesIn, scopeQuery,
} from "@/lib/serving-scope";

/** The scope in the URL and a setter that rewrites it in place (no history entry, no scroll). */
export function useServingScope(): [ServingScope, (next: ServingScope) => void] {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const scope = useMemo(() => ({ cluster: params.get("cluster") ?? "", ns: params.get("ns") ?? "" }), [params]);
  const setScope = useCallback(
    (next: ServingScope) => router.replace(`${pathname}${scopeQuery(next)}`, { scroll: false }),
    [router, pathname],
  );
  return [scope, setScope];
}

/** cluster_id (null = the portal's own cluster) → display name. */
export function useClusterName(): (clusterId: string | null | undefined) => string {
  const t = useTranslations("serving");
  const { data: clusters } = useK8sClusters();
  return useCallback(
    (clusterId) => {
      if (!clusterId) return t("scopePortalCluster");
      return clusters?.find((c) => c.id === clusterId)?.name ?? clusterId.slice(0, 8);
    },
    [clusters, t],
  );
}

/** Cluster and namespace selects; namespace options are the ones present in `items` for the chosen cluster. */
export function ServingScopeFilter({
  scope, onChange, items,
}: { scope: ServingScope; onChange: (next: ServingScope) => void; items: ScopedItem[] }) {
  const t = useTranslations("serving");
  const { data: clusters } = useK8sClusters();
  const namespaces = namespacesIn(items, scope.cluster);
  if (scope.ns && !namespaces.includes(scope.ns)) namespaces.unshift(scope.ns);
  const select = "h-8 rounded-md border border-input bg-transparent px-2 text-sm";

  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="serving-scope">
      <select
        id="scope-cluster" aria-label={t("scopeCluster")} className={select} value={scope.cluster}
        onChange={(e) => onChange({ cluster: e.target.value, ns: "" })}
      >
        <option value="">{t("scopeAllClusters")}</option>
        <option value={PORTAL_CLUSTER}>{t("scopePortalCluster")}</option>
        {(clusters ?? []).map((c) => <option key={c.id} value={clusterKey(c.id)}>{c.name}</option>)}
      </select>
      <select
        id="scope-namespace" aria-label={t("scopeNamespace")} className={`${select} font-mono`} value={scope.ns}
        onChange={(e) => onChange({ ...scope, ns: e.target.value })}
      >
        <option value="">{t("scopeAllNamespaces")}</option>
        {namespaces.map((ns) => <option key={ns} value={ns}>{ns}</option>)}
      </select>
      {isScoped(scope) && (
        <Button type="button" size="sm" variant="ghost" className="h-8" onClick={() => onChange({ cluster: "", ns: "" })}>
          <X className="size-3.5" />{t("scopeReset")}
        </Button>
      )}
    </div>
  );
}
