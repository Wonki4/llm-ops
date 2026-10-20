"use client";

import { useEffect } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { useResolveStorages } from "@/hooks/use-api";
import type { ServingRecipe, StorageResolution } from "@/types";

export function recipeStorageIds(recipe: Pick<ServingRecipe, "storages" | "pd_config">): string[] {
  const ids = new Set((recipe.storages ?? []).map((s) => s.storage_id));
  for (const role of ["prefill", "decode"] as const) {
    for (const s of recipe.pd_config?.[role]?.storages ?? []) ids.add(s.storage_id);
  }
  return [...ids];
}

function targetOf(r: StorageResolution, t: (k: string) => string): string {
  const b = r.binding;
  if (!b) return t("resolutionNone");
  if (b.type === "pvc") return `PVC ${b.claim_name} (${b.namespace})`;
  return `NFS ${b.nfs_server}:${b.nfs_path}${b.namespace ? ` (${b.namespace})` : ` (${t("resolutionAll")})`}`;
}

/** Per-storage resolution for the chosen cluster/namespace; reports whether any storage is unbound. */
export function StorageResolutionPreview({
  storageIds, clusterId, namespace, onBlockedChange,
}: {
  storageIds: string[];
  clusterId: string | null;
  namespace: string;
  onBlockedChange: (blocked: boolean) => void;
}) {
  const t = useTranslations("storages");
  const { data, isFetching, isError, refetch } = useResolveStorages(storageIds, clusterId, namespace);
  const blocked = storageIds.length > 0 && (!data || data.some((r) => !r.binding));

  useEffect(() => { onBlockedChange(blocked); }, [blocked, onBlockedChange]);

  if (storageIds.length === 0) return null;
  return (
    <div className="sm:col-span-2 rounded-md border bg-muted/30 p-3 text-xs" data-testid="storage-resolution">
      <div className="mb-1 font-medium">{t("resolution")}</div>
      {isError ? (
        <div className="flex items-center gap-2 text-destructive">
          <span>{t("resolutionError")}</span>
          <button type="button" className="underline" onClick={() => refetch()}>{t("resolutionRetry")}</button>
        </div>
      ) : isFetching && !data ? (
        <div className="h-4 w-40 animate-pulse rounded bg-muted" />
      ) : (
        <ul className="space-y-1">
          {(data ?? []).map((r) => (
            <li key={r.storage_id} className="flex flex-wrap items-center gap-2 font-mono">
              <span className={r.binding ? "" : "text-destructive"}>{r.binding ? "✓" : "✗"}</span>
              <span className="font-medium">{r.name ?? r.storage_id}</span>
              <span className="text-muted-foreground">→ {targetOf(r, t)}</span>
              {r.pvc_phase && r.pvc_phase !== "Bound" && (
                <span className="text-amber-600">{t.has(`status_${r.pvc_phase}`) ? t(`status_${r.pvc_phase}`) : r.pvc_phase}</span>
              )}
              {!r.binding && (
                <Link className="underline" href={`/admin/storages/${r.storage_id}`}>{t("addBindingLink")}</Link>
              )}
            </li>
          ))}
        </ul>
      )}
      {blocked && !(isFetching && !data) && <p className="mt-2 text-destructive">{t("deployBlocked")}</p>}
    </div>
  );
}
