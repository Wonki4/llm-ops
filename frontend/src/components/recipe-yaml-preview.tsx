"use client";

import { useState } from "react";
import { Copy, Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import { useDefaultNamespace, useK8sClusters, usePreviewServingRecipe } from "@/hooks/use-api";
import type { ServingRecipeInput, ServingRecipePreviewBody } from "@/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { copyText } from "@/lib/clipboard";
import { cn } from "@/lib/utils";

const INGRESS_PLACEHOLDER = "<ingress-host>";

type Target = { clusterId: string; namespace: string; modelName: string };

/**
 * The K8s manifests a deploy of `recipe` would apply, rendered by the backend's
 * deploy path for a chosen cluster/namespace. A null `recipe` shows `emptyText`
 * (the form is not complete enough to render). `fill` stretches the YAML to the
 * parent's height (side panel); otherwise it scrolls within a fixed height.
 */
export function RecipeYamlPanel({
  recipe, emptyText, fill, className,
}: { recipe: ServingRecipeInput | null; emptyText?: string; fill?: boolean; className?: string }) {
  const t = useTranslations("servingRecipes");
  const ts = useTranslations("storages");
  const { data: clusters } = useK8sClusters();

  const [clusterId, setClusterId] = useState("");
  const [namespace, setNamespace] = useState("");
  const [modelName, setModelName] = useState("");
  // What gets rendered; text fields apply on Enter / "Render again", the cluster on change.
  const [target, setTarget] = useState<Target>({ clusterId: "", namespace: "", modelName: "" });
  const [selected, setSelected] = useState(0);
  const defaultNs = useDefaultNamespace(clusterId);

  const body: ServingRecipePreviewBody | null = recipe
    ? {
        ...recipe,
        cluster_id: target.clusterId || null,
        namespace: target.namespace.trim() || null,
        model_name: target.modelName.trim() || null,
      }
    : null;
  const preview = usePreviewServingRecipe(body);
  const manifests = preview.data?.manifests ?? [];
  const shown = manifests[Math.min(selected, manifests.length - 1)];
  const isPd = recipe?.serving_mode === "pd";

  function apply(next: Partial<Target> = {}) {
    setTarget({ clusterId, namespace, modelName, ...next });
  }

  async function copy(text: string) {
    if (await copyText(text)) toast.success(t("previewCopied"));
    else toast.error(t("previewCopyFailed"));
  }

  function warningText(w: string): string {
    const i = w.indexOf(":");
    const code = i < 0 ? w : w.slice(0, i);
    const claim = i < 0 ? "" : w.slice(i + 1);
    return ts.has(`warn_${code}`) ? ts(`warn_${code}`, { claim }) : w;
  }

  const onEnter = (e: React.KeyboardEvent) => { if (e.key === "Enter") { e.preventDefault(); apply(); } };
  const small = "h-8 text-xs";

  return (
    <div className={cn("flex min-h-0 flex-col gap-3", className)}>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-[1fr_1fr_1fr_auto] sm:items-end">
        <div className="space-y-1">
          <Label htmlFor="preview-cluster" className="text-[11px] font-normal text-muted-foreground">{t("deployCluster")}</Label>
          <select
            id="preview-cluster"
            className={cn("w-full rounded-md border border-input bg-transparent px-2", small)}
            value={clusterId}
            onChange={(e) => { setClusterId(e.target.value); apply({ clusterId: e.target.value }); }}
          >
            <option value="">{t("deployClusterDefault")}</option>
            {(clusters ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </div>
        <div className="space-y-1">
          <Label htmlFor="preview-namespace" className="text-[11px] font-normal text-muted-foreground">{t("deployNamespace")}</Label>
          <Input
            id="preview-namespace" className={small} value={namespace} placeholder={defaultNs}
            onChange={(e) => setNamespace(e.target.value)} onKeyDown={onEnter}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="preview-model-name" className="text-[11px] font-normal text-muted-foreground">{t("previewModelName")}</Label>
          <Input
            id="preview-model-name" className={small} value={modelName} placeholder={recipe?.name.trim() || "preview"}
            onChange={(e) => setModelName(e.target.value)} onKeyDown={onEnter}
          />
        </div>
        <Button type="button" size="sm" variant="outline" onClick={() => apply()} disabled={!recipe || preview.isFetching} title={t("previewRender")}>
          {preview.isFetching ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}
          <span className="sm:sr-only xl:not-sr-only">{t("previewRender")}</span>
        </Button>
      </div>

      <p className="text-[11px] text-muted-foreground">
        {isPd ? <>{t("previewPdRouterNote")} {t("previewModelNameNote")}</> : t("previewIngressNote", { host: INGRESS_PLACEHOLDER })}
      </p>

      {!recipe ? (
        <div className="flex flex-1 items-center justify-center rounded-md border border-dashed p-6 text-center text-xs text-muted-foreground">
          {emptyText}
        </div>
      ) : preview.isError ? (
        <p className="text-xs text-destructive" data-testid="recipe-yaml-preview-error">
          {t("previewError")}: {preview.error instanceof Error ? preview.error.message : String(preview.error)}
        </p>
      ) : preview.isPending ? (
        <div className="h-64 animate-pulse rounded-md bg-muted" />
      ) : (
        <>
          {(preview.data?.warnings ?? []).length > 0 && (
            <ul className="space-y-1 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs" data-testid="recipe-yaml-preview-warnings">
              {preview.data!.warnings.map((w) => <li key={w}>{warningText(w)}</li>)}
            </ul>
          )}
          <div className="flex items-start justify-between gap-2">
            <div role="tablist" className="flex flex-wrap gap-1">
              {manifests.map((m, i) => (
                <button
                  key={`${m.kind}/${m.name}`}
                  type="button"
                  role="tab"
                  aria-selected={m === shown}
                  onClick={() => setSelected(i)}
                  className={
                    "rounded-md border px-2 py-0.5 font-mono text-[11px] transition-colors " +
                    (m === shown ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground")
                  }
                >
                  {m.kind}/{m.name}
                </button>
              ))}
            </div>
            <Button type="button" size="xs" variant="outline" className="shrink-0" onClick={() => copy(manifests.map((m) => m.yaml).join("---\n"))}>
              <Copy className="size-3.5" />{t("previewCopyAll")}
            </Button>
          </div>
          {shown && (
            <div role="tabpanel" className={cn("relative min-h-0 min-w-0", fill && "flex flex-1 flex-col")}>
              <Button type="button" size="xs" variant="ghost" className="absolute right-4 top-2" onClick={() => copy(shown.yaml)}>
                <Copy className="size-3.5" />{t("previewCopy")}
              </Button>
              <pre
                className={cn(
                  "overflow-auto rounded-md border bg-muted/40 p-3 font-mono text-[11px] leading-relaxed",
                  fill ? "min-h-0 flex-1" : "max-h-[55vh]",
                  preview.isFetching && "opacity-60",
                )}
              >
                {shown.yaml}
              </pre>
            </div>
          )}
        </>
      )}
    </div>
  );
}

/** The panel in a dialog, for screens too narrow for the side panel. `recipe` null = closed. */
export function RecipeYamlPreview({ recipe, onClose }: { recipe: ServingRecipeInput | null; onClose: () => void }) {
  const t = useTranslations("servingRecipes");
  return (
    <Dialog open={!!recipe} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="sm:max-w-4xl" data-testid="recipe-yaml-preview">
        <DialogHeader>
          <DialogTitle>{t("previewTitle")}</DialogTitle>
          <DialogDescription>{t("previewDescription")}</DialogDescription>
        </DialogHeader>
        <RecipeYamlPanel recipe={recipe} />
      </DialogContent>
    </Dialog>
  );
}
