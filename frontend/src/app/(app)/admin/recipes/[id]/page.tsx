"use client";

import { useParams } from "next/navigation";
import { useTranslations } from "next-intl";

import { useServingRecipes } from "@/hooks/use-api";
import { RecipePageHeader, ServingRecipeForm } from "@/components/serving-recipe-form";

export default function EditRecipePage() {
  const t = useTranslations("servingRecipes");
  const { id } = useParams<{ id: string }>();
  // The list is small and already cached by the recipes page; no need for a
  // dedicated single-recipe endpoint.
  const { data: recipes, isLoading } = useServingRecipes();
  const recipe = recipes?.find((r) => r.id === id);

  return (
    <div className="space-y-6 max-w-4xl">
      <RecipePageHeader title={t("editTitle")} description={recipe?.name} />
      {isLoading ? (
        <div className="h-64 animate-pulse rounded-lg bg-muted" />
      ) : !recipe ? (
        <div className="rounded-lg border border-dashed p-8 text-center text-muted-foreground">{t("notFound")}</div>
      ) : (
        <ServingRecipeForm key={recipe.id} recipe={recipe} />
      )}
    </div>
  );
}
