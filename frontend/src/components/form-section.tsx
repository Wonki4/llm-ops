"use client";

import { ChevronRight } from "lucide-react";

import { cn } from "@/lib/utils";

/** One bordered card whose sections are separated by rules (the recipe form's sections live in these). */
export function FormCard({ children, className, ...rest }: React.ComponentProps<"div">) {
  return (
    <div className={cn("divide-y rounded-xl border bg-card text-card-foreground shadow-sm", className)} {...rest}>
      {children}
    </div>
  );
}

/** A titled block inside a FormCard; `action` sits at the right of the title row. */
export function FormSection({
  title, hint, action, children,
}: { title: React.ReactNode; hint?: React.ReactNode; action?: React.ReactNode; children?: React.ReactNode }) {
  return (
    <section className="space-y-3 px-5 py-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold">{title}</h3>
          {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

/**
 * A FormSection that starts folded, showing a one-line `summary` of its values
 * next to the title. `defaultOpen` is read on mount only (native <details>).
 */
export function CollapsibleSection({
  title, summary, hint, defaultOpen, children,
}: { title: React.ReactNode; summary?: React.ReactNode; hint?: React.ReactNode; defaultOpen?: boolean; children: React.ReactNode }) {
  return (
    <details className="group px-5 py-3" open={defaultOpen}>
      <summary className="flex cursor-pointer list-none items-center gap-2 text-sm [&::-webkit-details-marker]:hidden">
        <ChevronRight className="size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-90" />
        <span className="shrink-0 font-semibold">{title}</span>
        {summary && <span className="min-w-0 truncate font-mono text-xs text-muted-foreground">{summary}</span>}
      </summary>
      <div className="mt-3 space-y-3 sm:pl-6">
        {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
        {children}
      </div>
    </details>
  );
}
