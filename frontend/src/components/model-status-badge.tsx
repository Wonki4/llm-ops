import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";
import type { ModelStatus } from "@/types";

const STATUS_STYLES: Record<ModelStatus, string> = {
  testing:
    "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
  prerelease:
    "bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400",
  lts: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400",
  deprecating:
    "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400",
  deprecated:
    "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",
};

/** Colored, localized model lifecycle status badge (shared across model views). */
export function ModelStatusBadge({
  status,
  className = "",
}: {
  status: ModelStatus;
  className?: string;
}) {
  const tms = useTranslations("modelStatus");
  return <Badge className={`${STATUS_STYLES[status]} ${className}`}>{tms(status)}</Badge>;
}
