"use client";

import { useState } from "react";
import { Loader2, Plus, Save, Tag, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import { usePodLabels, useUpdatePodLabels, type PodMetadataRules } from "@/hooks/use-api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

type Row = [key: string, value: string];
type Kind = keyof PodMetadataRules;
type Draft = Record<Kind, Row[]>;

const KINDS: Kind[] = ["labels", "annotations"];
// data-testid stem per kind: pod-label-row / pod-annotation-row, …-add
const TEST_ID: Record<Kind, string> = { labels: "pod-label", annotations: "pod-annotation" };

const PLACEHOLDERS = [
  ["{{model_name}}", "placeholderModelName"],
  ["{{namespace}}", "placeholderNamespace"],
  ["{{gpu_type}}", "placeholderGpuType"],
  ["{{metrics_port}}", "placeholderMetricsPort"],
] as const;

/** A row's problem the server would not explain as well: a missing or repeated key. */
function rowErrors(rows: Row[]): ("keyRequired" | "duplicateKey" | null)[] {
  const keys = rows.map(([k]) => k.trim());
  return rows.map(([, value], i) => {
    const key = keys[i];
    if (!key) return value.trim() ? "keyRequired" : null;
    return keys.indexOf(key) !== i ? "duplicateKey" : null;
  });
}

function toRules(rows: Row[]): Record<string, string> {
  return Object.fromEntries(rows.filter(([k, v]) => k.trim() || v.trim()).map(([k, v]) => [k.trim(), v.trim()]));
}

/** One kind's key / value rows with an add button. */
function RuleRows({ kind, rows, onChange }: { kind: Kind; rows: Row[]; onChange: (rows: Row[]) => void }) {
  const t = useTranslations("podLabels");
  const errors = rowErrors(rows);
  const setRow = (i: number, next: Row) => onChange(rows.map((r, j) => (j === i ? next : r)));

  return (
    <section className="space-y-2">
      <div>
        <h4 className="text-sm font-medium">{t(`${kind}Title`)}</h4>
        <p className="text-xs text-muted-foreground">{t(`${kind}Hint`)}</p>
      </div>
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t(`${kind}Empty`)}</p>
      ) : (
        rows.map(([key, value], i) => {
          const err = errors[i];
          return (
            <div key={i} className="space-y-1" data-testid={`${TEST_ID[kind]}-row`}>
              <div className="grid grid-cols-[1fr_1fr_auto] gap-2">
                <Input
                  aria-label={t(`${kind}Key`)}
                  className="font-mono text-xs"
                  placeholder={t(`${kind}KeyPlaceholder`)}
                  value={key}
                  onChange={(e) => setRow(i, [e.target.value, value])}
                  aria-invalid={!!err}
                />
                <Input
                  aria-label={t(`${kind}Value`)}
                  className="font-mono text-xs"
                  placeholder={t(`${kind}ValuePlaceholder`, { example: "{{model_name}}" })}
                  value={value}
                  onChange={(e) => setRow(i, [key, e.target.value])}
                />
                <Button
                  variant="ghost"
                  size="icon"
                  className="text-destructive hover:text-destructive"
                  title={t("remove")}
                  onClick={() => onChange(rows.filter((_, j) => j !== i))}
                >
                  <Trash2 className="size-4" />
                </Button>
              </div>
              {err && <p className="text-xs text-destructive">{t(err)}</p>}
            </div>
          );
        })
      )}
      <Button variant="outline" size="sm" onClick={() => onChange([...rows, ["", ""]])} data-testid={`${TEST_ID[kind]}-add`}>
        <Plus className="size-3.5" />{t(`${kind}Add`)}
      </Button>
    </section>
  );
}

/**
 * Admin card: the label and annotation keys every serving pod gets (regular deployments
 * and both P/D pools), each value fixed text or placeholders the portal fills per
 * deployment. Deployments take them on create and on re-apply from a recipe.
 */
export function PodLabelsCard() {
  const t = useTranslations("podLabels");
  const tc = useTranslations("common");
  const { data, isLoading } = usePodLabels();
  const save = useUpdatePodLabels();
  // null = showing the saved rules; edits start a draft (both kinds) that a save clears.
  const [draft, setDraft] = useState<Draft | null>(null);
  const shown: Draft = draft ?? {
    labels: Object.entries(data?.labels ?? {}),
    annotations: Object.entries(data?.annotations ?? {}),
  };
  const invalid = KINDS.some((kind) => rowErrors(shown[kind]).some(Boolean));

  function submit() {
    save.mutate(
      { labels: toRules(shown.labels), annotations: toRules(shown.annotations) },
      {
        onSuccess: () => { setDraft(null); toast.success(t("saveSuccess")); },
        onError: (e) => toast.error(e instanceof Error ? e.message : t("saveFailed")),
      },
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base flex items-center gap-2"><Tag className="size-4" />{t("title")}</CardTitle>
        <CardDescription>{t("description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
          <span>{t("placeholdersHint")}</span>
          {PLACEHOLDERS.map(([token, label]) => (
            <span key={token}>
              <code className="rounded bg-muted px-1 py-0.5 font-mono text-[11px] text-foreground">{token}</code> {t(label)}
            </span>
          ))}
        </div>

        {isLoading ? (
          <div className="flex justify-center py-4"><Loader2 className="size-5 animate-spin text-muted-foreground" /></div>
        ) : (
          KINDS.map((kind) => (
            <RuleRows key={kind} kind={kind} rows={shown[kind]} onChange={(rows) => setDraft({ ...shown, [kind]: rows })} />
          ))
        )}

        <div className="flex justify-end">
          <Button size="sm" onClick={submit} disabled={draft === null || invalid || save.isPending} data-testid="pod-label-save">
            {save.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <Save className="size-3.5" />}
            {tc("save")}
          </Button>
        </div>
        <p className="rounded-md bg-muted/40 px-3 py-2 text-xs text-muted-foreground">{t("applyNote")}</p>
      </CardContent>
    </Card>
  );
}
