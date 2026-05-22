"use client";

import { useEffect, useState } from "react";
import { Loader2, Pencil, Plus, Trash2, X, Save } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import {
  useCatalogList,
  useModelCache,
  useSetModelCacheEntry,
  useDeleteModelCacheEntry,
  type ModelCacheEntry,
} from "@/hooks/use-api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent } from "@/components/ui/card";

interface SuffixFormState {
  model: string;
  apiBase: string;
  apiKey: string;
  options: string;
}

const EMPTY_FORM: SuffixFormState = {
  model: "",
  apiBase: "",
  apiKey: "",
  options: "{}",
};

function entryToForm(entry: ModelCacheEntry | null | undefined): SuffixFormState {
  if (!entry) return EMPTY_FORM;
  return {
    model: entry.model ?? "",
    apiBase: entry.apiBase ?? "",
    apiKey: entry.apiKey ?? "",
    options: entry.options ? JSON.stringify(entry.options, null, 2) : "{}",
  };
}

function formToEntry(form: SuffixFormState): ModelCacheEntry | null {
  let options: Record<string, unknown> = {};
  try {
    options = form.options.trim() ? JSON.parse(form.options) : {};
  } catch {
    return null;
  }
  return {
    model: form.model.trim(),
    apiBase: form.apiBase.trim(),
    apiKey: form.apiKey.trim(),
    options,
  };
}

function SuffixCacheForm({
  modelName,
  suffix,
  initialEntry,
  onDone,
}: {
  modelName: string;
  suffix: string;
  initialEntry: ModelCacheEntry | null;
  onDone: () => void;
}) {
  const t = useTranslations("modelCache");
  const tc = useTranslations("common");
  const [form, setForm] = useState<SuffixFormState>(entryToForm(initialEntry));
  const setMutation = useSetModelCacheEntry();

  function handleSave() {
    const entry = formToEntry(form);
    if (!entry) {
      toast.error(t("form.invalidJson"));
      return;
    }
    setMutation.mutate(
      { modelName, suffix, entry },
      {
        onSuccess: () => {
          toast.success(t("toast.saveSuccess", { suffix }));
          onDone();
        },
        onError: (err) =>
          toast.error(err instanceof Error ? err.message : t("toast.saveFailed")),
      },
    );
  }

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="text-xs text-muted-foreground">model</label>
          <Input
            value={form.model}
            onChange={(e) => setForm((f) => ({ ...f, model: e.target.value }))}
            placeholder={t("form.modelPlaceholder")}
            className="h-8 text-sm"
          />
        </div>
        <div>
          <label className="text-xs text-muted-foreground">apiBase</label>
          <Input
            value={form.apiBase}
            onChange={(e) => setForm((f) => ({ ...f, apiBase: e.target.value }))}
            placeholder="https://..."
            className="h-8 text-sm"
          />
        </div>
      </div>
      <div>
        <label className="text-xs text-muted-foreground">apiKey</label>
        <Input
          value={form.apiKey}
          onChange={(e) => setForm((f) => ({ ...f, apiKey: e.target.value }))}
          className="h-8 text-sm"
        />
      </div>
      <div>
        <label className="text-xs text-muted-foreground">options (JSON)</label>
        <textarea
          value={form.options}
          onChange={(e) => setForm((f) => ({ ...f, options: e.target.value }))}
          rows={3}
          className="w-full rounded-md border border-input bg-transparent px-2 py-1 font-mono text-xs"
        />
      </div>
      <div className="flex gap-1">
        <Button size="sm" onClick={handleSave} disabled={setMutation.isPending}>
          {setMutation.isPending ? <Loader2 className="size-3 animate-spin" /> : <Save className="size-3" />}
          {tc("save")}
        </Button>
        <Button size="sm" variant="ghost" onClick={onDone}>
          <X className="size-3" />
          {tc("cancel")}
        </Button>
      </div>
    </div>
  );
}

function SuffixCacheRow({
  modelName,
  suffix,
  entry,
}: {
  modelName: string;
  suffix: string;
  entry: ModelCacheEntry | null;
}) {
  const t = useTranslations("modelCache");
  const tc = useTranslations("common");
  const [editing, setEditing] = useState(false);
  const deleteMutation = useDeleteModelCacheEntry();

  // Auto-open edit mode when adding to a missing suffix
  useEffect(() => {
    if (!entry && editing) return;
  }, [entry, editing]);

  function handleDelete() {
    if (!confirm(t("confirm.delete", { suffix }))) return;
    deleteMutation.mutate(
      { modelName, suffix },
      {
        onSuccess: () => toast.success(t("toast.deleteSuccess", { suffix })),
        onError: (err) => toast.error(err instanceof Error ? err.message : t("toast.deleteFailed")),
      },
    );
  }

  return (
    <Card>
      <CardContent className="pt-3 pb-3 space-y-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="font-mono text-xs font-semibold uppercase">{suffix}</span>
            {entry ? (
              <span className="text-[10px] text-muted-foreground">{t("registered")}</span>
            ) : (
              <span className="text-[10px] text-muted-foreground">{t("unregistered")}</span>
            )}
          </div>
          {!editing && (
            <div className="flex gap-1">
              <Button
                variant="ghost"
                size="icon-xs"
                onClick={() => setEditing(true)}
                title={entry ? tc("edit") : t("add")}
              >
                {entry ? <Pencil className="size-3" /> : <Plus className="size-3" />}
              </Button>
              {entry && (
                <Button
                  variant="ghost"
                  size="icon-xs"
                  onClick={handleDelete}
                  disabled={deleteMutation.isPending}
                  title={tc("delete")}
                >
                  <Trash2 className="size-3 text-destructive" />
                </Button>
              )}
            </div>
          )}
        </div>

        {editing ? (
          <SuffixCacheForm
            modelName={modelName}
            suffix={suffix}
            initialEntry={entry}
            onDone={() => setEditing(false)}
          />
        ) : entry ? (
          <div className="space-y-0.5 text-xs">
            <div className="flex gap-1.5">
              <span className="text-muted-foreground w-16">model</span>
              <span className="font-mono break-all">{entry.model || "-"}</span>
            </div>
            <div className="flex gap-1.5">
              <span className="text-muted-foreground w-16">apiBase</span>
              <span className="font-mono break-all">{entry.apiBase || "-"}</span>
            </div>
            <div className="flex gap-1.5">
              <span className="text-muted-foreground w-16">apiKey</span>
              <span className="font-mono">{entry.apiKey ? "********" : "-"}</span>
            </div>
            {entry.options && Object.keys(entry.options).length > 0 && (
              <details className="text-[11px]">
                <summary className="cursor-pointer text-muted-foreground">options</summary>
                <pre className="mt-1 rounded bg-muted p-1.5 overflow-auto">
                  {JSON.stringify(entry.options, null, 2)}
                </pre>
              </details>
            )}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

export function ModelCacheSection({ modelName }: { modelName: string }) {
  const t = useTranslations("modelCache");
  const { data: catalogsData } = useCatalogList();
  const { data: cacheData, isLoading } = useModelCache(modelName);

  const suffixes: string[] = catalogsData?.catalogs ?? [];

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-3">
        <Loader2 className="size-4 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (suffixes.length === 0) {
    return (
      <div className="text-xs text-muted-foreground">
        {t("noSuffixes")}
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {suffixes.map((suffix) => (
        <SuffixCacheRow
          key={suffix}
          modelName={modelName}
          suffix={suffix}
          entry={cacheData?.entries[suffix] ?? null}
        />
      ))}
    </div>
  );
}
