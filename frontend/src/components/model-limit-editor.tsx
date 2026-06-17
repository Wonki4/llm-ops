"use client";

import { useState } from "react";
import { Plus, X } from "lucide-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

type Row = { model: string; tpm: string; rpm: string };

/** A selectable model: `value` is the technical model_name LiteLLM enforces on,
 * `label` is the human-friendly catalog display name shown in the dropdown. */
export type ModelOption = { value: string; label: string };

function buildRows(
  tpm: Record<string, number> | null | undefined,
  rpm: Record<string, number> | null | undefined,
): Row[] {
  const models = new Set([...Object.keys(tpm || {}), ...Object.keys(rpm || {})]);
  return [...models].map((m) => ({
    model: m,
    tpm: tpm?.[m] != null ? String(tpm[m]) : "",
    rpm: rpm?.[m] != null ? String(rpm[m]) : "",
  }));
}

/**
 * Editor for per-model TPM/RPM limits. Emits two dicts ({model_name: value}) on
 * every change. State is initialised once from the initial props, so callers
 * should remount it (via a React `key`) when the edit target changes.
 *
 * The dropdown shows display names but the STORED key is always the technical
 * model_name — LiteLLM matches per-model limits against the request's model_name.
 *
 * NOTE: a per-key override is a WHOLE-MAP replacement on the LiteLLM side (it
 * does not merge with the team value), so prefill this with the effective limits
 * (team-inherited values included) and let the admin edit the full set.
 */
export function ModelLimitEditor({
  initialTpm,
  initialRpm,
  onChange,
  models = [],
}: {
  initialTpm: Record<string, number> | null | undefined;
  initialRpm: Record<string, number> | null | undefined;
  onChange: (tpm: Record<string, number>, rpm: Record<string, number>) => void;
  /** Selectable models (value = model_name, label = display_name). */
  models?: ModelOption[];
}) {
  const t = useTranslations("modelLimits");
  const [rows, setRows] = useState<Row[]>(() => buildRows(initialTpm, initialRpm));

  // Label lookup so already-saved rows render with their display name even if
  // the model is no longer in the team's current list.
  const labelOf = (value: string) =>
    models.find((m) => m.value === value)?.label ?? value;

  const emit = (next: Row[]) => {
    const tpm: Record<string, number> = {};
    const rpm: Record<string, number> = {};
    for (const r of next) {
      const m = r.model.trim();
      if (!m) continue;
      if (r.tpm.trim() !== "" && Number.isFinite(Number(r.tpm))) {
        tpm[m] = Math.floor(Number(r.tpm));
      }
      if (r.rpm.trim() !== "" && Number.isFinite(Number(r.rpm))) {
        rpm[m] = Math.floor(Number(r.rpm));
      }
    }
    onChange(tpm, rpm);
  };

  const update = (next: Row[]) => {
    setRows(next);
    emit(next);
  };
  const setRow = (i: number, patch: Partial<Row>) =>
    update(rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  const addRow = () => update([...rows, { model: "", tpm: "", rpm: "" }]);
  const removeRow = (i: number) => update(rows.filter((_, idx) => idx !== i));

  return (
    <div className="space-y-2">
      {rows.length > 0 && (
        <div className="grid grid-cols-[1fr_88px_88px_32px] gap-2 text-xs text-muted-foreground">
          <span>{t("model")}</span>
          <span>TPM</span>
          <span>RPM</span>
          <span />
        </div>
      )}
      {rows.map((r, i) => {
        // Hide models already chosen in other rows; keep this row's own value.
        const chosenElsewhere = new Set(
          rows.filter((_, idx) => idx !== i).map((x) => x.model),
        );
        const options = models.filter(
          (m) => m.value === r.model || !chosenElsewhere.has(m.value),
        );
        return (
          <div key={i} className="grid grid-cols-[1fr_88px_88px_32px] items-center gap-2">
            <Select value={r.model} onValueChange={(v) => setRow(i, { model: v })}>
              <SelectTrigger className="w-full">
                <SelectValue placeholder={t("model")}>
                  {r.model ? labelOf(r.model) : undefined}
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                {/* Ensure the current value is always selectable even if not in options. */}
                {r.model && !options.some((m) => m.value === r.model) && (
                  <SelectItem value={r.model}>{labelOf(r.model)}</SelectItem>
                )}
                {options.map((m) => (
                  <SelectItem key={m.value} value={m.value}>
                    {m.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Input
              type="number"
              min={0}
              value={r.tpm}
              placeholder="∞"
              onChange={(e) => setRow(i, { tpm: e.target.value })}
            />
            <Input
              type="number"
              min={0}
              value={r.rpm}
              placeholder="∞"
              onChange={(e) => setRow(i, { rpm: e.target.value })}
            />
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-8"
              onClick={() => removeRow(i)}
            >
              <X className="size-3.5" />
            </Button>
          </div>
        );
      })}
      <Button type="button" variant="outline" size="sm" onClick={addRow}>
        <Plus className="mr-1 size-3.5" /> {t("addModel")}
      </Button>
      <p className="text-xs text-muted-foreground">{t("editorHint")}</p>
    </div>
  );
}
