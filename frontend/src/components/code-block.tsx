"use client";

import { useState } from "react";
import { Check, Copy } from "lucide-react";

/** Copyable code snippet for the guide page: bordered <pre> with a
 *  copy-to-clipboard button (icon flips to a check for a moment). */
export function CodeBlock({ code }: { code: string }) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="relative rounded-md border bg-muted/40">
      <button
        type="button"
        onClick={copy}
        aria-label="copy"
        className="absolute right-2 top-2 rounded p-1 text-muted-foreground transition-colors hover:text-foreground"
      >
        {copied ? <Check className="size-4" /> : <Copy className="size-4" />}
      </button>
      <pre className="overflow-x-auto p-4 pr-10 text-xs leading-relaxed">
        <code>{code}</code>
      </pre>
    </div>
  );
}
