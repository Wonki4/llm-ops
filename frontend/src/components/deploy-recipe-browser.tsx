"use client";

import { useMemo, useState } from "react";
import { ChevronRight } from "lucide-react";
import { useTranslations } from "next-intl";

import { groupRecipes } from "@/lib/recipe-groups";
import { RecipeModelNavigation } from "@/components/recipe-model-navigation";
import { RecipeRepositoryIcon } from "@/components/recipe-repository-icon";
import { Badge } from "@/components/ui/badge";

type DeployableRecipe = {
  id: string; name: string; hf_repo_id?: string | null; engine: string; serving_mode: string;
};

export function DeployRecipeBrowser<T extends DeployableRecipe>({ recipes, initialRepo, onSelect }: {
  recipes: T[];
  initialRepo?: string | null;
  onSelect: (recipe: T) => void;
}) {
  const t = useTranslations("servingRecipes");
  const owners = useMemo(() => groupRecipes(recipes), [recipes]);
  const [repo, setRepo] = useState(initialRepo);
  const owner = owners.find(o => o.models.some(m => m.repo === repo)) ?? owners[0];
  const model = owner?.models.find(m => m.repo === repo) ?? owner?.models[0];
  if (!model) return null;
  return <div className="grid min-w-0 items-start gap-3 sm:grid-cols-[minmax(10rem,2fr)_minmax(0,3fr)]">
    <RecipeModelNavigation owners={owners} selectedRepo={model.repo} onSelect={setRepo} />
    <section className="min-w-0 rounded-lg border bg-card">
      <div className="flex items-center gap-3 border-b p-3">
        <RecipeRepositoryIcon owner={owner.owner} size={28} />
        <div className="min-w-0">
          {owner.owner && <p className="break-all text-xs text-muted-foreground">{owner.owner}</p>}
          <h3 className="break-all font-semibold">{model.model || t("unassignedModel")}</h3>
        </div>
        <span className="ml-auto shrink-0 text-xs text-muted-foreground">{t("recipeCount", {count: model.recipes.length})}</span>
      </div>
      <ul className="max-h-[45dvh] divide-y overflow-y-auto">
        {model.recipes.map(recipe => <li key={recipe.id}>
          <button type="button" className="flex w-full items-center gap-3 px-3 py-3 text-start hover:bg-muted focus-visible:bg-muted"
            onClick={() => onSelect(recipe)}>
            <span className="min-w-0 flex-1">
              <span className="block break-all text-sm font-medium">{recipe.name}</span>
              <span className="mt-1 flex flex-wrap items-center gap-1.5">
                <Badge variant="secondary" className="uppercase">{recipe.engine}</Badge>
                <span className="text-xs text-muted-foreground">{recipe.serving_mode === "pd" ? "P/D" : t("modeAggregated")}</span>
              </span>
            </span>
            <ChevronRight aria-hidden className="size-4 shrink-0 text-muted-foreground" />
          </button>
        </li>)}
      </ul>
    </section>
  </div>;
}

export function SelectedRecipe({ recipe, onChange, disabled }: {
  recipe: DeployableRecipe; onChange?: () => void; disabled?: boolean;
}) {
  const t = useTranslations("servingRecipes");
  return <div className="flex min-w-0 items-center gap-3 rounded-lg border bg-muted/30 px-3 py-2">
    <RecipeRepositoryIcon owner={recipe.hf_repo_id?.split("/")[0]} size={24} />
    <div className="min-w-0 flex-1">
      <p className="break-all text-xs text-muted-foreground">{recipe.hf_repo_id || t("unassignedModel")}</p>
      <p className="break-all text-sm font-medium">{recipe.name}</p>
    </div>
    {onChange && <button type="button" disabled={disabled} onClick={onChange}
      className="shrink-0 rounded px-2 py-1 text-xs underline underline-offset-2 hover:bg-muted disabled:opacity-50">
      {t("changeRecipe")}
    </button>}
  </div>;
}
