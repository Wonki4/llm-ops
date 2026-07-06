"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";

import { ExternalServing, useRegisterExternalServing } from "@/hooks/use-api";
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
      setApiKey("");
      setError(null);
    }
  }

  const submit = () => {
    if (!serving) return;
    setError(null);
    register.mutate(
      {
        cluster_id: serving.cluster_id,
        namespace: serving.namespace,
        deployment_name: serving.deployment_name,
        model_name: modelName,
        served_model_name: servedModelName,
        api_base: apiBase,
        ...(apiKey ? { api_key: apiKey } : {}),
      },
      {
        onSuccess: () => onClose(),
        onError: (e: Error) => setError(t("registerFailed", { message: e.message })),
      },
    );
  };

  return (
    <Dialog open={!!serving} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent>
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
            <Input id="ext-api-key" value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>{t("cancel")}</Button>
          <Button onClick={submit} disabled={!modelName || !servedModelName || !apiBase || register.isPending}>
            {register.isPending && <Loader2 className="size-4 animate-spin" />}
            {t("submitRegister")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
