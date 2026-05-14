"use client";

import { useEffect, useState } from "react";
import { Loader2, Pencil, Plus, Trash2, X, Save } from "lucide-react";
import { toast } from "sonner";

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
  const [form, setForm] = useState<SuffixFormState>(entryToForm(initialEntry));
  const setMutation = useSetModelCacheEntry();

  function handleSave() {
    const entry = formToEntry(form);
    if (!entry) {
      toast.error("options이 올바른 JSON 형식이 아닙니다.");
      return;
    }
    setMutation.mutate(
      { modelName, suffix, entry },
      {
        onSuccess: () => {
          toast.success(`${suffix} 캐시가 저장되었습니다.`);
          onDone();
        },
        onError: (err) =>
          toast.error(err instanceof Error ? err.message : "저장 실패"),
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
            placeholder="실제 LiteLLM 모델 식별자"
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
          저장
        </Button>
        <Button size="sm" variant="ghost" onClick={onDone}>
          <X className="size-3" />
          취소
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
  const [editing, setEditing] = useState(false);
  const deleteMutation = useDeleteModelCacheEntry();

  // Auto-open edit mode when adding to a missing suffix
  useEffect(() => {
    if (!entry && editing) return;
  }, [entry, editing]);

  function handleDelete() {
    if (!confirm(`${suffix} 캐시 엔트리를 삭제할까요?`)) return;
    deleteMutation.mutate(
      { modelName, suffix },
      {
        onSuccess: () => toast.success(`${suffix} 캐시가 삭제되었습니다.`),
        onError: (err) => toast.error(err instanceof Error ? err.message : "삭제 실패"),
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
              <span className="text-[10px] text-muted-foreground">등록됨</span>
            ) : (
              <span className="text-[10px] text-muted-foreground">미등록</span>
            )}
          </div>
          {!editing && (
            <div className="flex gap-1">
              <Button
                variant="ghost"
                size="icon-xs"
                onClick={() => setEditing(true)}
                title={entry ? "편집" : "추가"}
              >
                {entry ? <Pencil className="size-3" /> : <Plus className="size-3" />}
              </Button>
              {entry && (
                <Button
                  variant="ghost"
                  size="icon-xs"
                  onClick={handleDelete}
                  disabled={deleteMutation.isPending}
                  title="삭제"
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
        등록된 catalog suffix가 없습니다. 포털 설정에서 먼저 추가하세요.
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
