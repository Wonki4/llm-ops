"use client";

import { useState } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import { useReapplyDeployment, useRecipeDiff, useServingRecipes } from "@/hooks/use-api";
import type { ModelDeployment } from "@/types";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

/** Strings as-is, string lists space-joined (argv), everything else as JSON; empty as "—". */
function formatValue(v: unknown): string {
  if (v == null) return "—";
  if (typeof v === "string") return v;
  if (Array.isArray(v) && v.every((x) => typeof x === "string")) return v.length ? v.join(" ") : "—";
  if (typeof v === "object" && !Array.isArray(v) && Object.keys(v as object).length === 0) return "—";
  return JSON.stringify(v);
}

/**
 * Overlay a recipe's serving fields on `dep` and roll it. Shows the fields that
 * change, what the rollout needs (extra GPUs or a capacity dip) and whether
 * LiteLLM has to re-register, before anything is applied.
 */
export function ReapplyFromRecipeDialog({
  dep,
  open,
  onClose,
}: {
  dep: ModelDeployment;
  open: boolean;
  onClose: () => void;
}) {
  const t = useTranslations("adminDeployments");
  const tc = useTranslations("common");
  // null = follow the deployment's own recipe (so a re-apply that links a recipe is reflected).
  const [choice, setChoice] = useState<string | null>(null);
  const recipeId = choice ?? dep.recipe_id ?? null;
  const { data: recipes } = useServingRecipes();
  const candidates = (recipes ?? []).filter((r) => (r.serving_mode ?? "aggregated") === dep.serving_mode);
  const diff = useRecipeDiff(dep.id, recipeId, open && !!recipeId);
  const reapply = useReapplyDeployment(dep.id);
  const blocked = diff.data?.rollout.blocked ?? null;
  const rollout = diff.data?.rollout;

  function submit() {
    reapply.mutate(recipeId, {
      onSuccess: () => { toast.success(t("reapplySuccess")); onClose(); },
      onError: (e) => toast.error(e instanceof Error ? e.message : t("reapplyFailed")),
    });
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t("reapplyTitle")}</DialogTitle>
          <DialogDescription>{t("reapplyDesc")}</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="reapply-recipe">{t("reapplyRecipe")}</Label>
            {candidates.length === 0 ? (
              <p className="text-xs text-muted-foreground">{t("reapplyNoRecipes")}</p>
            ) : (
              <select
                id="reapply-recipe"
                className="w-full h-9 rounded-md border border-input bg-transparent px-3 text-sm"
                value={recipeId ?? ""}
                onChange={(e) => setChoice(e.target.value || null)}
              >
                {!recipeId && <option value="">{t("reapplyPickRecipe")}</option>}
                {candidates.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
              </select>
            )}
          </div>

          {recipeId && diff.isLoading && (
            <div className="flex justify-center py-4"><Loader2 className="size-5 animate-spin text-muted-foreground" /></div>
          )}
          {recipeId && diff.error && (
            <p className="text-xs text-destructive" data-testid="reapply-error">
              {diff.error instanceof Error ? diff.error.message : t("reapplyFailed")}
            </p>
          )}
          {diff.data && (
            <>
              {diff.data.changes.length === 0 ? (
                <p className="rounded-md border bg-muted/30 px-3 py-2 text-xs text-muted-foreground" data-testid="reapply-no-changes">
                  {t("reapplyNoChanges")}
                </p>
              ) : (
                <div className="max-h-72 overflow-auto rounded-md border" data-testid="reapply-changes">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="w-36">{t("reapplyColField")}</TableHead>
                        <TableHead>{t("reapplyColCurrent")}</TableHead>
                        <TableHead>{t("reapplyColProposed")}</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {diff.data.changes.map((c) => (
                        <TableRow key={c.field}>
                          <TableCell className="font-mono text-xs align-top">{c.field}</TableCell>
                          <TableCell className="font-mono text-xs align-top break-all text-muted-foreground">{formatValue(c.current)}</TableCell>
                          <TableCell className="font-mono text-xs align-top break-all">{formatValue(c.proposed)}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              )}
              {rollout && (
                <p className="text-xs text-muted-foreground" data-testid="reapply-rollout-note">
                  {rollout.mode === "surge"
                    ? t("reapplySurgeNote", { count: rollout.extra_gpus, key: rollout.gpu_resource_key })
                    : t("reapplyReplaceNote")}
                </p>
              )}
              {diff.data.reregister && (
                <div className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs">
                  <AlertTriangle className="size-4 shrink-0 text-amber-500 mt-0.5" />
                  <span className="text-muted-foreground">{t("reapplyReregisterNote")}</span>
                </div>
              )}
              {blocked && (
                <p className="text-xs text-destructive" data-testid="reapply-blocked">{blocked}</p>
              )}
            </>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={reapply.isPending}>{tc("cancel")}</Button>
          <Button onClick={submit} disabled={!diff.data || !!blocked || reapply.isPending}>
            {reapply.isPending && <Loader2 className="size-3.5 animate-spin" />}{t("reapplySubmit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
