"use client";

import { useMemo, useState } from "react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import { useCreateDeployment, useGpuNodes, useGpuProfiles, useK8sClusters } from "@/hooks/use-api";
import type { CreateDeploymentBody, PdConfig, ServingRecipe } from "@/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { mapToLines, resolvePlacementPreview, tolerationsToLines } from "@/lib/placement";

export function DeployFromRecipeDialog({
  recipe,
  onClose,
}: {
  recipe: ServingRecipe | null;
  onClose: () => void;
}) {
  const t = useTranslations("servingRecipes");
  const tc = useTranslations("common");
  const createDep = useCreateDeployment();
  const { data: clusters } = useK8sClusters();

  const isPd = recipe?.serving_mode === "pd";
  const [modelName, setModelName] = useState("");
  const [namespace, setNamespace] = useState("default");
  const [clusterId, setClusterId] = useState<string>("");
  const [ingressHost, setIngressHost] = useState("");
  const [ingressPath, setIngressPath] = useState("/");
  const [ingressClass, setIngressClass] = useState("nginx");
  const [replicas, setReplicas] = useState(1);
  // P/D: one replica count per pool, seeded from the recipe's role overrides.
  const [prefillReplicas, setPrefillReplicas] = useState(recipe?.pd_config?.prefill?.replicas ?? 1);
  const [decodeReplicas, setDecodeReplicas] = useState(recipe?.pd_config?.decode?.replicas ?? 1);
  const [gpuTypeChoice, setGpuTypeChoice] = useState<string | null>(null); // null = follow the recipe
  const gpuType = gpuTypeChoice ?? recipe?.gpu_type ?? "";
  const setGpuType = (v: string) => setGpuTypeChoice(v);

  // Instance fields reset each time a new recipe opens the dialog: the
  // caller remounts this component with `key={recipe.id}`.

  const open = !!recipe;
  const clusterKey = clusterId || null;
  const { data: profilesData } = useGpuProfiles(clusterKey, open);
  const nodes = useGpuNodes(clusterKey, open);
  const profiles = useMemo(() => (profilesData?.profiles ?? []).filter((p) => p.enabled), [profilesData]);
  const needsGpu = isPd
    ? Math.max(recipe?.gpu_count ?? 0, recipe?.pd_config?.prefill?.gpu_count ?? 0, recipe?.pd_config?.decode?.gpu_count ?? 0) > 0
    : (recipe?.gpu_count ?? 0) > 0;
  const hasProfiles = profiles.length > 0;
  const selected = profiles.find((p) => p.name === gpuType) ?? null;
  const gpuTypeMissing = needsGpu && hasProfiles && !selected;

  // Free/total per profile from the live scan, keyed by label value.
  const availability = useMemo(() => {
    const out: Record<string, { available: number; total: number }> = {};
    for (const g of nodes.data?.groups ?? []) {
      if (!g.label_value) continue;
      for (const p of profiles) {
        if (p.label_value !== g.label_value) continue;
        out[p.name] = { available: g.available[p.gpu_resource_key] ?? 0, total: g.allocatable[p.gpu_resource_key] ?? 0 };
      }
    }
    return out;
  }, [nodes.data, profiles]);

  const preview = useMemo(() => {
    if (!recipe) return null;
    return resolvePlacementPreview(selected, {
      node_selector: recipe.node_selector, tolerations: recipe.tolerations, gpu_resource_key: recipe.gpu_resource_key,
    });
  }, [recipe, selected]);

  function submit() {
    if (!recipe) return;
    if (!modelName.trim() || !ingressHost.trim()) {
      toast.error(t("deployRequired"));
      return;
    }
    if (gpuTypeMissing) {
      toast.error(t("deployGpuTypeRequired"));
      return;
    }
    const pdConfig: PdConfig | null = isPd
      ? {
          ...(recipe.pd_config ?? {}),
          prefill: { ...(recipe.pd_config?.prefill ?? {}), replicas: prefillReplicas },
          decode: { ...(recipe.pd_config?.decode ?? {}), replicas: decodeReplicas },
        }
      : null;
    const body: CreateDeploymentBody = {
      model_name: modelName.trim(),
      cluster_id: clusterId || null,
      recipe_id: recipe.id,
      namespace: namespace.trim() || "default",
      image: recipe.image,
      replicas: isPd ? decodeReplicas : replicas,
      gpu_count: recipe.gpu_count,
      gpu_resource_key: recipe.gpu_resource_key,
      cpu_request: recipe.cpu_request,
      cpu_limit: recipe.cpu_limit,
      memory_request: recipe.memory_request,
      memory_limit: recipe.memory_limit,
      node_selector: recipe.node_selector,
      tolerations: recipe.tolerations,
      pvc_name: recipe.pvc_name,
      pvc_mount_path: recipe.pvc_mount_path,
      model_path: recipe.model_path,
      vllm_extra_args: recipe.vllm_extra_args,
      env: recipe.env,
      engine: recipe.engine,
      engine_args: recipe.engine_args,
      probes: recipe.probes ?? null,
      gpu_type: hasProfiles ? (gpuType || null) : (recipe.gpu_type ?? null),
      ingress_host: ingressHost.trim(),
      ingress_path: isPd ? "/" : ingressPath.trim() || "/",
      ingress_class: ingressClass.trim() || "nginx",
      serving_mode: recipe.serving_mode ?? "aggregated",
      pd_config: pdConfig,
      runtime: recipe.runtime ?? null,
    };
    createDep.mutate(body, {
      onSuccess: () => { toast.success(t("deploySuccess")); onClose(); },
      onError: (e) => toast.error(e instanceof Error ? e.message : t("deployError")),
    });
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{recipe ? t("deployTitle", { name: recipe.name }) : ""}</DialogTitle>
          <DialogDescription>{recipe?.model_path}</DialogDescription>
        </DialogHeader>
        {isPd && <p className="rounded-md border bg-muted/30 px-3 py-2 text-xs text-muted-foreground" data-testid="deploy-pd-note">{t("deployPdNote")}</p>}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label={t("deployModelName")} span2><Input value={modelName} onChange={(e) => setModelName(e.target.value)} /></Field>
          <Field label={t("deployNamespace")}><Input value={namespace} onChange={(e) => setNamespace(e.target.value)} /></Field>
          <Field label={t("deployCluster")}>
            <select
              id="deploy-cluster"
              className="w-full h-9 rounded-md border border-input bg-transparent px-3 text-sm"
              value={clusterId}
              onChange={(e) => { setClusterId(e.target.value); setGpuTypeChoice(null); }}
            >
              <option value="">{t("deployClusterDefault")}</option>
              {(clusters ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </Field>
          <Field label={t("deployGpuType")} span2>
            {hasProfiles ? (
              <>
                <select
                  id="deploy-gpu-type"
                  className="w-full h-9 rounded-md border border-input bg-transparent px-3 text-sm"
                  value={selected ? gpuType : ""}
                  onChange={(e) => setGpuType(e.target.value)}
                >
                  <option value="">{t("deployGpuTypeNone")}</option>
                  {profiles.map((p) => {
                    const a = availability[p.name];
                    const label = a ? `${p.name} · ${t("deployAvailable", { available: a.available, total: a.total })}` : p.name;
                    return <option key={p.id} value={p.name}>{label}</option>;
                  })}
                </select>
                {gpuTypeMissing && <p className="text-xs text-destructive">{t("deployGpuTypeRequired")}</p>}
              </>
            ) : (
              <p className="text-xs text-muted-foreground">{t("deployGpuTypeNoProfiles")}</p>
            )}
          </Field>
          {isPd ? (
            <>
              <Field label={t("deployRouterHost")} span2>
                <Input id="deploy-router-host" value={ingressHost} onChange={(e) => setIngressHost(e.target.value)} />
                <p className="text-xs text-muted-foreground">{t("deployRouterHostHint", { name: modelName.trim() || "<model>" })}</p>
              </Field>
              <Field label={t("deployIngressClass")} span2><Input value={ingressClass} onChange={(e) => setIngressClass(e.target.value)} /></Field>
              <Field label={t("deployPrefillReplicas")}>
                <Input id="deploy-prefill-replicas" type="number" min={0} value={prefillReplicas} onChange={(e) => setPrefillReplicas(Number(e.target.value))} />
              </Field>
              <Field label={t("deployDecodeReplicas")}>
                <Input id="deploy-decode-replicas" type="number" min={0} value={decodeReplicas} onChange={(e) => setDecodeReplicas(Number(e.target.value))} />
              </Field>
            </>
          ) : (
            <>
              <Field label={t("deployIngressHost")} span2><Input id="deploy-ingress-host" value={ingressHost} onChange={(e) => setIngressHost(e.target.value)} /></Field>
              <Field label={t("deployIngressPath")}><Input id="deploy-ingress-path" value={ingressPath} onChange={(e) => setIngressPath(e.target.value)} /></Field>
              <Field label={t("deployIngressClass")}><Input value={ingressClass} onChange={(e) => setIngressClass(e.target.value)} /></Field>
              <Field label={t("deployReplicas")}><Input id="deploy-replicas" type="number" min={0} value={replicas} onChange={(e) => setReplicas(Number(e.target.value))} /></Field>
            </>
          )}
          {preview && (preview.node_selector || preview.tolerations || selected) && (
            <div className="sm:col-span-2 rounded-md border bg-muted/30 p-3 text-xs">
              <div className="mb-1 font-medium">{t("resolvedPlacement")}</div>
              <div className="grid grid-cols-1 gap-1 font-mono sm:grid-cols-3">
                <div><span className="text-muted-foreground">nodeSelector</span><br />{mapToLines(preview.node_selector) || "—"}</div>
                <div><span className="text-muted-foreground">tolerations</span><br />{tolerationsToLines(preview.tolerations) || "—"}</div>
                <div><span className="text-muted-foreground">resource</span><br />{recipe?.gpu_count} × {preview.gpu_resource_key}</div>
              </div>
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={createDep.isPending}>{tc("cancel")}</Button>
          <Button onClick={submit} disabled={createDep.isPending || gpuTypeMissing}>{t("deploySubmit")}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Field({ label, span2, children }: { label: string; span2?: boolean; children: React.ReactNode }) {
  return (
    <div className={span2 ? "space-y-1 sm:col-span-2" : "space-y-1"}>
      <Label>{label}</Label>
      {children}
    </div>
  );
}
