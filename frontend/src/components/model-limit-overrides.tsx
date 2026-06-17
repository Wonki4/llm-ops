"use client";

import { useTranslations } from "next-intl";

import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

/**
 * Compact display for per-model TPM/RPM limit overrides stored on a key.
 * Shows a small badge with the override count; the full `model: value`
 * breakdown appears in a hover tooltip so rows never bloat or overflow,
 * regardless of how many models or how long the model names are.
 */
export function ModelLimitOverrides({
  limits,
  inherited = false,
}: {
  limits: Record<string, number> | null | undefined;
  /** True when the value comes from the team default (not a per-key override). */
  inherited?: boolean;
}) {
  const t = useTranslations("modelLimits");
  const entries = limits ? Object.entries(limits) : [];
  if (entries.length === 0) return null;

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="mt-0.5 inline-block cursor-default rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
            {t("perModelBadge")} {entries.length}
            {inherited ? ` (${t("teamBadge")})` : ""}
          </span>
        </TooltipTrigger>
        <TooltipContent className="max-w-xs">
          <div className="space-y-0.5">
            {entries.map(([model, limit]) => (
              <div key={model} className="flex justify-between gap-3">
                <span className="truncate">{model}</span>
                <span className="tabular-nums">{limit.toLocaleString()}</span>
              </div>
            ))}
          </div>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
