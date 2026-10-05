"use client";

import { useTranslations } from "next-intl";

import type { EngineArgs, ServingEngine } from "@/types";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ENGINE_ARG_FIELDS, argLabelKey, type EngineArgField } from "@/lib/serving-engines";

/** Labelled form row; `span2` spans both columns of a 2-col grid. */
export function Field({
  id, label, required, span2, hint, children,
}: { id: string; label: string; required?: boolean; span2?: boolean; hint?: string; children: React.ReactNode }) {
  return (
    <div className={span2 ? "space-y-2 sm:col-span-2" : "space-y-2"}>
      <Label htmlFor={id}>
        {label}
        {required && <span className="text-destructive"> *</span>}
      </Label>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

/** Monospace multi-line input for line-oriented lists (args, env, labels). */
export function Area({
  id, value, onChange, placeholder, rows = 6,
}: { id: string; value: string; onChange: (v: string) => void; placeholder?: string; rows?: number }) {
  return (
    <textarea
      id={id}
      rows={rows}
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
      className="w-full rounded-md border border-input bg-transparent px-3 py-2 font-mono text-sm leading-relaxed placeholder:text-muted-foreground/50 focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
    />
  );
}

export type EngineArgValue = string | number | boolean | undefined;

/** Merge one structured engine arg into `values` (empty/false/undefined removes it). */
export function withEngineArg(values: EngineArgs | null | undefined, key: string, value: EngineArgValue): EngineArgs | null {
  const next: EngineArgs = { ...(values ?? {}) };
  if (value === undefined || value === "" || value === false) delete next[key];
  else next[key] = value;
  return Object.keys(next).length ? next : null;
}

/** The engine's structured flags as typed inputs + checkboxes. */
export function EngineArgsFields({
  engine, values, onChange, idPrefix = "recipe-arg",
}: {
  engine: ServingEngine;
  values: EngineArgs | null;
  onChange: (key: string, value: EngineArgValue) => void;
  idPrefix?: string;
}) {
  const t = useTranslations("servingRecipes");
  const fields = ENGINE_ARG_FIELDS[engine];
  const scalar = fields.filter((f) => f.type !== "bool");
  const bools = fields.filter((f) => f.type === "bool");
  const get = (key: string) => values?.[key];
  const num = (key: string): number | "" => {
    const v = get(key);
    return typeof v === "number" ? v : "";
  };

  const inputFor = (f: EngineArgField) => {
    const id = `${idPrefix}-${f.key}`;
    switch (f.type) {
      case "int":
        return (
          <Input
            id={id} type="number" step={1} min={f.min} placeholder={f.placeholder}
            value={num(f.key)}
            onChange={(e) => onChange(f.key, e.target.value === "" ? undefined : Number(e.target.value))}
          />
        );
      case "float":
        return (
          <Input
            id={id} type="number" step={f.step} min={f.min} max={f.max} placeholder={f.placeholder}
            value={num(f.key)}
            onChange={(e) => onChange(f.key, e.target.value === "" ? undefined : Number(e.target.value))}
          />
        );
      case "text":
        return (
          <Input id={id} placeholder={f.placeholder} value={String(get(f.key) ?? "")} onChange={(e) => onChange(f.key, e.target.value)} />
        );
      case "select":
        return (
          <select
            id={id}
            value={String(get(f.key) ?? "")}
            onChange={(e) => onChange(f.key, e.target.value)}
            className="w-full h-9 rounded-md border border-input bg-transparent px-3 text-sm"
          >
            {f.options.map((o) => <option key={o} value={o}>{o === "" ? "—" : o}</option>)}
          </select>
        );
      default:
        return null;
    }
  };

  return (
    <>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {scalar.map((f) => (
          <div key={f.key} className="space-y-2">
            <Label htmlFor={`${idPrefix}-${f.key}`}>
              {t(argLabelKey(f.key))}
              <span className="ml-2 font-mono text-[11px] text-muted-foreground">--{f.key}</span>
            </Label>
            {inputFor(f)}
          </div>
        ))}
      </div>
      <div className="flex flex-wrap gap-x-6 gap-y-2">
        {bools.map((f) => (
          <label key={f.key} className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={get(f.key) === true} onChange={(e) => onChange(f.key, e.target.checked)} />
            {t(argLabelKey(f.key))}
            <span className="font-mono text-[11px] text-muted-foreground">--{f.key}</span>
          </label>
        ))}
      </div>
    </>
  );
}
