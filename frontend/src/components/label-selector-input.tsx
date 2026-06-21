"use client";

import { useState } from "react";
import { Plus, X } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";

type Row = { key: string; value: string };

function parse(selector: string): Row[] {
  const rows = selector
    .split(",")
    .map((p) => p.trim())
    .filter(Boolean)
    .map((p) => {
      const i = p.indexOf("=");
      return i >= 0
        ? { key: p.slice(0, i).trim(), value: p.slice(i + 1).trim() }
        : { key: p, value: "" };
    });
  return rows.length ? rows : [{ key: "", value: "" }];
}

function serialize(rows: Row[]): string {
  return rows
    .filter((r) => r.key.trim())
    .map((r) => `${r.key.trim()}=${r.value.trim()}`)
    .join(",");
}

type Props = {
  /** Comma-separated label selector, e.g. "app=vllm,model=x". */
  value: string;
  onChange: (value: string) => void;
  addLabel: string;
  keyPlaceholder?: string;
  valuePlaceholder?: string;
};

/**
 * Edits a Kubernetes label selector as key/value rows, serialized to a
 * comma-separated `k=v,k=v` string. Supports multiple labels.
 */
export function LabelSelectorInput({ value, onChange, addLabel, keyPlaceholder, valuePlaceholder }: Props) {
  const [rows, setRows] = useState<Row[]>(() => parse(value));

  const update = (next: Row[]) => {
    setRows(next);
    onChange(serialize(next));
  };

  return (
    <div className="space-y-2">
      {rows.map((row, i) => (
        <div key={i} className="flex items-center gap-2">
          <Input
            value={row.key}
            placeholder={keyPlaceholder ?? "key"}
            onChange={(e) => update(rows.map((r, j) => (j === i ? { ...r, key: e.target.value } : r)))}
            className="font-mono text-xs"
          />
          <span className="text-muted-foreground">=</span>
          <Input
            value={row.value}
            placeholder={valuePlaceholder ?? "value"}
            onChange={(e) => update(rows.map((r, j) => (j === i ? { ...r, value: e.target.value } : r)))}
            className="font-mono text-xs"
          />
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            className="shrink-0"
            disabled={rows.length === 1}
            onClick={() => update(rows.filter((_, j) => j !== i))}
          >
            <X className="size-3.5" />
          </Button>
        </div>
      ))}
      <Button type="button" variant="outline" size="sm" onClick={() => update([...rows, { key: "", value: "" }])}>
        <Plus className="size-3.5" />
        {addLabel}
      </Button>
    </div>
  );
}
