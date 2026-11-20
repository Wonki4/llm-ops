"use client";
import { Suspense, use, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { useRecipeValidation, useValidationAction } from "@/hooks/use-recipe-validations";
import { useServingRecipes } from "@/hooks/use-api";
import { ServingRecipeForm } from "@/components/serving-recipe-form";
import { DeployRecipeBrowser } from "@/components/deploy-recipe-browser";
import { ManifestDiffView } from "@/components/manifest-diff-view";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api";
import { recipePatch, VALIDATIONS, VALIDATION_API, type ValidationSnapshot } from "@/lib/recipe-validation";
import type { ManifestDiff, ServingRecipe, ServingRecipeInput } from "@/types";

export default function AddCandidatePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return <Suspense><Form id={id} /></Suspense>;
}
function merge(original: unknown, patch: unknown): unknown {
  if (!patch || typeof patch !== "object" || Array.isArray(patch)) return patch;
  const result = { ...(original && typeof original === "object" ? original : {}) } as Record<string, unknown>;
  for (const [key, value] of Object.entries(patch)) {
    if (value === null) delete result[key]; else result[key] = merge(result[key], value);
  }
  return result;
}
function Form({ id }: { id: string }) {
  const t = useTranslations("recipeValidation"), router = useRouter(), params = useSearchParams();
  const query = useRecipeValidation(id), recipes = useServingRecipes(), add = useValidationAction(id, "candidates");
  const [recipe, setRecipe] = useState<ServingRecipe | null>(null), [body, setBody] = useState<object | null>(null);
  const [hypothesis, setHypothesis] = useState("");
  const [requestKey] = useState(() => crypto.randomUUID());
  const preview = useMutation({ mutationFn: (value: object) => apiFetch<{ snapshot: ValidationSnapshot; diff: ManifestDiff }>(`${VALIDATION_API}/${id}/candidate-preview`, { method: "POST", body: JSON.stringify(value) }) });
  const candidate = query.data?.candidates.find(c => c.id === params.get("source"));
  const attempt = candidate?.attempts.find(a => a.id === params.get("attempt"));
  const original = candidate?.snapshot.recipe ?? recipe;
  const proposed = attempt?.analysis?.proposed_patch;
  if (query.error) return <p role="alert">{query.error.message}</p>;
  if (!query.data) return <p>{t("loading")}</p>;
  if (query.data.status !== "awaiting_input") return <p>{t("onlyAwaiting")} <Link className="underline" href={`${VALIDATIONS}/${id}`}>{t("backToJob")}</Link></p>;
  return <div className="space-y-5"><Link className="text-sm text-muted-foreground hover:underline" href={`${VALIDATIONS}/${id}`}>← {t("backToJob")}</Link>
    <h1 className="text-2xl font-bold">{t("addCorrection")}</h1><p className="text-muted-foreground">{t("correctionHint")}</p>
    {query.data.conditions.search && <label className="block space-y-2 text-sm">{t("searchHypothesis")}<Input value={hypothesis} onChange={e => setHypothesis(e.target.value)} required /></label>}
    {!original ? <DeployRecipeBrowser recipes={recipes.data ?? []} onSelect={setRecipe} /> : body && preview.data ? <>
      <ManifestDiffView diff={preview.data.diff} expandAll={false} leftLabel={t("original")} rightLabel={t("newCandidate")} />
      <div className="flex gap-3"><Button variant="outline" onClick={() => setBody(null)}>{t("previous")}</Button><Button disabled={add.isPending} onClick={() => add.mutate(body, { onSuccess: () => router.push(`${VALIDATIONS}/${id}`), onError: e => toast.error(e.message) })}>{t("addAndResume")}</Button></div>
    </> : <ServingRecipeForm key={candidate?.id ?? recipe?.id} initial={(proposed ? merge(original, proposed) : original) as ServingRecipeInput}
      submitLabel={t("reviewChanges")} cancelHref={`${VALIDATIONS}/${id}`} draftPending={preview.isPending}
      onDraftSubmit={async draft => {
        // Snapshot-only mutation; shared recipe edits remain on the separate recipe page.
        const baseline = { ...original } as Record<string, unknown>;
        for (const key of ["id", "created_by", "updated_by", "created_at", "updated_at", "warnings"]) delete baseline[key];
        if (query.data?.conditions.search && !hypothesis.trim()) { toast.error(t("searchHypothesisRequired")); return; }
        const value = { request_key: requestKey, expected_candidate_count: query.data?.candidates.length, hypothesis: hypothesis || null, ...(candidate ? { source_candidate_id: candidate.id } : { recipe_id: recipe!.id }), patch: recipePatch(baseline, draft) ?? {} };
        try { await preview.mutateAsync(value); setBody(value); } catch (e) { toast.error(e instanceof Error ? e.message : String(e)); }
      }} />}
    {preview.error && <p role="alert" className="text-destructive">{preview.error.message}</p>}
  </div>;
}
