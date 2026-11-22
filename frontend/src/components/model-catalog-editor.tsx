"use client";
import { useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { useCreateCatalogEntry, useUpdateCatalogEntry, useDeleteCatalogEntry } from "@/hooks/use-api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { ModelWithCatalog, ModelStatus } from "@/types";

const statuses: ModelStatus[] = ["testing", "prerelease", "lts", "deprecating", "deprecated"];
export function ModelCatalogEditor({model}: {model: ModelWithCatalog}) {
  const t = useTranslations("portalModels");
  const statusLabel = useTranslations("modelStatus");
  const c = model.catalog;
  const [name, setName] = useState(c?.display_name || model.model_name);
  const [description, setDescription] = useState(c?.description || "");
  const [icon, setIcon] = useState(c?.icon_url || "");
  const [status, setStatus] = useState<ModelStatus>(c?.status || "testing");
  const [visible, setVisible] = useState(c?.visible !== false);
  const [schedule, setSchedule] = useState<Record<string, string>>(c?.status_schedule || {});
  const create = useCreateCatalogEntry(); const update = useUpdateCatalogEntry(); const remove = useDeleteCatalogEntry();
  const busy = create.isPending || update.isPending || remove.isPending;
  async function save() {
    const body = {display_name: name.trim(), description: description.trim(), icon_url: icon.trim() || null,
      status, visible, status_schedule: Object.fromEntries(Object.entries(schedule).filter(([,v]) => v))};
    try {
      if (c) await update.mutateAsync({catalogId: c.id, body});
      else await create.mutateAsync({...body, model_name: model.model_name, is_external: !model.litellm_info});
      toast.success(t("saved"));
    } catch (e) { toast.error((e as Error).message); }
  }
  return <div className="max-w-2xl space-y-4 rounded-lg border bg-background p-5">
    <label className="block space-y-1"><span>{t("displayName")}</span><Input value={name} onChange={e => setName(e.target.value)} /></label>
    <label className="block space-y-1"><span>{t("description")}</span><textarea className="min-h-24 w-full rounded-md border bg-background p-2" value={description} onChange={e => setDescription(e.target.value)} /></label>
    <label className="block space-y-1"><span>{t("icon")}</span><Input value={icon} onChange={e => setIcon(e.target.value)} /></label>
    <label className="flex items-center gap-3">{t("status")}<select className="rounded border bg-background p-2" value={status} onChange={e => setStatus(e.target.value as ModelStatus)}>{statuses.map(s => <option key={s} value={s}>{statusLabel(s)}</option>)}</select></label>
    <label className="flex items-center gap-2"><input type="checkbox" checked={visible} onChange={e => setVisible(e.target.checked)} />{t("visible")}</label>
    <fieldset className="grid gap-2"><legend className="mb-2 font-medium">{t("schedule")}</legend>{statuses.map(s => <label key={s} className="flex items-center justify-between gap-3">{statusLabel(s)}<Input className="w-48" type="date" value={schedule[s] || ""} onChange={e => setSchedule({...schedule, [s]:e.target.value})} /></label>)}</fieldset>
    <div className="flex gap-3"><Button disabled={busy || !name.trim()} onClick={save}>{t("save")}</Button>
      {c && <Button variant="destructive" disabled={busy} onClick={async () => { if (confirm(t("removeCatalogConfirm"))) { try {await remove.mutateAsync(c.id); toast.success(t("saved"));} catch(e) {toast.error((e as Error).message);} } }}>{t("removeCatalog")}</Button>}
    </div>
  </div>;
}
