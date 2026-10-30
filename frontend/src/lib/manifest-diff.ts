import type { ManifestDiffRow } from "@/types";

/** Unchanged lines kept visible around each change. */
export const DIFF_CONTEXT = 3;

/** A run of rows shown as-is, or a run of unchanged rows folded behind "⋯ N lines the same". */
export type DiffSegment =
  | { kind: "rows"; rows: ManifestDiffRow[] }
  | { kind: "fold"; id: number; rows: ManifestDiffRow[] };

/**
 * Fold every run of unchanged rows down to `context` rows next to a change
 * (none at the top or bottom of the manifest). A fold always hides at least
 * two rows; an unchanged manifest is one fold.
 */
export function foldRows(rows: ManifestDiffRow[], context = DIFF_CONTEXT): DiffSegment[] {
  const out: DiffSegment[] = [];
  let shown: ManifestDiffRow[] = [];
  let folds = 0;
  const flush = () => {
    if (shown.length) out.push({ kind: "rows", rows: shown });
    shown = [];
  };
  let i = 0;
  while (i < rows.length) {
    if (rows[i].op !== "equal") {
      shown.push(rows[i++]);
      continue;
    }
    let end = i;
    while (end < rows.length && rows[end].op === "equal") end++;
    const head = i === 0 ? 0 : context;
    const tail = end === rows.length ? 0 : context;
    if (end - i - head - tail >= 2) {
      shown.push(...rows.slice(i, i + head));
      flush();
      out.push({ kind: "fold", id: folds++, rows: rows.slice(i + head, end - tail) });
      shown.push(...rows.slice(end - tail, end));
    } else {
      shown.push(...rows.slice(i, end));
    }
    i = end;
  }
  flush();
  return out;
}
