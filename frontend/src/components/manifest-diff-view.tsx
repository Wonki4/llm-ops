"use client";

import { Fragment, useMemo, useState } from "react";
import { useTranslations } from "next-intl";

import type { ManifestDiff, ManifestDiffRow } from "@/types";
import { foldRows } from "@/lib/manifest-diff";

const LEFT_BG: Record<ManifestDiffRow["op"], string> = {
  equal: "",
  replace: "bg-red-500/10",
  delete: "bg-red-500/10",
  insert: "bg-muted/60",
};
const RIGHT_BG: Record<ManifestDiffRow["op"], string> = {
  equal: "",
  replace: "bg-emerald-500/10",
  insert: "bg-emerald-500/10",
  delete: "bg-muted/60",
};

function Side({ line, bg, border }: { line: [number, string] | null; bg: string; border?: boolean }) {
  return (
    <div className={`grid grid-cols-[3rem_1fr] ${bg} ${border ? "border-l" : ""}`}>
      <span className="select-none pr-2 text-right text-muted-foreground/60">{line?.[0] ?? ""}</span>
      <span className="whitespace-pre-wrap break-all pr-2">{line?.[1] ?? ""}</span>
    </div>
  );
}

/**
 * One manifest's YAML, current (left) against after the re-apply (right). Unchanged
 * runs are folded to a few lines around each change; a fold opens on click, and
 * `expandAll` opens them all. Remount (key) when the manifest or recipe changes.
 */
export function ManifestDiffView({
  diff,
  expandAll,
  leftLabel,
  rightLabel,
}: {
  diff: ManifestDiff;
  expandAll: boolean;
  leftLabel: string;
  rightLabel: string;
}) {
  const t = useTranslations("adminDeployments");
  const segments = useMemo(() => foldRows(diff.rows), [diff.rows]);
  const [opened, setOpened] = useState<Set<number>>(() => new Set());

  return (
    <div className="overflow-hidden rounded-md border" data-testid="manifest-diff">
      <div className="grid grid-cols-2 border-b bg-muted/30 text-xs font-medium">
        <div className="px-3 py-1.5">{leftLabel}</div>
        <div className="border-l px-3 py-1.5">{rightLabel}</div>
      </div>
      <div className="grid grid-cols-2 font-mono text-xs leading-5">
        {segments.map((seg, si) =>
          seg.kind === "fold" && !expandAll && !opened.has(seg.id) ? (
            <button
              key={`f${seg.id}`}
              type="button"
              className="col-span-2 bg-muted/40 py-0.5 font-sans text-[11px] text-primary hover:bg-muted"
              onClick={() => setOpened((s) => new Set(s).add(seg.id))}
              data-testid="diff-fold"
            >
              {t("reapplyFold", { count: seg.rows.length })}
            </button>
          ) : (
            <Fragment key={`s${si}`}>
              {seg.rows.map((r, ri) => (
                <Fragment key={ri}>
                  <Side line={r.left} bg={LEFT_BG[r.op]} />
                  <Side line={r.right} bg={RIGHT_BG[r.op]} border />
                </Fragment>
              ))}
            </Fragment>
          ),
        )}
      </div>
    </div>
  );
}
