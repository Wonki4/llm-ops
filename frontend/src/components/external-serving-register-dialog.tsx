"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";

import { ExternalServing, useRegisterExternalServing } from "@/hooks/use-api";
import { RegistrationPreview, useRegistrationPreview } from "@/components/litellm-registration-preview";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/** Registration dialog for a discovered external serving.
 *  model_name defaults to the basename of the discovered --model value;
 *  served_model_name defaults to the full value (vLLM's default served name). */
export function ExternalServingRegisterDialog({
  serving,
  onClose,
}: {
  serving: ExternalServing | null;
  onClose: () => void;
}) {
  const t = useTranslations("adminDeployments");
  const register = useRegisterExternalServing();

  const [modelName, setModelName] = useState("");
  const [servedModelName, setServedModelName] = useState("");
  const [apiBase, setApiBase] = useState("");
  const [appendV1, setAppendV1] = useState(true);
  const [apiKey, setApiKey] = useState("");
  const [error, setError] = useState<string | null>(null);

  // Reset the form whenever a different serving is targeted. Adjusting state
  // during render (rather than in an effect) avoids an extra render pass —
  // see https://react.dev/learn/you-might-not-need-an-effect#adjusting-some-state-when-a-prop-changes
  const [prevServing, setPrevServing] = useState<ExternalServing | null>(null);
  if (serving !== prevServing) {
    setPrevServing(serving);
    if (serving) {
      const path = serving.model_path;
      setModelName(path ? path.split("/").filter(Boolean).pop() ?? serving.deployment_name : serving.deployment_name);
      setServedModelName(path ?? serving.deployment_name);
      setApiBase("");
      setAppendV1(true);
      setApiKey("");
      setError(null);
    }
  }

  const draft = serving && modelName.trim() && servedModelName.trim() && apiBase.trim() ? {
    cluster_id: serving.cluster_id,
    namespace: serving.namespace,
    deployment_name: serving.deployment_name,
    model_name: modelName.trim(),
    served_model_name: servedModelName.trim(),
    api_base: apiBase.trim(),
    append_v1: appendV1,
  } : null;
  const preview = useRegistrationPreview("/api/model-deployments/external/register-preview", draft);

  const submit = () => {
    if (!draft || !preview.data) return;
    setError(null);
    register.mutate(
      { ...draft, ...(apiKey ? { api_key: apiKey } : {}) },
      {
        onSuccess: () => onClose(),
        onError: (e: Error) => setError(t("registerFailed", { message: e.message })),
      },
    );
  };

  return (
    <Dialog open={!!serving} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t("registerDialogTitle")}</DialogTitle>
          <DialogDescription>{t("registerDialogDesc")}</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="ext-model-name">{t("fieldModelName")}</Label>
            <Input id="ext-model-name" value={modelName} onChange={(e) => setModelName(e.target.value)} />
          </div>
          <div className="space-y-2">
            <Label htmlFor="ext-served-name">{t("fieldServedModelName")}</Label>
            <Input id="ext-served-name" value={servedModelName} onChange={(e) => setServedModelName(e.target.value)} />
          </div>
          <div className="space-y-2">
            <Label htmlFor="ext-api-base">{t("fieldApiBase")}</Label>
            <Input
              id="ext-api-base"
              value={apiBase}
              placeholder={t("fieldApiBasePlaceholder")}
              onChange={(e) => setApiBase(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="ext-api-key">{t("fieldApiKey")}</Label>
            <Input type="password" id="ext-api-key" value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={appendV1} onChange={(e) => setAppendV1(e.target.checked)} />
            {t("appendV1")}
          </label>
          <RegistrationPreview preview={preview} />
          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>{t("cancel")}</Button>
          <Button onClick={submit} disabled={!preview.data || register.isPending}>
            {register.isPending && <Loader2 className="size-4 animate-spin" />}
            {t("submitRegister")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
