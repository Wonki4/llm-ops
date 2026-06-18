"use client";

import { useLocale } from "next-intl";

/**
 * Returns the BCP-47 locale tag for the current portal locale.
 *
 * The portal stores locale as a short code (`ko` / `en`) but
 * `Date#toLocaleString` and friends want a full BCP-47 tag. Use this
 * inside components to feed into `toLocaleDateString(...)` so the
 * dates follow the language switcher instead of being hardcoded to
 * `ko-KR` everywhere.
 */
export function useLocaleTag(): "ko-KR" | "en-US" {
  const locale = useLocale();
  return locale === "ko" ? "ko-KR" : "en-US";
}

/**
 * Parse a timestamp returned by the backend into a Date.
 *
 * The backend serializes some DB columns (`timestamp without time zone`,
 * stored in UTC) via Python `.isoformat()`, which omits the timezone suffix
 * (e.g. `2026-07-16T10:47:43`). `new Date()` would then interpret that as
 * LOCAL time and show the raw UTC wall-clock. We tag tz-less strings as UTC so
 * `toLocale*` renders in the viewer's local timezone.
 *
 * Self-correcting: strings that already carry a timezone (`Z` or `+09:00`) are
 * left untouched, so tz-aware fields keep rendering correctly.
 *
 * NOTE: only use for instants (real timestamps). Do NOT use for calendar dates
 * like model `status_schedule` values (`2026-01-15`) — those are wall dates and
 * must not be timezone-shifted.
 */
export function parseServerDate(dateStr: string): Date {
  const hasTz = /[zZ]|[+-]\d{2}:?\d{2}$/.test(dateStr);
  return new Date(hasTz ? dateStr : `${dateStr}Z`);
}
