import { ChevronDown, ChevronRight } from "lucide-react";
import { useTranslations } from "next-intl";

import { RecipeRepositoryIcon } from "@/components/recipe-repository-icon";
import { cn } from "@/lib/utils";

type OwnerModels = {
  owner: string;
  models: { repo: string; model: string; recipes: readonly unknown[] }[];
};

export function RecipeModelNavigation({ owners, selectedRepo, onSelect }: {
  owners: OwnerModels[];
  selectedRepo: string;
  onSelect: (repo: string) => void;
}) {
  const t = useTranslations("servingRecipes");
  return (
    <nav aria-label={t("modelLibrary")} className="overflow-hidden rounded-lg border bg-card">
      <h2 className="border-b px-4 py-3 text-sm font-semibold">{t("modelLibrary")}</h2>
      <div className="max-h-[33dvh] space-y-1 overflow-y-auto p-2 sm:max-h-[55dvh]">
        {owners.map(({owner, models}) => {
          const active = models.some(m => m.repo === selectedRepo);
          if (!owner) return <button key="" type="button" aria-pressed={active}
            className={cn("w-full rounded-md px-2 py-2 text-start text-sm hover:bg-muted", active && "bg-muted font-medium")}
            onClick={() => onSelect("")}>{t("unassignedModel")}</button>;
          const Arrow = active ? ChevronDown : ChevronRight;
          return <div key={owner}>
            <button type="button" aria-expanded={active} className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-sm font-medium hover:bg-muted"
              onClick={() => onSelect(models[0].repo)}>
              <Arrow className="size-3.5 text-muted-foreground" /><RecipeRepositoryIcon owner={owner} />{owner}
            </button>
            {active && <div className="ml-4 space-y-0.5 border-l pl-2">
              {models.map(m => <button key={m.repo} type="button" aria-pressed={m.repo === selectedRepo}
                className={cn("flex w-full items-center gap-2 rounded-md px-2 py-2 text-start text-sm hover:bg-muted",
                  m.repo === selectedRepo && "bg-muted font-medium")}
                onClick={() => onSelect(m.repo)}>
                <span className="min-w-0 flex-1 break-all">{m.model}</span>
                <span className="text-xs tabular-nums text-muted-foreground">{m.recipes.length}</span>
              </button>)}
            </div>}
          </div>;
        })}
      </div>
    </nav>
  );
}
