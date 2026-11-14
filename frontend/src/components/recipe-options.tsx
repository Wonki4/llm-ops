import { useTranslations } from "next-intl";
import { groupRecipes } from "@/lib/recipe-groups";

export function RecipeOptions<T extends { id: string; name: string; hf_repo_id?: string | null }>({
  recipes, label,
}: { recipes: T[]; label?: (recipe: T) => string }) {
  const t = useTranslations("servingRecipes");
  return groupRecipes(recipes).flatMap(({ models }) => models.map(({ repo, recipes: variants }) => (
    <optgroup key={repo} label={repo || t("unassignedModel")}>
      {variants.map(r => <option key={r.id} value={r.id}>{label ? label(r) : r.name}</option>)}
    </optgroup>
  )));
}
