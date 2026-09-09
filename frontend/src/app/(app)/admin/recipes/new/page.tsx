"use client";

import { useTranslations } from "next-intl";

import { RecipePageHeader, ServingRecipeForm } from "@/components/serving-recipe-form";

export default function NewRecipePage() {
  const t = useTranslations("servingRecipes");
  return (
    <div className="space-y-6 max-w-4xl">
      <RecipePageHeader title={t("createTitle")} description={t("createDescription")} />
      <ServingRecipeForm />
    </div>
  );
}
