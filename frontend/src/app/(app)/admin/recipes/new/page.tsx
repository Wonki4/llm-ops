"use client";

import { Suspense, useMemo } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { AlertTriangle, Import, Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";

import { useExternalRecipeDraft, useModelDeployment } from "@/hooks/use-api";
import type { RecipeDraftWarning, ServingRecipeInput } from "@/types";
import { RecipePageHeader, ServingRecipeForm } from "@/components/serving-recipe-form";
import { normalizeDraft, recipeFromDeployment } from "@/lib/recipe-drafts";

export default function NewRecipePage() {
  // useSearchParams needs a Suspense boundary for static prerendering.
  return (
    <Suspense fallback={<div className="h-64 animate-pulse rounded-lg bg-muted" />}>
      <NewRecipeRoute />
    </Suspense>
  );
}

function NewRecipeRoute() {
  const t = useTranslations("servingRecipes");
  const sp = useSearchParams();
  const from = sp.get("from");

  if (from === "deployment" && sp.get("id")) return <FromPortalDeployment id={sp.get("id")!} />;
  if (from === "external" && sp.get("namespace") && sp.get("name")) {
    return (
      <FromExternalDeployment
        namespace={sp.get("namespace")!}
        name={sp.get("name")!}
        clusterId={sp.get("cluster")}
      />
    );
  }
  return (
    <div className="space-y-6 max-w-4xl">
      <RecipePageHeader title={t("createTitle")} description={t("createDescription")} />
      <ServingRecipeForm />
    </div>
  );
}

/** Portal deployment: structured columns, straight copy. No parsing involved. */
function FromPortalDeployment({ id }: { id: string }) {
  const t = useTranslations("servingRecipes");
  const { data: dep, isLoading } = useModelDeployment(id);

  const initial = useMemo<ServingRecipeInput | null>(() => {
    if (!dep) return null;
    return recipeFromDeployment(
      dep,
      t("importDefaultName", { model: dep.model_name }),
      t("importDefaultDescription", { namespace: dep.namespace, name: dep.model_name }),
    );
  }, [dep, t]);

  return (
    <div className="space-y-6 max-w-4xl">
      <RecipePageHeader title={t("importTitle")} description={t("importDescription")} />
      {isLoading ? (
        <Pulse />
      ) : !dep || !initial ? (
        <Empty text={t("importSourceNotFound")} />
      ) : (
        <ServingRecipeForm key={dep.id} initial={initial} sourceDeploymentId={dep.id}>
          <ImportNotice>
            {t.rich("importFromDeployment", {
              name: dep.model_name,
              link: (chunks) => (
                <Link href={`/admin/deployments/${dep.id}`} className="font-medium underline underline-offset-2">{chunks}</Link>
              ),
            })}
          </ImportNotice>
        </ServingRecipeForm>
      )}
    </div>
  );
}

/** External Deployment: reverse-parsed on the server, reviewed here before saving. */
function FromExternalDeployment({
  namespace, name, clusterId,
}: { namespace: string; name: string; clusterId: string | null }) {
  const t = useTranslations("servingRecipes");
  const { data, isLoading, error } = useExternalRecipeDraft({
    namespace, deployment_name: name, cluster_id: clusterId,
  });

  const prepared = useMemo(() => {
    if (!data) return null;
    const { input, relocated } = normalizeDraft({
      ...data.draft,
      name: data.draft.name || name,
      description: t("importDefaultDescription", { namespace, name }),
    });
    return { input, relocated };
  }, [data, name, namespace, t]);

  return (
    <div className="space-y-6 max-w-4xl">
      <RecipePageHeader title={t("importTitle")} description={t("importExternalDescription")} />
      {isLoading ? (
        <Pulse />
      ) : error || !data || !prepared ? (
        <Empty text={error instanceof Error ? error.message : t("importSourceNotFound")} />
      ) : (
        <ServingRecipeForm key={`${namespace}/${name}`} initial={prepared.input}>
          <ImportNotice>
            {t("importFromExternal", { namespace, name })}
            {data.source.command.length + data.source.args.length > 0 && (
              <code className="mt-2 block whitespace-pre-wrap break-all rounded bg-muted/60 p-2 text-[11px] leading-relaxed">
                {[...data.source.command, ...data.source.args].join(" ")}
              </code>
            )}
          </ImportNotice>
          <ParserWarnings warnings={data.warnings} relocated={prepared.relocated} />
        </ServingRecipeForm>
      )}
    </div>
  );
}

function ParserWarnings({ warnings, relocated }: { warnings: RecipeDraftWarning[]; relocated: string[] }) {
  const t = useTranslations("servingRecipes");
  const items: { key: string; text: string; detail?: string }[] = warnings.map((w, i) => ({
    key: `${w.code}-${i}`,
    text: t.has(`warn.${w.code}`) ? t(`warn.${w.code}`) : w.code,
    detail: w.detail,
  }));
  if (relocated.length) {
    items.push({ key: "relocated", text: t("warn.relocated_flags"), detail: relocated.join(" ") });
  }
  if (items.length === 0) {
    return (
      <div className="rounded-md border border-emerald-500/40 bg-emerald-500/10 px-4 py-3 text-sm">
        {t("importClean")}
      </div>
    );
  }
  return (
    <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-4 text-sm">
      <div className="flex items-center gap-2 font-medium">
        <AlertTriangle className="size-4 text-amber-500" />
        {t("importWarningsTitle", { count: items.length })}
      </div>
      <p className="mt-1 text-xs text-muted-foreground">{t("importWarningsHint")}</p>
      <ul className="mt-3 space-y-1.5">
        {items.map((it) => (
          <li key={it.key} className="flex flex-wrap items-baseline gap-x-2">
            <span>{it.text}</span>
            {it.detail && <code className="break-all text-[11px] text-muted-foreground">{it.detail}</code>}
          </li>
        ))}
      </ul>
    </div>
  );
}

function ImportNotice({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-2 rounded-md border bg-muted/30 px-4 py-3 text-sm">
      <Import className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

function Pulse() {
  return (
    <div className="flex items-center justify-center rounded-lg border border-dashed py-16 text-muted-foreground">
      <Loader2 className="size-5 animate-spin" />
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return <div className="rounded-lg border border-dashed p-8 text-center text-muted-foreground">{text}</div>;
}
