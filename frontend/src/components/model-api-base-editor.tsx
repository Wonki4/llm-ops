"use client";

import { useId, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { apiFetch } from "@/lib/api";
import { type ModelConnection, useModelConnections } from "@/hooks/use-model-connections";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";

export function ModelApiBaseEditor({ connection }: { connection: ModelConnection }) {
  const t = useTranslations("portalModels");
  const cache = useQueryClient();
  const id = useId();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

  async function save() {
    setPending(true);
    setError("");
    try {
      await apiFetch(`/api/admin/model-connections/${encodeURIComponent(connection.id)}/api-base`, {
        method: "PATCH", body: JSON.stringify({ api_base: draft.trim() }),
      });
      await cache.invalidateQueries();
      setOpen(false);
      toast.success(t("saved"));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }

  if (!connection.api_base_editable) return <span className="text-xs text-muted-foreground">{t("readOnly")}</span>;
  return <span onClick={e => e.stopPropagation()}>
    <Button size="sm" variant="outline" onClick={() => { setDraft(connection.api_base ?? ""); setError(""); setOpen(true); }}>
      {t("editApiBase")}
    </Button>
    <Dialog open={open} onOpenChange={value => { if (!pending) setOpen(value); }}>
      <DialogContent>
        <DialogHeader><DialogTitle>{t("editApiBase")}</DialogTitle><DialogDescription>{t("apiBaseHint")}</DialogDescription></DialogHeader>
        <form className="space-y-4" onSubmit={e => { e.preventDefault(); if (!pending) void save(); }}>
          <p className="break-all font-mono text-xs">{connection.model_name} · {connection.id}</p>
          <div className="space-y-2">
            <Label htmlFor={id}>API Base URL</Label>
            <Input id={id} type="url" required maxLength={512} disabled={pending} value={draft}
              placeholder="http://model.serving.svc:8000/v1" onChange={e => setDraft(e.target.value)} />
          </div>
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          <Button type="submit" disabled={pending || !draft.trim() || draft.trim() === connection.api_base}>{t("save")}</Button>
        </form>
      </DialogContent>
    </Dialog>
  </span>;
}

/** Serving screens read the live LiteLLM endpoint, including direct upstream changes. */
export function ServingApiBaseEditor({ modelId, modelName }: { modelId: string; modelName: string }) {
  const t = useTranslations("portalModels");
  const query = useModelConnections(modelName);
  if (query.isPending) return <p className="text-xs" aria-busy="true">{t("loading")}</p>;
  if (query.isError) return <p role="alert" className="text-xs text-destructive">{query.error.message}</p>;
  const connection = query.data.find(c => c.id === modelId);
  if (!connection) return <p className="text-xs text-muted-foreground">{t("noConnections")}</p>;
  return <div className="flex flex-wrap items-center gap-2">
    <span className="break-all font-mono text-xs">API Base URL: {connection.api_base || t("providerDefault")}</span>
    <ModelApiBaseEditor connection={connection} />
  </div>;
}
