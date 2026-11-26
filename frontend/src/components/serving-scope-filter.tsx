"use client";

import { useCallback, useMemo } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { X } from "lucide-react";
import { useTranslations } from "next-intl";

import { useK8sClusters } from "@/hooks/use-api";
import { Button } from "@/components/ui/button";
import {
  PORTAL_CLUSTER, type ClusterSettings, type ScopedItem, type ServingScope,
  clusterKey, clusterScope, namespacesIn, scopeFromParams,
} from "@/lib/serving-scope";
import { FormSelect, FormSelectItem } from "@/components/ui/form-select";

/** Preserve other view options and retain scope changes in browser history. */
export function useServingScope(settings: ClusterSettings | undefined): [ServingScope, (next: ServingScope) => void] {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const scope = useMemo(() => scopeFromParams(params, settings), [params, settings]);
  const includeEmpty = !!settings;
  const setScope = useCallback(
    (next: ServingScope) => {
      const query = new URLSearchParams(params.toString());
      for (const key of ["cluster", "ns"] as const) {
        if (next[key] || includeEmpty) query.set(key, next[key]);
        else query.delete(key);
      }
      router.push(`${pathname}${query.size ? `?${query}` : ""}`, { scroll: false });
    },
    [router, pathname, includeEmpty, params],
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
  scope, onChange, items, settings,
}: { scope: ServingScope; onChange: (next: ServingScope) => void; items: ScopedItem[]; settings?: ClusterSettings }) {
  const t = useTranslations("serving");
  const { data: clusters } = useK8sClusters();
  const namespaces = namespacesIn(items, scope.cluster);
  const reset = settings ? clusterScope(settings) : { cluster: "", ns: "" };
  if (scope.ns && !namespaces.includes(scope.ns)) namespaces.unshift(scope.ns);

  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="serving-scope">
      <FormSelect placeholder={t("scopeAllClusters")}
        id="scope-cluster" aria-label={t("scopeCluster")} size="sm" value={scope.cluster}
        onValueChange={(selectedValue) => onChange(settings ? clusterScope(settings, selectedValue) : { cluster: selectedValue, ns: "" })}
      >
        <FormSelectItem value="">{t("scopeAllClusters")}</FormSelectItem>
        <FormSelectItem value={PORTAL_CLUSTER}>{t("scopePortalCluster")}</FormSelectItem>
        {(clusters ?? []).map((c) => <FormSelectItem key={c.id} value={clusterKey(c.id)}>{c.name}</FormSelectItem>)}
      </FormSelect>
      <FormSelect placeholder={t("scopeAllNamespaces")}
        id="scope-namespace" aria-label={t("scopeNamespace")} size="sm" className="font-mono" value={scope.ns}
        onValueChange={(selectedValue) => onChange({ ...scope, ns: selectedValue })}
      >
        <FormSelectItem value="">{t("scopeAllNamespaces")}</FormSelectItem>
        {namespaces.map((ns) => <FormSelectItem key={ns} value={ns}>{ns}</FormSelectItem>)}
      </FormSelect>
      {(scope.cluster !== reset.cluster || scope.ns !== reset.ns) && (
        <Button type="button" size="sm" variant="ghost" className="h-8" onClick={() => onChange(reset)}>
          <X className="size-3.5" />{t("scopeReset")}
        </Button>
      )}
    </div>
  );
}
