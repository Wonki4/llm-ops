"use client";

import { useState } from "react";
import { Loader2, RefreshCw, Save } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import { useReregisterDeployment, useUpdateDeploymentLitellmName } from "@/hooks/use-api";
import type { ModelDeployment } from "@/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

/**
 * How the deployment is registered in LiteLLM: the name callers use (editable,
 * may be shared with other deployments) and what LiteLLM sends the server.
 * Renaming drops the old LiteLLM entry; the reconciler registers the new name
 * once the deployment is Ready again. Re-register fixes an entry in place.
 */
export function LitellmRegistrationCard({ dep }: { dep: ModelDeployment }) {
  const t = useTranslations("adminDeployments");
  const [draft, setDraft] = useState(dep.litellm_model_name ?? "");
  const rename = useUpdateDeploymentLitellmName(dep.id);
  const reregister = useReregisterDeployment(dep.id);
  const next = draft.trim() || null;
  const changed = next !== (dep.litellm_model_name ?? null);

  function save() {
    if (dep.litellm_model_id && !confirm(t("litellmRenameConfirm", { name: next ?? dep.model_name }))) return;
    rename.mutate(next, {
      onSuccess: () => toast.success(t("litellmRenameSuccess")),
      onError: (e) => toast.error(e instanceof Error ? e.message : String(e)),
    });
  }

  function register() {
    if (!confirm(t("litellmReregisterConfirm"))) return;
    reregister.mutate(undefined, {
      onSuccess: () => toast.success(t("litellmReregisterSuccess")),
      onError: (e) => toast.error(e instanceof Error ? e.message : String(e)),
    });
  }

  return (
    <Card data-testid="litellm-registration">
      <CardHeader className="flex flex-row items-center justify-between gap-2 space-y-0">
        <CardTitle className="text-base">{t("litellmSection")}</CardTitle>
        <Button
          size="sm" variant="outline" onClick={register} disabled={dep.status !== "Ready" || reregister.isPending}
          title={dep.status !== "Ready" ? t("litellmReregisterNeedsReady") : undefined} data-testid="litellm-reregister"
        >
          {reregister.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}
          {t("litellmReregister")}
        </Button>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="space-y-1">
          <label htmlFor="litellm-name" className="text-xs text-muted-foreground">{t("litellmName")}</label>
          <div className="flex flex-col gap-2 sm:flex-row">
            <Input
              id="litellm-name" className="font-mono" value={draft} placeholder={dep.model_name}
              onChange={(e) => setDraft(e.target.value)}
            />
            <Button size="sm" onClick={save} disabled={!changed || rename.isPending} data-testid="litellm-name-save">
              {rename.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <Save className="size-3.5" />}
              {t("litellmNameSave")}
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">{t("litellmNameHint")}</p>
        </div>
        {dep.litellm_model && (
          <div className="space-y-1">
            <div className="text-xs text-muted-foreground">{t("litellmServedAs")}</div>
            <div className="font-mono text-sm break-all">{dep.litellm_model}</div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
