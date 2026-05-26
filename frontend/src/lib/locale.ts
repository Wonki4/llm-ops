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
