"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { apiFetch } from "@/lib/api";
import { createRequestKey } from "@/lib/request-key";
import type { ModelConnection } from "@/hooks/use-model-connections";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";

export function ModelConnectionDialog({ connection, modelName = "", onClose }: { connection?: ModelConnection; modelName?: string; onClose: () => void }) {
  const t = useTranslations("portalModels"); const router = useRouter(); const cache = useQueryClient();
  const [values, setValues] = useState({model_name:connection?.model_name || modelName, model:connection?.model || "", api_base:connection?.api_base || "", api_version:connection?.api_version || "", api_key:""});
  const [extra, setExtra] = useState(""); const [requestKey] = useState(createRequestKey);
  const [error, setError] = useState(""); const [pending, setPending] = useState(false);
  async function save() {
    setPending(true); setError("");
    try {
      const payload: Record<string, unknown> = {};
      for (const [key, value] of Object.entries(values)) {
        if (key === "api_key") {if (value.trim()) payload[key] = value.trim();}
        else if (!connection || value !== (connection[key as keyof ModelConnection] || "")) payload[key] = value.trim();
      }
      if (extra.trim()) {const parsed = JSON.parse(extra); if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error(t("jsonObject")); payload.extra_params = parsed;}
      if (!connection) payload.request_key = requestKey;
      await apiFetch(`/api/admin/model-connections${connection ? `/${encodeURIComponent(connection.id)}` : ""}`, {method:connection ? "PATCH" : "POST", body:JSON.stringify(payload)});
      await cache.invalidateQueries(); onClose(); router.push(`/admin/models/${values.model_name.trim().split("/").map(encodeURIComponent).join("/")}?tab=connections`);
    } catch(e) {setError((e as Error).message);} finally {setPending(false);}
  }
  return <Dialog open onOpenChange={open=>{if(!open && !pending) onClose();}}><DialogContent className="max-h-[90vh] overflow-y-auto"><DialogHeader><DialogTitle>{t(connection ? "editConnection" : "newConnection")}</DialogTitle></DialogHeader>
    <p className="text-sm text-muted-foreground">{t("credentialHint")}</p>
    {(["model_name","model","api_base","api_version","api_key"] as const).map(key=><div className="space-y-1" key={key}><Label htmlFor={`connection-${key}`}>{t(`field_${key}`)}</Label><Input id={`connection-${key}`} autoComplete="off" type={key === "api_key" ? "password" : "text"} value={values[key]} onChange={e=>setValues({...values,[key]:e.target.value})} placeholder={key === "model" ? "openai/gpt-4.1" : undefined} /></div>)}
    <details><summary className="cursor-pointer text-sm">{t("providerOptions")}</summary><p className="my-2 text-xs text-muted-foreground">{t("providerOptionsHint")}</p><textarea className="min-h-32 w-full rounded border bg-background p-2 font-mono text-sm" aria-label={t("providerOptions")} value={extra} onChange={e=>setExtra(e.target.value)} placeholder="{}" /></details>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}<Button disabled={pending || !values.model.trim() || !values.model_name.trim()} onClick={save}>{t("save")}</Button>
  </DialogContent></Dialog>;
}
