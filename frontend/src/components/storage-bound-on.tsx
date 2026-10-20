"use client";

import { useTranslations } from "next-intl";

import type { StorageBoundOn as BoundOn } from "@/types";
import { Badge } from "@/components/ui/badge";

/** Where a storage is bound: one chip per binding, "<cluster>/<namespace>" (`*` = every namespace). */
export function StorageBoundOn({ boundOn }: { boundOn: BoundOn[] | undefined }) {
  const t = useTranslations("storages");
  if (!boundOn?.length) return <span className="text-xs text-amber-600">{t("notBound")}</span>;
  return (
    <div className="flex flex-wrap gap-1">
      {boundOn.map((b) => (
        <Badge key={`${b.cluster_id ?? "default"}/${b.namespace ?? "*"}`} variant="outline" className="font-mono text-xs">
          {b.cluster_name}/{b.namespace ?? "*"}
        </Badge>
      ))}
    </div>
  );
}
