"use client";

import { useState } from "react";
import Link from "next/link";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import { useServingRecipes, useDeleteServingRecipe } from "@/hooks/use-api";
import type { ServingRecipe } from "@/types";
import { Button } from "@/components/ui/button";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { DeployFromRecipeDialog } from "@/components/deploy-from-recipe-dialog";

export default function AdminRecipesPage() {
  const t = useTranslations("servingRecipes");
  const { data: recipes, isLoading } = useServingRecipes();
  const deleteMut = useDeleteServingRecipe();
  const [deployTarget, setDeployTarget] = useState<ServingRecipe | null>(null);

  function remove(r: ServingRecipe) {
    if (!confirm(t("deleteConfirm", { name: r.name }))) return;
    deleteMut.mutate(r.id, { onError: (e) => toast.error(e instanceof Error ? e.message : t("saveError")) });
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">{t("title")}</h1>
          <p className="text-muted-foreground mt-1">{t("subtitle")}</p>
        </div>
        <Button asChild><Link href="/admin/recipes/new">{t("newRecipe")}</Link></Button>
      </div>

      {isLoading ? (
        <div className="h-32 animate-pulse rounded-lg bg-muted" />
      ) : !recipes || recipes.length === 0 ? (
        <div className="rounded-lg border border-dashed p-8 text-center text-muted-foreground">{t("empty")}</div>
      ) : (
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("colName")}</TableHead>
                <TableHead>{t("colModel")}</TableHead>
                <TableHead>{t("colImage")}</TableHead>
                <TableHead>{t("colGpu")}</TableHead>
                <TableHead className="text-right">{t("colActions")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {recipes.map((r) => (
                <TableRow key={r.id}>
                  <TableCell className="font-medium">{r.name}</TableCell>
                  <TableCell className="font-mono text-xs">{r.model_path}</TableCell>
                  <TableCell className="font-mono text-xs">{r.image}</TableCell>
                  <TableCell>{r.gpu_count} × {r.gpu_resource_key}</TableCell>
                  <TableCell className="text-right space-x-2 whitespace-nowrap">
                    <Button size="xs" onClick={() => setDeployTarget(r)}>{t("deploy")}</Button>
                    <Button asChild size="xs" variant="outline">
                      <Link href={`/admin/recipes/${r.id}`}>{t("edit")}</Link>
                    </Button>
                    <Button size="xs" variant="destructive" onClick={() => remove(r)}>{t("delete")}</Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <DeployFromRecipeDialog
        key={deployTarget?.id ?? "closed"}
        recipe={deployTarget}
        onClose={() => setDeployTarget(null)}
      />
    </div>
  );
}
