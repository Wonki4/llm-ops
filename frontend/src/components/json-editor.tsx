"use client";

import { useMemo } from "react";
import { cn } from "@/lib/utils";

type Props = {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  /** Tailwind min-height class, e.g. "min-h-28". */
  minHeight?: string;
  /** When true (default), the value must be a JSON object (not array/scalar). */
  requireObject?: boolean;
};

/** Validate a JSON string; returns an error message or null. */
export function jsonError(value: string, requireObject = true): string | null {
  const t = value.trim();
  if (!t) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(t);
  } catch (e) {
    return e instanceof Error ? e.message : "Invalid JSON";
  }
  if (requireObject && (typeof parsed !== "object" || parsed === null || Array.isArray(parsed))) {
    return "Must be a JSON object";
  }
  return null;
}

/**
 * A controlled JSON textarea with inline validation and a one-click formatter.
 * Reusable across forms that accept a free-form JSON object (llm-d values
 * override, benchmark extra params / serving overrides).
 */
export function JsonEditor({
  id,
  value,
  onChange,
  placeholder,
  minHeight = "min-h-28",
  requireObject = true,
}: Props) {
  const error = useMemo(() => jsonError(value, requireObject), [value, requireObject]);
  const canFormat = !!value.trim() && !error;

  const format = () => {
    try {
      onChange(JSON.stringify(JSON.parse(value), null, 2));
    } catch {
      /* button only shown when valid */
    }
  };

  return (
    <div className="space-y-1">
      <div className="relative">
        <textarea
          id={id}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          spellCheck={false}
          className={cn(
            "w-full rounded-md border bg-transparent px-3 py-2 font-mono text-xs shadow-xs",
            "focus-visible:outline-none focus-visible:ring-1",
            minHeight,
            error
              ? "border-destructive focus-visible:ring-destructive"
              : "border-input focus-visible:ring-ring",
          )}
        />
        {canFormat && (
          <button
            type="button"
            onClick={format}
            className="absolute right-2 top-2 rounded bg-muted/70 px-1.5 py-0.5 text-[10px] text-muted-foreground hover:text-foreground"
          >
            format
          </button>
        )}
      </div>
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  );
}
