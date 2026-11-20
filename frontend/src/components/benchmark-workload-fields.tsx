"use client";
import { useTranslations } from "next-intl";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import type { LoadPreset } from "@/types";
// Preset key -> i18n label key, mirrors the retired sweep form's mapping.
const PRESET_LABEL_KEY: Record<string, string> = {
  chat: "presetChat",
  long_input: "presetLongInput",
  long_output: "presetLongOutput",
};

// Portal runner workload settings. Presets (below) are one-click
// prefill for these fields — every value is then directly editable.
export const DEFAULT_PERF_PARAMS = {
  random_input_len: 1024,
  random_output_len: 128,
  num_prompts: 200,
  max_concurrency: "" as number | string,
  request_rate: "" as number | string,
  seed: 0,
  ignore_eos: false,
  tokenizer: "",
};
export type PerfParams = typeof DEFAULT_PERF_PARAMS;

// Which preset (if any) the current workload values exactly match — drives the
// highlighted card and the `preset:` record tag. Compares only the four knobs a
// preset defines.
export function matchedPresetKey(
  presets: Record<string, LoadPreset> | undefined,
  p: PerfParams,
): string | undefined {
  if (!presets) return undefined;
  for (const [key, preset] of Object.entries(presets)) {
    if (
      preset.random_input_len === p.random_input_len &&
      preset.random_output_len === p.random_output_len &&
      preset.num_prompts === p.num_prompts &&
      String(preset.max_concurrency) === String(p.max_concurrency)
    ) {
      return key;
    }
  }
  return undefined;
}

export const DEFAULT_ACCURACY_PARAMS = {
  tasks: "mmlu",
  num_fewshot: "",
  limit: "",
  batch_size: 8,
  num_concurrent: 4,
  apply_chat_template: false,
  gen_kwargs: "",
};


export function PerfWorkloadFields({
  presets,
  perf,
  onChange,
}: {
  presets: Record<string, LoadPreset> | undefined;
  perf: PerfParams;
  onChange: (next: PerfParams) => void;
}) {
  const t = useTranslations("benchmarkForm");
  const activePreset = matchedPresetKey(presets, perf);
  return (
    <>
      <div className="space-y-2">
        <Label>{t("presetLabel")}</Label>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
          {Object.entries(presets ?? {}).map(([key, p]) => (
            <button
              key={key}
              type="button"
              onClick={() =>
                onChange({
                  ...perf,
                  random_input_len: p.random_input_len,
                  random_output_len: p.random_output_len,
                  num_prompts: p.num_prompts,
                  max_concurrency: String(p.max_concurrency),
                })
              }
              className={`rounded-lg border p-3 text-left text-sm transition-colors ${
                activePreset === key ? "border-primary ring-2 ring-primary/30" : "hover:bg-muted/40"
              }`}
            >
              <div className="font-medium">{t(PRESET_LABEL_KEY[key] ?? key)}</div>
              <div className="text-xs text-muted-foreground mt-1 font-mono">
                {p.random_input_len}→{p.random_output_len} tok · {p.num_prompts} reqs · ×{p.max_concurrency}
              </div>
            </button>
          ))}
        </div>
        <p className="text-xs text-muted-foreground">{t("presetFillHint")}</p>
      </div>

      <details><summary className="cursor-pointer text-sm">{t("advanced")}</summary>
      <div className="mt-3 space-y-4">
      <div className="grid grid-cols-2 gap-4">
        <NumberField
          id="random_input_len"
          label={t("randomInputLenLabel")}
          hint={t("randomInputLenHint")}
          value={perf.random_input_len}
          onChange={(v) => onChange({ ...perf, random_input_len: v })}
          min={1}
        />
        <NumberField
          id="random_output_len"
          label={t("randomOutputLenLabel")}
          hint={t("randomOutputLenHint")}
          value={perf.random_output_len}
          onChange={(v) => onChange({ ...perf, random_output_len: v })}
          min={1}
        />
        <NumberField
          id="num_prompts"
          label={t("numPromptsLabel")}
          hint={t("numPromptsHint")}
          value={perf.num_prompts}
          onChange={(v) => onChange({ ...perf, num_prompts: v })}
          min={1}
        />
        <OptionalNumberField
          id="max_concurrency"
          label={t("maxConcurrencyLabel")}
          hint={t("maxConcurrencyHint")}
          value={`${perf.max_concurrency}`}
          onChange={(v) => onChange({ ...perf, max_concurrency: v })}
        />
        <OptionalNumberField
          id="request_rate"
          label={t("requestRateLabel")}
          hint={t("requestRateHint")}
          value={`${perf.request_rate}`}
          onChange={(v) => onChange({ ...perf, request_rate: v })}
        />
        <NumberField
          id="seed"
          label={t("seedLabel")}
          hint={t("seedHint")}
          value={perf.seed}
          onChange={(v) => onChange({ ...perf, seed: v })}
          min={0}
        />
      </div>

      <div className="flex items-center gap-2">
        <input
          id="ignore_eos"
          type="checkbox"
          className="size-4 rounded border-input"
          checked={perf.ignore_eos}
          onChange={(e) => onChange({ ...perf, ignore_eos: e.target.checked })}
        />
        <Label htmlFor="ignore_eos" className="cursor-pointer">
          {t("ignoreEosLabel")}
        </Label>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="tokenizer">{t("tokenizerLabel")}</Label>
        <Input
          id="tokenizer"
          value={perf.tokenizer}
          onChange={(e) => onChange({ ...perf, tokenizer: e.target.value })}
          placeholder={t("tokenizerPlaceholder")}
          className="font-mono"
        />
        <p className="text-xs text-muted-foreground">{t("tokenizerHint")}</p>
      </div>

      </div></details>
    </>
  );
}

export function AccuracyParamsFields({
  params,
  onChange,
}: {
  params: typeof DEFAULT_ACCURACY_PARAMS;
  onChange: (next: typeof DEFAULT_ACCURACY_PARAMS) => void;
}) {
  const t = useTranslations("benchmarkForm");
  return (
    <>
      <div className="space-y-1.5">
        <Label htmlFor="tasks">{t("tasksLabel")}</Label>
        <Input
          id="tasks"
          placeholder="mmlu, hellaswag"
          value={params.tasks}
          onChange={(e) => onChange({ ...params, tasks: e.target.value })}
        />
        <p className="text-xs text-muted-foreground">{t("tasksHint")}</p>
      </div>
      <div className="grid grid-cols-2 gap-4">
        <NumberField
          id="batch_size"
          label={t("batchSizeLabel")}
          hint={t("batchSizeHint")}
          value={params.batch_size}
          onChange={(v) => onChange({ ...params, batch_size: v })}
          min={1}
        />
        <NumberField
          id="num_concurrent"
          label={t("numConcurrentLabel")}
          hint={t("numConcurrentHint")}
          value={params.num_concurrent}
          onChange={(v) => onChange({ ...params, num_concurrent: v })}
          min={1}
        />
        <OptionalNumberField
          id="num_fewshot"
          label={t("numFewshotLabel")}
          hint={t("numFewshotHint")}
          value={params.num_fewshot}
          onChange={(v) => onChange({ ...params, num_fewshot: v })}
        />
        <OptionalNumberField
          id="limit"
          label={t("limitLabel")}
          hint={t("limitHint")}
          value={params.limit}
          onChange={(v) => onChange({ ...params, limit: v })}
        />
      </div>
      <div className="flex items-center gap-2">
        <input
          id="apply_chat_template"
          type="checkbox"
          className="size-4 rounded border-input"
          checked={params.apply_chat_template}
          onChange={(e) => onChange({ ...params, apply_chat_template: e.target.checked })}
        />
        <Label htmlFor="apply_chat_template" className="cursor-pointer">
          {t("applyChatTemplateLabel")}
        </Label>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="gen_kwargs">{t("genKwargsLabel")}</Label>
        <Input
          id="gen_kwargs"
          placeholder={t("genKwargsPlaceholder")}
          value={params.gen_kwargs}
          onChange={(e) => onChange({ ...params, gen_kwargs: e.target.value })}
        />
        <p className="text-xs text-muted-foreground">{t("genKwargsHint")}</p>
      </div>
    </>
  );
}

export function NumberField({
  id,
  label,
  hint,
  value,
  onChange,
  min,
  step,
}: {
  id: string;
  label: string;
  hint: string;
  value: number;
  onChange: (v: number) => void;
  min?: number;
  step?: string;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        type="number"
        value={value}
        min={min}
        step={step}
        onChange={(e) => onChange(Number(e.target.value))}
      />
      <p className="text-xs text-muted-foreground">{hint}</p>
    </div>
  );
}

export function OptionalNumberField({
  id,
  label,
  hint,
  value,
  onChange,
  step,
}: {
  id: string;
  label: string;
  hint: string;
  value: string;
  onChange: (v: string) => void;
  step?: string;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        type="number"
        value={value}
        step={step}
        onChange={(e) => onChange(e.target.value)}
      />
      <p className="text-xs text-muted-foreground">{hint}</p>
    </div>
  );
}
