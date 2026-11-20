"use client";
import { useTranslations } from "next-intl";
import { useDefaultNamespace, useK8sClusters } from "@/hooks/use-api";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/** Instance scope shared by deployment and validation forms. Changing cluster clears its namespace. */
export function DeploymentScopeFields({ clusterId, namespace, onClusterChange, onNamespaceChange }: {
  clusterId: string; namespace: string; onClusterChange: (id: string) => void; onNamespaceChange: (ns: string) => void;
}) {
  const t = useTranslations("benchmarkForm"), { data: clusters } = useK8sClusters();
  const defaultNs = useDefaultNamespace(clusterId);
  return <div className="grid gap-4 sm:grid-cols-2">
    <div className="space-y-1.5"><Label htmlFor="deploy-cluster">{t("clusterLabel")}</Label>
      <select id="deploy-cluster" className="h-9 w-full rounded-md border bg-background px-3 text-sm" value={clusterId}
        onChange={e => { onClusterChange(e.target.value); onNamespaceChange(""); }}>
        <option value="">{t("clusterDefault")}</option>{clusters?.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
      </select></div>
    <div className="space-y-1.5"><Label htmlFor="deploy-namespace">{t("namespaceLabel")}</Label>
      <Input id="deploy-namespace" value={namespace} placeholder={defaultNs} onChange={e => onNamespaceChange(e.target.value)} />
      <p className="text-xs text-muted-foreground">{namespace || defaultNs}</p></div>
  </div>;
}
