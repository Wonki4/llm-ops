"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation";
import { AlertTriangle, ArrowLeft, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import { useModelDeployment, useReapplyDeployment, useRecipeDiff, useServingRecipes } from "@/hooks/use-api";
import type { ManifestDiff } from "@/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ManifestDiffView } from "@/components/manifest-diff-view";

const diffKey = (m: ManifestDiff) => `${m.kind}/${m.name}`;

function Spinner() {
  return <div className="flex justify-center py-16"><Loader2 className="size-6 animate-spin text-muted-foreground" /></div>;
}

export default function ReapplyFromRecipePage() {
  // useSearchParams needs a Suspense boundary for static prerendering.
  return (
    <Suspense fallback={<Spinner />}>
      <ReapplyFromRecipe />
    </Suspense>
  );
}

/**
 * Re-apply a recipe to a deployment: each K8s manifest as the deployment renders now
 * (left) against what the re-apply would send (right), with the rollout notes and
 * the Apply button. `?recipe=<id>` picks another recipe of the same serving mode;
 * without it the deployment's own recipe is used.
 */
function ReapplyFromRecipe() {
  const t = useTranslations("adminDeployments");
  const tc = useTranslations("common");
  const id = String(useParams().id);
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams();
  const { data: dep, isLoading } = useModelDeployment(id);
  const { data: recipes } = useServingRecipes();
  const recipeId = search.get("recipe") ?? dep?.recipe_id ?? null;
  const diff = useRecipeDiff(id, recipeId, !!dep && !!recipeId);
  const reapply = useReapplyDeployment(id);
  const [tab, setTab] = useState<string | null>(null);
  const [expandAll, setExpandAll] = useState(false);

  if (isLoading) return <Spinner />;
  if (!dep) {
    return (
      <div className="space-y-4">
        <Link href="/admin/deployments" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="size-4" />{t("backToList")}
        </Link>
        <p className="text-sm text-muted-foreground">{t("notFound")}</p>
      </div>
    );
  }

  const detailHref = `/admin/deployments/${id}`;
  const candidates = (recipes ?? []).filter((r) => (r.serving_mode ?? "aggregated") === dep.serving_mode);
  const data = diff.data;
  const manifests = data?.manifests ?? [];
  const blocked = data?.rollout.blocked ?? null;
  const rollout = data?.rollout;
  const active = manifests.find((m) => diffKey(m) === tab) ?? manifests.find((m) => m.changed > 0) ?? manifests[0];
  const nothingChanges = manifests.length > 0 && manifests.every((m) => m.changed === 0);
  // P/D has two Deployments (and Services): name them when the kind alone is ambiguous,
  // by the part between the model name and the kind (`qwen-prefill-deployment` → `prefill`).
  const kindCount = (kind: string) => manifests.filter((m) => m.kind === kind).length;
  const shortName = (m: ManifestDiff) => {
    const prefix = `${dep.model_name}-`;
    const suffix = `-${m.kind.toLowerCase()}`;
    const head = m.name.startsWith(prefix) ? m.name.slice(prefix.length) : m.name;
    return (head.endsWith(suffix) ? head.slice(0, -suffix.length) : head) || m.name;
  };

  function pickRecipe(next: string) {
    setTab(null);
    router.replace(next ? `${pathname}?recipe=${encodeURIComponent(next)}` : pathname, { scroll: false });
  }

  function submit() {
    reapply.mutate(recipeId, {
      onSuccess: () => { toast.success(t("reapplySuccess")); router.push(detailHref); },
      onError: (e) => toast.error(e instanceof Error ? e.message : t("reapplyFailed")),
    });
  }

  return (
    <div className="space-y-4">
      <div className="space-y-3">
        <Link href={detailHref} className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="size-4" />{dep.model_name}
        </Link>
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="space-y-1">
            <h1 className="text-2xl font-bold">{t("reapplyTitle")}</h1>
            <p className="max-w-3xl text-sm text-muted-foreground">{t("reapplyDesc")}</p>
          </div>
          <div className="flex flex-wrap items-end gap-2">
            <div className="space-y-1">
              <Label htmlFor="reapply-recipe" className="text-xs text-muted-foreground">{t("reapplyRecipe")}</Label>
              {candidates.length === 0 ? (
                <p className="h-9 text-xs leading-9 text-muted-foreground">{t("reapplyNoRecipes")}</p>
              ) : (
                <select
                  id="reapply-recipe"
                  className="h-9 min-w-48 rounded-md border border-input bg-transparent px-3 text-sm"
                  value={recipeId ?? ""}
                  onChange={(e) => pickRecipe(e.target.value)}
                >
                  {!recipeId && <option value="">{t("reapplyPickRecipe")}</option>}
                  {candidates.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
                </select>
              )}
            </div>
            <Button asChild variant="outline" disabled={reapply.isPending}>
              <Link href={detailHref}>{tc("cancel")}</Link>
            </Button>
            <Button onClick={submit} disabled={!data || !!blocked || reapply.isPending} data-testid="reapply-submit">
              {reapply.isPending && <Loader2 className="size-3.5 animate-spin" />}{t("reapplySubmit")}
            </Button>
          </div>
        </div>
      </div>

      {rollout && (
        <p className="rounded-md bg-muted/40 px-3 py-2 text-xs text-muted-foreground" data-testid="reapply-rollout-note">
          {rollout.mode === "surge"
            ? t("reapplySurgeNote", { count: rollout.extra_gpus, key: rollout.gpu_resource_key })
            : t("reapplyReplaceNote")}
        </p>
      )}
      {data?.reregister && (
        <div className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-500" />
          <span className="text-muted-foreground">{t("reapplyReregisterNote")}</span>
        </div>
      )}
      {blocked && (
        <p className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive" data-testid="reapply-blocked">
          {blocked}
        </p>
      )}

      {recipeId && diff.isLoading && <Spinner />}
      {recipeId && diff.error && (
        <p className="text-sm text-destructive" data-testid="reapply-error">
          {diff.error instanceof Error ? diff.error.message : t("reapplyFailed")}
        </p>
      )}
      {nothingChanges && (
        <p className="rounded-md border bg-muted/30 px-3 py-2 text-xs text-muted-foreground" data-testid="reapply-no-changes">
          {t("reapplyNoChanges")}
        </p>
      )}

      {active && data && (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Tabs value={diffKey(active)} onValueChange={setTab}>
              <TabsList variant="line" className="h-auto flex-wrap justify-start">
                {manifests.map((m) => (
                  <TabsTrigger key={diffKey(m)} value={diffKey(m)} title={m.name} className="flex-none gap-1.5" data-testid="manifest-tab">
                    {m.kind}
                    {kindCount(m.kind) > 1 && <span className="font-mono text-[11px] text-muted-foreground">{shortName(m)}</span>}
                    {m.changed > 0 ? (
                      <Badge className="h-4 bg-amber-500/20 px-1.5 text-[10px] text-amber-700 dark:text-amber-300">{m.changed}</Badge>
                    ) : (
                      <Badge variant="secondary" className="h-4 px-1.5 text-[10px] font-normal text-muted-foreground">{t("reapplySame")}</Badge>
                    )}
                  </TabsTrigger>
                ))}
              </TabsList>
            </Tabs>
            <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <input type="checkbox" checked={expandAll} onChange={(e) => setExpandAll(e.target.checked)} />
              {t("reapplyExpandAll")}
            </label>
          </div>
          <ManifestDiffView
            key={`${recipeId}/${diffKey(active)}`}
            diff={active}
            expandAll={expandAll}
            leftLabel={t("reapplySideCurrent", { name: dep.model_name })}
            rightLabel={t("reapplySideProposed", { name: data.recipe.name })}
          />
        </div>
      )}
    </div>
  );
}
