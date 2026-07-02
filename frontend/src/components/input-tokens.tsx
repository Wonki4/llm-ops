"use client";

import { useLocaleTag } from "@/lib/locale";
import { formatInputTokens } from "@/lib/usage";

/** Input-token cell shared by every usage table: `120,000 (30,000)` with the
 *  cache-read portion muted; no parenthetical when nothing was cached. */
export function InputTokens({ input, cacheRead }: { input: number; cacheRead: number }) {
  const localeTag = useLocaleTag();
  const { input: inputStr, cache } = formatInputTokens(input, cacheRead, localeTag);
  return (
    <span className="tabular-nums">
      {inputStr}
      {cache !== null && <span className="text-muted-foreground"> ({cache})</span>}
    </span>
  );
}
