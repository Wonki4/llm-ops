"use client";

import { Suspense, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Plus } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import { useServingRecipes, useDeleteServingRecipe } from "@/hooks/use-api";
import type { ServingRecipe } from "@/types";
import { groupRecipes } from "@/lib/recipe-groups";
import { RecipeModelNavigation } from "@/components/recipe-model-navigation";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { DeployFromRecipeDialog } from "@/components/deploy-from-recipe-dialog";
import { RecipeRepositoryIcon } from "@/components/recipe-repository-icon";

export default function AdminRecipesPage() {
  return <Suspense fallback={<div className="h-32 animate-pulse rounded-lg bg-muted" />}><RecipeLibrary /></Suspense>;
}

function RecipeLibrary() {
  const t = useTranslations("servingRecipes");
  const query = useServingRecipes();
  const owners = useMemo(() => groupRecipes(query.data ?? []), [query.data]);
  const requested = useSearchParams().get("repo");
  const selectedOwner = owners.find(owner => owner.models.some(m => m.repo === requested)) ?? owners[0];
  const selectedModel = selectedOwner?.models.find(m => m.repo === requested) ?? selectedOwner?.models[0];
  const deleteMut = useDeleteServingRecipe();
  const [deployTarget, setDeployTarget] = useState<ServingRecipe | null>(null);

  function selectModel(repo: string) {
    const url = new URL(window.location.href);
    url.searchParams.set("repo", repo);
    window.history.replaceState(null, "", url);
  }

  function remove(r: ServingRecipe) {
    if (!confirm(t("deleteConfirm", { name: r.name }))) return;
    deleteMut.mutate(r.id, { onError: (e) => toast.error(e instanceof Error ? e.message : t("saveError")) });
  }

  return <div className="space-y-6">
    <header className="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h1 className="text-2xl font-bold">{t("title")}</h1>
        <p className="mt-1 text-muted-foreground">{t("subtitle")}</p>
      </div>
      <Button asChild><Link href="/admin/recipes/new"><Plus />{t("newRecipe")}</Link></Button>
    </header>

    {query.isPending ? <div className="h-32 animate-pulse rounded-lg bg-muted" />
      : query.isError ? <p role="alert" className="text-destructive">{query.error.message}</p>
      : !selectedModel ? <p className="rounded-lg border border-dashed p-8 text-center text-muted-foreground">{t("empty")}</p>
      : <div className="grid items-start gap-4 lg:grid-cols-[minmax(13rem,17rem)_minmax(0,1fr)]">
        <RecipeModelNavigation owners={owners} selectedRepo={selectedModel.repo} onSelect={selectModel} />
        <section className="min-w-0 overflow-hidden rounded-lg border bg-card">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3">
            <div className="flex min-w-0 items-center gap-3">
              <div className="flex size-12 shrink-0 items-center justify-center rounded-xl border bg-background">
                <RecipeRepositoryIcon owner={selectedOwner.owner} size={32} />
              </div>
              <div className="min-w-0">
                {selectedModel.repo && <p className="break-all text-xs text-muted-foreground">{selectedOwner.owner}</p>}
                <h2 className="flex flex-wrap items-baseline gap-x-2 font-semibold">
                  <span className="break-all">{selectedModel.model || t("unassignedModel")}</span>
                  <span className="text-sm font-normal text-muted-foreground">{t("recipeCount", {count: selectedModel.recipes.length})}</span>
                </h2>
              </div>
            </div>
            {selectedModel.repo && <Button asChild size="sm" variant="outline">
              <Link href={`/admin/recipes/new?repo=${encodeURIComponent(selectedModel.repo)}`}><Plus />{t("addForModel")}</Link>
            </Button>}
          </div>
          <Table>
            <TableHeader><TableRow>
              <TableHead>{t("recipeName")}</TableHead><TableHead>{t("engine")}</TableHead>
              <TableHead>{t("colImage")}</TableHead><TableHead>{t("colGpu")}</TableHead><TableHead className="text-right">{t("colActions")}</TableHead>
            </TableRow></TableHeader>
            <TableBody>{selectedModel.recipes.map(r => <TableRow key={r.id}>
              <TableCell className="font-medium"><Link href={`/admin/recipes/${r.id}`} className="hover:underline">{r.name}</Link></TableCell>
              <TableCell><div className="flex items-center gap-1">
                <Badge variant="secondary" className="uppercase">{r.engine}</Badge>
                {r.serving_mode === "pd" && <Badge variant="outline">P/D</Badge>}
              </div></TableCell>
              <TableCell className="max-w-52 truncate font-mono text-xs" title={r.image}>{r.image}</TableCell>
              <TableCell>{r.gpu_count} × {r.gpu_resource_key}</TableCell>
              <TableCell className="text-right whitespace-nowrap space-x-2">
                <Button size="xs" onClick={() => setDeployTarget(r)}>{t("deploy")}</Button>
                <Button asChild size="xs" variant="outline"><Link href={`/admin/recipes/${r.id}`}>{t("edit")}</Link></Button>
                <Button size="xs" variant="destructive" onClick={() => remove(r)}>{t("delete")}</Button>
              </TableCell>
            </TableRow>)}</TableBody>
          </Table>
        </section>
      </div>}
    <DeployFromRecipeDialog recipe={deployTarget} onClose={() => setDeployTarget(null)} />
  </div>;
}
