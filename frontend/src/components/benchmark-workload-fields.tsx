"use client";
import { useTranslations } from "next-intl";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { FormSelect, FormSelectItem } from "@/components/ui/form-select";
import type { LoadPreset } from "@/types";
import { applyPreset, matchedPresetKey, type PerfParams } from "@/lib/benchmark-workload";
export { DEFAULT_PERF_PARAMS, matchedPresetKey, type PerfParams } from "@/lib/benchmark-workload";

export function PerfWorkloadFields({ presets, perf, onChange, idPrefix = "", estimatedInput = false }: {
  idPrefix?: string; estimatedInput?: boolean; presets: Record<string, LoadPreset> | undefined; perf: PerfParams; onChange: (next: PerfParams) => void;
}) {
  const t = useTranslations("benchmarkSimple");
  const active = matchedPresetKey(presets, perf);
  const agentic = perf.workload_type === "agentic_replay";
  return <div className="space-y-4">
    <div className="grid gap-4 sm:grid-cols-2">
      <div className="space-y-1.5"><Label htmlFor={`${idPrefix}workload_type`}>{t("inputType")}</Label><FormSelect id={`${idPrefix}workload_type`} className="w-full" value={perf.workload_type} onValueChange={value => {
        const preset = Object.values(presets ?? {}).find(p => (p.workload_type ?? "synthetic") === value);
        onChange(preset ? applyPreset(perf, preset) : { ...perf, workload_type: value as PerfParams["workload_type"], scenario: undefined });
      }}>{["synthetic", "agentic_replay"].map(k => <FormSelectItem value={k} key={k}>{t(k)}</FormSelectItem>)}</FormSelect></div>
      {presets && <div className="space-y-1.5"><Label htmlFor={`${idPrefix}preset`}>{t("preset")}</Label><FormSelect id={`${idPrefix}preset`} className="w-full" value={active ?? ""} placeholder={t("custom")} onValueChange={key => { if (presets?.[key]) onChange(applyPreset(perf, presets[key])); }}>
        {Object.entries(presets ?? {}).filter(([, p]) => (p.workload_type ?? "synthetic") === perf.workload_type).map(([key, p]) => <FormSelectItem key={key} value={key}>{p.name ?? key}</FormSelectItem>)}
      </FormSelect></div>}
    </div>
    {agentic && <p className="rounded-md bg-muted p-3 text-sm">{t("scenarioHint")}</p>}
    <div className="grid gap-4 sm:grid-cols-3">
      <NumberField id={idPrefix + (agentic ? "num_sessions" : "num_prompts")} label={t(agentic ? "sessions" : "requests")} hint="" min={1} value={agentic ? perf.num_sessions : perf.num_prompts} onChange={v => onChange({ ...perf, [agentic ? "num_sessions" : "num_prompts"]: v })} />
      <NumberField id={`${idPrefix}max_concurrency`} label={t(agentic ? "concurrentSessions" : "concurrency")} hint="" min={1} value={Number(perf.max_concurrency)} onChange={v => onChange({ ...perf, max_concurrency: v })} />
      {!agentic && <NumberField id={`${idPrefix}cache_ratio`} label={t("ratio")} hint={t("ratioHint")} min={0} max={100} step="any" value={perf.cache_ratio} onChange={v => onChange({ ...perf, cache_ratio: v })} />}
      {!agentic && <NumberField id={`${idPrefix}random_input_len`} label={t(estimatedInput ? "estimatedInputTokens" : "inputTokens")} hint="" min={1} value={perf.random_input_len} onChange={v => onChange({ ...perf, random_input_len: v })} />}
      <NumberField id={`${idPrefix}random_output_len`} label={t(agentic ? "turnOutput" : "outputTokens")} hint="" min={1} value={perf.random_output_len} onChange={v => onChange({ ...perf, random_output_len: v })} />
    </div>
  </div>;
}

export function AdvancedWorkloadFields({ perf, onChange }: { perf: PerfParams; onChange: (p: PerfParams) => void }) {
  const t = useTranslations("benchmarkForm");
  return <div className="space-y-4"><div className="grid gap-4 sm:grid-cols-2">
    <NumberField id="seed" label={t("seedLabel")} hint="" min={0} value={perf.seed} onChange={v => onChange({ ...perf, seed: v })} />
    <RequestRateField perf={perf} onChange={onChange} />
  </div>
  <div className="space-y-1.5"><Label htmlFor="tokenizer">{t("tokenizerLabel")}</Label><Input id="tokenizer" value={perf.tokenizer} onChange={e => onChange({ ...perf, tokenizer: e.target.value })} /><p className="text-xs text-muted-foreground">{t("tokenizerHint")}</p></div>
  {perf.workload_type === "synthetic" && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={perf.ignore_eos} onChange={e => onChange({ ...perf, ignore_eos: e.target.checked })} />{t("ignoreEosLabel")}</label>}
  </div>;
}

export function RequestRateField({ perf, onChange, disabled = false }: {
  perf: PerfParams; onChange: (p: PerfParams) => void; disabled?: boolean;
}) {
  const t = useTranslations("benchmarkSimple");
  const agentic = perf.workload_type === "agentic_replay";
  const invalid = perf.request_rate !== "" && !(Number(perf.request_rate) > 0);
  return <div className="space-y-1.5">
    <Label htmlFor="request_rate">{t(agentic ? "sessionRate" : "requestRate")}</Label>
    <Input id="request_rate" type="number" min={0} step="any" disabled={disabled}
      placeholder={t("rateUnlimited")} aria-describedby="request-rate-hint"
      value={perf.request_rate}
      ref={node => { node?.setCustomValidity(invalid ? t("ratePositive") : ""); }}
      onInvalid={e => e.currentTarget.closest("details")?.setAttribute("open", "")}
      onChange={e => onChange({ ...perf, request_rate: e.target.value })} />
    <p id="request-rate-hint" className="text-xs text-muted-foreground">{t(agentic ? "sessionRateHint" : "requestRateHint")}</p>
  </div>;
}

export function NumberField({
  id,
  label,
  hint,
  value,
  onChange,
  min,
  step,
  max,
}: {
  id: string;
  label: string;
  hint: string;
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: string;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        type="number"
        value={value}
        required
        min={min}
        max={max}
        step={step}
        onChange={(e) => onChange(Number(e.target.value))}
      />
      <p className="text-xs text-muted-foreground">{hint}</p>
    </div>
  );
}
