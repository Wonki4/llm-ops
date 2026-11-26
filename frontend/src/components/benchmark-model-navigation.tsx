import { ChevronRight, Layers } from "lucide-react";
import { useTranslations } from "next-intl";

import { RecipeRepositoryIcon } from "@/components/recipe-repository-icon";
import { cn } from "@/lib/utils";
import type { BenchmarkModelGroup } from "@/types";

export function benchmarkModelTitle(name: string) {
  return name.startsWith("/") ? name.split("/").filter(Boolean).at(-1) || name : name;
}

type ModelItem = { group: BenchmarkModelGroup; title: string };
type NavigationItem = { owner: string; children: ModelItem[] } | ModelItem;

function navigationItems(groups: BenchmarkModelGroup[]) {
  const items: NavigationItem[] = [];
  const byOwner = new Map<string, ModelItem[]>();
  for (const group of groups) {
    // An API alias can include a tag (owner/model:tag). Path prefixes, drive
    // letters and extra path segments do not represent owners.
    const repo = /^([^./~\\:\s][^/\\:\s]*)\/([^/\\]+)$/u.exec(group.model_name);
    if (!repo || repo[2] === "." || repo[2] === "..") {
      items.push({ group, title: benchmarkModelTitle(group.model_name) });
      continue;
    }
    const [, owner, title] = repo;
    let children = byOwner.get(owner);
    if (!children) {
      children = [];
      byOwner.set(owner, children);
      items.push({ owner, children });
    }
    children.push({ group, title });
  }
  return items;
}

export function BenchmarkModelNavigation({ groups, selected, onSelect, searching }: {
  groups: BenchmarkModelGroup[];
  selected: string;
  onSelect: (name: string) => void;
  searching: boolean;
}) {
  const t = useTranslations("adminBenchmarks");

  function modelButton({ group, title }: ModelItem, nested = false) {
    const name = group.model_name;
    return <button key={`model:${name}`} type="button" title={name} aria-pressed={selected === name}
      onClick={() => onSelect(name)}
      className={cn("flex w-full items-start gap-2 rounded-md px-3 py-2 text-start text-sm hover:bg-muted",
        selected === name && "bg-muted font-medium")}>
      {!nested && <Layers className="mt-0.5 size-4 shrink-0 text-muted-foreground" />}
      <span className="min-w-0 flex-1 break-all">{title}</span>
      <span className="text-xs tabular-nums text-muted-foreground">{group.run_count}</span>
    </button>;
  }

  return <nav aria-label={t("modelLibrary")} className="overflow-hidden rounded-lg border bg-card">
    <div className="flex items-center justify-between border-b px-4 py-3">
      <h2 className="text-sm font-semibold">{t("modelLibrary")}</h2>
      <span className="text-xs tabular-nums text-muted-foreground">{groups.length}</span>
    </div>
    <div className="max-h-[33dvh] space-y-1 overflow-y-auto p-2 lg:max-h-[65dvh]">
      {navigationItems(groups).map(item => {
        if (!("owner" in item)) return modelButton(item);
        const active = item.children.some(({ group }) => group.model_name === selected);
        return <details key={`owner:${item.owner}`} open={active || searching} className="group">
          <summary className="flex cursor-pointer list-none items-center gap-2 rounded-md px-2 py-2 text-sm font-medium hover:bg-muted [&::-webkit-details-marker]:hidden">
            <ChevronRight className="size-3.5 shrink-0 text-muted-foreground group-open:rotate-90" />
            <RecipeRepositoryIcon owner={item.owner} size={18} />
            <span className="min-w-0 break-all">{item.owner}</span>
          </summary>
          <div className="ml-4 space-y-0.5 border-l pl-2">
            {item.children.map(child => modelButton(child, true))}
          </div>
        </details>;
      })}
    </div>
  </nav>;
}
