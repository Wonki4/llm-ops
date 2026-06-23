// Shared usage date helpers used by the team usage tab and the admin usage page.

export type UsagePreset = "today" | "7d" | "month" | "30d" | "custom";

/** Format a Date as a `YYYY-MM-DD` string in local time (for <input type="date">). */
export function toDateInput(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/** Resolve a preset to an inclusive {start, end} date range, or null for "custom". */
export function presetRange(preset: UsagePreset): { start: string; end: string } | null {
  if (preset === "custom") return null;
  const now = new Date();
  const end = toDateInput(now);
  if (preset === "today") return { start: end, end };
  if (preset === "7d") {
    const s = new Date(now);
    s.setDate(s.getDate() - 6);
    return { start: toDateInput(s), end };
  }
  if (preset === "30d") {
    const s = new Date(now);
    s.setDate(s.getDate() - 29);
    return { start: toDateInput(s), end };
  }
  // month: first day of current month
  const s = new Date(now.getFullYear(), now.getMonth(), 1);
  return { start: toDateInput(s), end };
}
