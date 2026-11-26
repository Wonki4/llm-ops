"use client";

import { Suspense, useState } from "react";
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation";
import { RefreshCw } from "lucide-react";
import { useTranslations } from "next-intl";
import { useQueryClient } from "@tanstack/react-query";

import { DeploymentScopeFields } from "@/components/deployment-scope-fields";
import { PageTabs, usePageTab } from "@/components/page-tabs";
import { RecipeKvCapacity, useRecipeCapacityScenario } from "@/components/recipe-kv-capacity";
import { RecipeThroughput } from "@/components/recipe-throughput";
import { RecipePageHeader, ServingRecipeForm } from "@/components/serving-recipe-form";
import { useClusterName } from "@/components/serving-scope-filter";
import { Button } from "@/components/ui/button";
import { useK8sClusterSettings, useSavedRecipeCapacity, useServingRecipe } from "@/hooks/use-api";
import { PORTAL_CLUSTER } from "@/lib/serving-scope";
import type { ServingRecipe } from "@/types";

export default function RecipePage() {
  return <Suspense fallback={<Loading />}><RecipeDetail /></Suspense>;
}

function Loading() {
  return <div aria-busy="true" className="h-32 animate-pulse rounded-lg bg-muted" />;
}

function RecipeDetail() {
  const t = useTranslations("savedRecipeCapacity");
  const { id } = useParams<{ id: string }>();
  const params = useSearchParams();
  const pathname = usePathname();
  const [tab] = usePageTab(["capacity", "edit"], "capacity");
  const query = useServingRecipe(id);
  if (query.isPending) return <Loading />;
  if (query.isError) return <p role="alert" className="text-destructive">{query.error.message}</p>;
  const recipe = query.data;
  const tabs = ["capacity", "edit"].map(value => {
    const next = new URLSearchParams(params.toString());
    next.set("tab", value);
    return { id: value, label: t(value === "capacity" ? "capacityAndThroughput" : value), href: `${pathname}?${next}` };
  });
  return <div className="space-y-5">
    <RecipePageHeader title={recipe.name} description={recipe.description ?? undefined} />
    <PageTabs label={recipe.name} tabs={tabs} active={tab} />
    {tab === "edit"
      ? <ServingRecipeForm key={id} recipe={recipe} cancelHref={tabs[0].href} />
      : <SavedCapacity key={id} recipe={recipe} />}
  </div>;
}

function SavedCapacity({ recipe }: { recipe: ServingRecipe }) {
  const t = useTranslations("savedRecipeCapacity");
  const ts = useTranslations("storages");
  const queryClient = useQueryClient();
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const settings = useK8sClusterSettings();
  const clusterName = useClusterName();
  const selected = params.get("cluster") || settings.data?.clusters.find(item => item.is_default)?.id || PORTAL_CLUSTER;
  const cluster = selected === PORTAL_CLUSTER ? "" : selected;
  const namespace = params.get("ns")?.trim() || "";
  const { scenario, changeScenario, syncRecipeLimit } = useRecipeCapacityScenario();
  const result = useSavedRecipeCapacity(recipe, {
    cluster_id: cluster || null,
    namespace: namespace || null,
    capacity_scenario: scenario,
  }, settings.isSuccess);
  syncRecipeLimit(result.data);

  function applyTarget(clusterId: string, nextNamespace: string) {
    if (clusterId === cluster && nextNamespace === namespace) {
      void result.refetch();
      void queryClient.invalidateQueries({ queryKey: ["saved-recipe-throughput", recipe.id] });
      return;
    }
    const url = new URLSearchParams(params.toString());
    url.set("cluster", clusterId || PORTAL_CLUSTER);
    if (nextNamespace) url.set("ns", nextNamespace);
    else url.delete("ns");
    router.push(`${pathname}?${url}`, { scroll: false });
  }
  if (settings.isPending) return <Loading />;
  if (settings.isError) return <p role="alert" className="text-destructive">{settings.error.message}</p>;
  const data = result.data;
  return <div className="max-w-5xl space-y-4">
    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm">
      <span className="break-all font-medium">{recipe.hf_repo_id || recipe.model_path}</span>
      <span className="text-muted-foreground">{recipe.engine === "vllm" ? "vLLM" : "SGLang"}{recipe.serving_mode === "pd" ? " · P/D" : ""}</span>
    </div>
    <TargetForm key={JSON.stringify([cluster, namespace])} cluster={cluster} namespace={namespace}
      busy={result.isFetching} onApply={applyTarget} />
    {result.isError ? <p role="alert" className="text-sm text-destructive">{result.error.message}</p>
      : result.isPending ? <Loading /> : <>
        {data && <p className="text-sm text-muted-foreground">{t("target", { cluster: clusterName(data.cluster_id), namespace: data.namespace })}</p>}
        {!!data?.warnings?.length && <ul className="space-y-1 text-sm text-amber-700 dark:text-amber-400">
          {data.warnings.map(warning => {
            const [code, ...detail] = warning.split(":");
            return <li key={warning}>{ts.has(`warn_${code}`) ? ts(`warn_${code}`, { claim: detail.join(":") }) : warning}</li>;
          })}
        </ul>}
        <RecipeKvCapacity data={data} scenario={scenario} onChange={changeScenario} />
      </>}
    <RecipeThroughput recipe={recipe} clusterId={cluster || null} namespace={namespace || null} />
  </div>;
}

function TargetForm({ cluster, namespace, busy, onApply }: {
  cluster: string; namespace: string; busy: boolean; onApply: (clusterId: string, namespace: string) => void;
}) {
  const t = useTranslations("savedRecipeCapacity");
  const [clusterId, setClusterId] = useState(cluster);
  const [ns, setNs] = useState(namespace);
  const changed = clusterId !== cluster || ns.trim() !== namespace;
  return <form className="rounded-lg border bg-card p-4" onSubmit={event => {
    event.preventDefault();
    onApply(clusterId, ns.trim());
  }}>
    <div className="flex flex-wrap items-start gap-3">
      <div className="min-w-0 grow"><DeploymentScopeFields clusterId={clusterId} namespace={ns}
        onClusterChange={setClusterId} onNamespaceChange={setNs} /></div>
      <Button className="sm:mt-6" type="submit" variant="outline" size="sm" disabled={busy && !changed}>
        <RefreshCw className={busy && !changed ? "animate-spin" : ""} />{t(changed ? "applyTarget" : "recalculate")}
      </Button>
    </div>
  </form>;
}
