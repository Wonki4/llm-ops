"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import { useCreateDeployment, useDefaultNamespace, useGpuNodes, useGpuProfiles, useK8sClusters, useServingRecipes } from "@/hooks/use-api";
import type { CreateDeploymentBody, PdConfig, ServingRecipe } from "@/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger,
} from "@/components/ui/dialog";
import { RegistrationPreview, useRegistrationPreview } from "@/components/litellm-registration-preview";
import { StorageResolutionPreview, recipeStorageIds } from "@/components/storage-resolution-preview";
import { isValidNamespace } from "@/lib/k8s-names";
import { DeployRecipeBrowser, SelectedRecipe } from "@/components/deploy-recipe-browser";
import { PORTAL_CLUSTER, type ServingScope } from "@/lib/serving-scope";
import { mapToLines, resolvePlacementPreview, tolerationsToLines } from "@/lib/placement";

export function AdminDeployFromRecipeButton({ scope }: { scope: ServingScope }) {
  const t = useTranslations("teamServing");
  const [open, setOpen] = useState(false);
  const [recipe, setRecipe] = useState<ServingRecipe | null>(null);
  function changeOpen(next: boolean) {
    setOpen(next);
    if (!next) setRecipe(null);
  }
  return <Dialog open={open} onOpenChange={changeOpen}>
    <DialogTrigger asChild><Button>{t("deployFromRecipe")}</Button></DialogTrigger>
    {open && (recipe
      ? <RecipeDeploymentContent key={recipe.id} recipe={recipe} initialScope={scope} onSelectRecipe={setRecipe} onClose={() => changeOpen(false)} />
      : <RecipePicker onSelect={setRecipe} />)}
  </Dialog>;
}

function RecipePicker({ initialRepo, onSelect }: { initialRepo?: string; onSelect: (recipe: ServingRecipe) => void }) {
  const t = useTranslations();
  const recipes = useServingRecipes();
  return <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-3xl">
    <DialogHeader>
      <DialogTitle>{t("teamServing.deployFromRecipe")}</DialogTitle>
      <DialogDescription>{t("servingRecipes.pickForDeploy")}</DialogDescription>
    </DialogHeader>
    {recipes.isPending && <p role="status">{t("common.loading")}</p>}
    {recipes.isError && <p role="alert">{recipes.error.message}</p>}
    {recipes.data?.length === 0 && <div className="space-y-3">
      <p>{t("servingRecipes.empty")}</p>
      <Button asChild variant="outline"><Link href="/admin/recipes/new">{t("servingRecipes.newRecipe")}</Link></Button>
    </div>}
    {!!recipes.data?.length && <DeployRecipeBrowser recipes={recipes.data} initialRepo={initialRepo} onSelect={onSelect} />}
  </DialogContent>;
}

export function DeployFromRecipeDialog({
  recipe,
  onClose,
}: {
  recipe: ServingRecipe | null;
  onClose: () => void;
}) {
  return <Dialog open={!!recipe} onOpenChange={open => { if (!open) onClose(); }}>
    {recipe && <RecipeDeploymentContent key={recipe.id} recipe={recipe} onClose={onClose} />}
  </Dialog>;
}

function RecipeDeploymentContent({ recipe, initialScope, onSelectRecipe, onClose }: {
  recipe: ServingRecipe;
  initialScope?: ServingScope;
  onSelectRecipe?: (recipe: ServingRecipe) => void;
  onClose: () => void;
}) {
  const t = useTranslations("servingRecipes");
  const tr = useTranslations("adminDeployments");
  const tc = useTranslations("common");
  const ts = useTranslations("storages");
  const deployLabel = useTranslations("teamServing");
  const createDep = useCreateDeployment();
  const [choosingRecipe, setChoosingRecipe] = useState(false);
  const { data: clusters } = useK8sClusters();

  const isPd = recipe.serving_mode === "pd";
  const [modelName, setModelName] = useState("");
  const [apiBase, setApiBase] = useState("");
  const [litellmName, setLitellmName] = useState("");
  const [namespace, setNamespace] = useState(initialScope?.ns ?? "");
  const [clusterId, setClusterId] = useState(initialScope?.cluster === PORTAL_CLUSTER ? "" : initialScope?.cluster ?? "");
  const [ingressHost, setIngressHost] = useState("");
  const [ingressPath, setIngressPath] = useState("/");
  const [ingressClass, setIngressClass] = useState("nginx");
  const [replicas, setReplicas] = useState(1);
  // P/D: one replica count per pool, seeded from the recipe's role overrides.
  const [prefillReplicas, setPrefillReplicas] = useState(recipe.pd_config?.prefill?.replicas ?? 1);
  const [decodeReplicas, setDecodeReplicas] = useState(recipe.pd_config?.decode?.replicas ?? 1);
  const [storageBlocked, setStorageBlocked] = useState(false);
  const [gpuTypeChoice, setGpuTypeChoice] = useState<string | null>(null); // null = follow the recipe
  const gpuType = gpuTypeChoice ?? recipe.gpu_type ?? "";
  const setGpuType = (v: string) => setGpuTypeChoice(v);

  const clusterKey = clusterId || null;
  // Blank = the cluster's default namespace; the backend resolves it the same way.
  const defaultNs = useDefaultNamespace(clusterId);
  const nsTyped = namespace.trim();
  const nsInvalid = !!nsTyped && !isValidNamespace(nsTyped);
  const effectiveNs = nsTyped || defaultNs;
  const { data: profilesData } = useGpuProfiles(clusterKey);
  const nodes = useGpuNodes(clusterKey);
  const profiles = useMemo(() => (profilesData?.profiles ?? []).filter((p) => p.enabled), [profilesData]);
  const needsGpu = isPd
    ? Math.max(recipe.gpu_count ?? 0, recipe.pd_config?.prefill?.gpu_count ?? 0, recipe.pd_config?.decode?.gpu_count ?? 0) > 0
    : (recipe.gpu_count ?? 0) > 0;
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
    return resolvePlacementPreview(selected, {
      node_selector: recipe.node_selector, tolerations: recipe.tolerations, gpu_resource_key: recipe.gpu_resource_key,
    });
  }, [recipe, selected]);

  const pdConfig: PdConfig | null = isPd
    ? {
        ...(recipe.pd_config ?? {}),
        prefill: { ...(recipe.pd_config?.prefill ?? {}), replicas: prefillReplicas },
        decode: { ...(recipe.pd_config?.decode ?? {}), replicas: decodeReplicas },
      }
    : null;
  const body: CreateDeploymentBody = {
    model_name: modelName.trim(),
    litellm_api_base: apiBase.trim() || null,
    litellm_model_name: litellmName.trim() || null,
    cluster_id: clusterId || null,
    recipe_id: recipe.id,
    namespace: nsTyped || null,
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
    storages: recipe.storages ?? [],
    model_path: recipe.model_path,
    vllm_extra_args: recipe.vllm_extra_args,
    env: recipe.env,
    engine: recipe.engine,
    engine_args: recipe.engine_args,
    probes: recipe.probes ?? null,
    gpu_type: hasProfiles ? (gpuType || null) : (recipe.gpu_type ?? null),
    ingress_host: ingressHost.trim(),
    ingress_path: isPd ? "/" : ingressPath.trim() || "/",
    ingress_class: isPd ? (recipe.pd_config?.router?.ingress_class ?? "nginx") : ingressClass.trim() || "nginx",
    serving_mode: recipe.serving_mode ?? "aggregated",
    pd_config: pdConfig,
    aggregated_router: recipe.aggregated_router ?? null,
    runtime: recipe.runtime ?? null,
  };
  const registration = useRegistrationPreview(
    isPd ? "/api/model-deployments/registration-preview" : "/api/model-deployments/registration-preview?include_ingress=true",
    body.model_name && body.ingress_host && !nsInvalid ? body : null,
  );

  function submit() {
    if (!registration.data) return;
    if (!modelName.trim() || !ingressHost.trim()) {
      toast.error(t("deployRequired"));
      return;
    }
    if (nsInvalid) {
      toast.error(t("deployNamespaceInvalid"));
      return;
    }
    if (gpuTypeMissing) {
      toast.error(t("deployGpuTypeRequired"));
      return;
    }
    createDep.mutate(body, {
      onSuccess: (res) => {
        toast.success(t("deploySuccess"));
        // Warnings look like `code` or `code:detail`; unknown codes fall back to the raw text.
        (res.warnings ?? []).forEach((w) => {
          const i = w.indexOf(":");
          const code = i < 0 ? w : w.slice(0, i);
          const claim = i < 0 ? "" : w.slice(i + 1);
          toast.warning(ts.has(`warn_${code}`) ? ts(`warn_${code}`, { claim }) : w);
        });
        onClose();
      },
      onError: (e) => toast.error(e instanceof Error ? e.message : t("deployError")),
    });
  }

  if (choosingRecipe && onSelectRecipe) {
    return <RecipePicker initialRepo={recipe.hf_repo_id ?? ""} onSelect={next => {
      onSelectRecipe(next);
      setChoosingRecipe(false);
    }} />;
  }
  return (
      <DialogContent className="max-h-[90dvh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{onSelectRecipe ? deployLabel("deployFromRecipe") : t("deployTitle", { name: recipe.name })}</DialogTitle>
          <DialogDescription>{recipe.model_path}</DialogDescription>
        </DialogHeader>
        {onSelectRecipe && <SelectedRecipe recipe={recipe} onChange={() => setChoosingRecipe(true)} disabled={createDep.isPending} />}
        {!isPd && <p className="text-sm text-muted-foreground">{recipe.aggregated_router != null ? t("aggregatedRouterRoute") : t("aggregatedDirectRoute")}</p>}
        {isPd && <p className="rounded-md border bg-muted/30 px-3 py-2 text-xs text-muted-foreground" data-testid="deploy-pd-note">{t("deployPdNote")}</p>}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label={t("deployModelName")} span2><Input value={modelName} onChange={(e) => setModelName(e.target.value)} /></Field>
          <Field label={t("deployLitellmName")} span2>
            <Input
              id="deploy-litellm-name" className="font-mono" value={litellmName} placeholder={modelName.trim() || undefined}
              onChange={(e) => setLitellmName(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">{t("deployLitellmNameHint")}</p>
          </Field>
          <Field label={t("deployNamespace")}>
            <Input
              id="deploy-namespace" className="font-mono" value={namespace} placeholder={defaultNs} aria-invalid={nsInvalid}
              onChange={(e) => setNamespace(e.target.value)}
            />
            <p className={nsInvalid ? "text-xs text-destructive" : "text-xs text-muted-foreground"} data-testid="deploy-namespace-hint">
              {nsInvalid ? t("deployNamespaceInvalid") : t("deployNamespaceHint", { ns: defaultNs })}
            </p>
          </Field>
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
                <div><span className="text-muted-foreground">resource</span><br />{recipe.gpu_count} × {preview.gpu_resource_key}</div>
              </div>
            </div>
          )}
          <StorageResolutionPreview
            storageIds={recipeStorageIds(recipe)}
            clusterId={clusterId || null}
            namespace={effectiveNs}
            onBlockedChange={setStorageBlocked}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="deploy-api-base">{tr("overrideApiBase")}</Label>
          <Input id="deploy-api-base" value={apiBase} onChange={(e) => setApiBase(e.target.value)}
            placeholder={tr("defaultApiBase")} />
          <RegistrationPreview preview={registration} />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={createDep.isPending}>{tc("cancel")}</Button>
          <Button onClick={submit} disabled={createDep.isPending || gpuTypeMissing || storageBlocked || nsInvalid || !registration.data}>{t("deploySubmit")}</Button>
        </DialogFooter>
      </DialogContent>
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
