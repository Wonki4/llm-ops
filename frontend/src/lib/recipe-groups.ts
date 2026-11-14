type ModelRecipe = { name: string; hf_repo_id?: string | null };

const modelNameCollator = new Intl.Collator(undefined, { numeric: true });

/** Group by the full repository ID so equally named models from different owners stay distinct. */
export function groupRecipes<T extends ModelRecipe>(recipes: T[]) {
  const owners = new Map<string, Map<string, T[]>>();
  for (const recipe of recipes) {
    const [owner, model] = (recipe.hf_repo_id ?? "").split("/");
    const ownerKey = model ? owner : "";
    const models = owners.get(ownerKey) ?? new Map<string, T[]>();
    owners.set(ownerKey, models);
    const modelKey = model || "";
    const variants = models.get(modelKey) ?? [];
    variants.push(recipe);
    models.set(modelKey, variants);
  }
  return Array.from(owners, ([owner, models]) => ({
    owner,
    models: Array.from(models, ([model, variants]) => ({
      model,
      repo: owner ? `${owner}/${model}` : "",
      recipes: variants.sort((a, b) => a.name.localeCompare(b.name)),
    })).sort((a, b) => modelNameCollator.compare(b.model, a.model)),
  })).sort((a, b) => Number(!a.owner) - Number(!b.owner) || a.owner.localeCompare(b.owner));
}
