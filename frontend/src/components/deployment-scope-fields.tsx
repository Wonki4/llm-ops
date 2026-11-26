"use client";
import { useTranslations } from "next-intl";
import { useDefaultNamespace, useK8sClusters } from "@/hooks/use-api";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { FormSelect, FormSelectItem } from "@/components/ui/form-select";

/** Instance scope shared by deployment and validation forms. Changing cluster clears its namespace. */
export function DeploymentScopeFields({ clusterId, namespace, onClusterChange, onNamespaceChange }: {
  clusterId: string; namespace: string; onClusterChange: (id: string) => void; onNamespaceChange: (ns: string) => void;
}) {
  const t = useTranslations("benchmarkForm"), { data: clusters } = useK8sClusters();
  const defaultNs = useDefaultNamespace(clusterId);
  return <div className="grid gap-4 sm:grid-cols-2">
    <div className="space-y-1.5"><Label htmlFor="deploy-cluster">{t("clusterLabel")}</Label>
      <FormSelect placeholder={t("clusterDefault")} id="deploy-cluster" className="w-full" value={clusterId}
        onValueChange={selectedValue => { onClusterChange(selectedValue); onNamespaceChange(""); }}>
        <FormSelectItem value="">{t("clusterDefault")}</FormSelectItem>{clusters?.map(c => <FormSelectItem key={c.id} value={c.id}>{c.name}</FormSelectItem>)}
      </FormSelect></div>
    <div className="space-y-1.5"><Label htmlFor="deploy-namespace">{t("namespaceLabel")}</Label>
      <Input id="deploy-namespace" value={namespace} placeholder={defaultNs} onChange={e => onNamespaceChange(e.target.value)} />
      <p className="text-xs text-muted-foreground">{namespace || defaultNs}</p></div>
  </div>;
}
