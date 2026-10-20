"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import { Plus, X } from "lucide-react";

import { useStorages } from "@/hooks/use-api";
import type { StorageEntry } from "@/types";
import { StorageBoundOn } from "@/components/storage-bound-on";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/** Which entry an absolute model path lives under (mirrors backend model_path_warnings). */
export function storageForPath(path: string, entries: StorageEntry[]): StorageEntry | null | undefined {
  if (!path.startsWith("/")) return undefined; // HF repo id: no hint
  return entries.find((e) => path === e.mount_path || path.startsWith(e.mount_path.replace(/\/$/, "") + "/")) ?? null;
}

/**
 * Storage rows of a recipe or one P/D pool: [storage] [mount path] [read-only] [x],
 * each picked storage followed by chips of where it is bound.
 */
export function StorageMountsEditor({
  value, onChange, modelPath, idPrefix,
}: {
  value: StorageEntry[];
  onChange: (next: StorageEntry[]) => void;
  modelPath: string;
  idPrefix: string;
}) {
  const t = useTranslations("storages");
  const { data: storages } = useStorages();
  const byId = new Map((storages ?? []).map((s) => [s.id, s]));
  const rows = value;
  const set = (i: number, patch: Partial<StorageEntry>) => onChange(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const hint = modelPath ? storageForPath(modelPath, rows) : undefined;

  return (
    <div className="space-y-2" data-testid={`${idPrefix}-storages`}>
      {(storages ?? []).length === 0 && (
        <p className="text-xs text-muted-foreground">
          {t("noStorages")} <Link className="underline" href="/admin/storages">{t("title")}</Link>
        </p>
      )}
      {rows.map((r, i) => (
        <div key={i} className="grid grid-cols-[1fr_1fr_auto_auto] items-center gap-2">
          <select
            id={`${idPrefix}-storage-${i}`}
            aria-label={t("picker")}
            className="h-9 rounded-md border border-input bg-transparent px-3 font-mono text-sm"
            value={r.storage_id}
            onChange={(e) => set(i, { storage_id: e.target.value })}
          >
            <option value="">{t("pickerPlaceholder")}</option>
            {(storages ?? []).map((s) => (
              <option key={s.id} value={s.id} disabled={rows.some((o, j) => j !== i && o.storage_id === s.id)}>
                {s.name}{s.binding_count ? "" : ` (${t("notBound")})`}
              </option>
            ))}
          </select>
          <Input
            id={`${idPrefix}-storage-mount-${i}`}
            aria-label={t("mountPath")}
            className="font-mono"
            aria-invalid={!r.mount_path.startsWith("/") || undefined}
            title={!r.mount_path.startsWith("/") ? t("mountPathInvalid") : undefined}
            placeholder="/models"
            value={r.mount_path}
            onChange={(e) => set(i, { mount_path: e.target.value })}
          />
          <label className="flex items-center gap-1 text-xs whitespace-nowrap">
            <input type="checkbox" checked={r.read_only} onChange={(e) => set(i, { read_only: e.target.checked })} />
            {t("readOnly")}
          </label>
          <Button type="button" size="icon" variant="ghost" aria-label={t("delete")} onClick={() => onChange(rows.filter((_, j) => j !== i))}>
            <X className="size-4" />
          </Button>
          {r.storage_id && (
            <div className="col-span-4" data-testid={`${idPrefix}-storage-bound-${i}`}>
              <StorageBoundOn boundOn={byId.get(r.storage_id)?.bound_on} />
            </div>
          )}
        </div>
      ))}
      <div className="flex items-center gap-3">
        <Button
          type="button"
          size="xs"
          variant="outline"
          onClick={() => onChange([...rows, { storage_id: "", mount_path: rows.length ? "" : "/models", read_only: true }])}
          data-testid={`${idPrefix}-storage-add`}
        >
          <Plus className="size-3" /> {t("addMount")}
        </Button>
      </div>
      {hint !== undefined && (
        <p className={hint ? "text-xs text-muted-foreground" : "text-xs text-amber-600"} data-testid={`${idPrefix}-model-path-hint`}>
          {hint ? t("modelPathUnder", { name: byId.get(hint.storage_id)?.name ?? "?" }) : t("modelPathOutside")}
        </p>
      )}
    </div>
  );
}
