"use client";
import { useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { useBenchmarkPresetSettings, useSaveBenchmarkPresets } from "@/hooks/use-api";
import type { BenchmarkPresetSettings } from "@/types";
import { createRequestKey } from "@/lib/request-key";
import { DEFAULT_PERF_PARAMS, applyPreset, defaultObjectives } from "@/lib/benchmark-workload";
import { PerfWorkloadFields, ObjectiveFields } from "@/components/benchmark-workload-fields";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { FormSelect, FormSelectItem } from "@/components/ui/form-select";

export function BenchmarkPresetManager() {
  const t = useTranslations("benchmarkSimple"), query = useBenchmarkPresetSettings(), save = useSaveBenchmarkPresets();
  const [draft, setDraft] = useState<BenchmarkPresetSettings | null>(null), [key, setKey] = useState("");
  const selected = draft?.presets[key];
  return <>
    <Button type="button" variant="outline" disabled={!query.data} onClick={() => { setDraft(structuredClone(query.data!)); setKey(query.data!.default_preset); }}>{t("managePresets")}</Button>
    <Dialog open={!!draft} onOpenChange={open => { if (!open) setDraft(null); }}><DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
      <DialogHeader><DialogTitle>{t("managePresets")}</DialogTitle><DialogDescription>{t("presetSaveHint")}</DialogDescription></DialogHeader>
      {draft && selected && <form className="space-y-4" onSubmit={async e => { e.preventDefault(); e.stopPropagation(); try { await save.mutateAsync(draft); setDraft(null); toast.success(t("saved")); } catch (e) { toast.error(e instanceof Error ? e.message : String(e)); } }}>
        <div className="flex gap-2"><FormSelect className="flex-1" aria-label={t("preset")} value={key} onValueChange={setKey}>{Object.entries(draft.presets).map(([id, p]) => <FormSelectItem value={id} key={id}>{p.name ?? id}{id === draft.default_preset ? " ★" : ""}</FormSelectItem>)}</FormSelect>
          <Button type="button" variant="outline" onClick={() => { const id = createRequestKey(); setDraft({ ...draft, presets: { ...draft.presets, [id]: { ...selected, name: t("newPreset") } } }); setKey(id); }}>{t("add")}</Button>
          <Button type="button" variant="outline" disabled={Object.keys(draft.presets).length === 1 || key === draft.default_preset} onClick={() => { const next = { ...draft.presets }; delete next[key]; setDraft({ ...draft, presets: next }); setKey(draft.default_preset); }}>{t("delete")}</Button>
        </div>
        <div className="space-y-1.5"><Label htmlFor="preset_name">{t("presetName")}</Label><Input id="preset_name" required maxLength={80} value={selected.name ?? ""} onChange={e => setDraft({ ...draft, presets: { ...draft.presets, [key]: { ...selected, name: e.target.value } } })} /></div>
        <PerfWorkloadFields idPrefix="preset-edit-" presets={undefined} perf={applyPreset(DEFAULT_PERF_PARAMS, selected)} onChange={p => setDraft({ ...draft, presets: { ...draft.presets, [key]: { ...selected, name: selected.name, workload_type: p.workload_type, num_sessions: p.num_sessions, cache_ratio: p.cache_ratio, random_input_len: p.random_input_len, random_output_len: p.random_output_len, num_prompts: p.num_prompts, max_concurrency: Number(p.max_concurrency) } } })} />
        <ObjectiveFields value={selected.slo ?? defaultObjectives} onChange={slo => setDraft({ ...draft, presets: { ...draft.presets, [key]: { ...selected, slo } } })} />
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={draft.default_preset === key} disabled={draft.default_preset === key} onChange={() => setDraft({ ...draft, default_preset: key })} />{t("defaultPreset")}</label>
        <p className="text-xs text-muted-foreground">{t("deleteDefaultHint")}</p>
        {save.error && <p role="alert" className="text-sm text-destructive">{save.error.message}</p>}
        <Button type="submit" disabled={save.isPending}>{t("save")}</Button>
      </form>}
    </DialogContent></Dialog>
  </>;
}
