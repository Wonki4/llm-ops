"use client";

import { useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, Loader2, Pencil, Trash2, Save, X, Network, ChevronRight, AlertTriangle } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";
import { useLocaleTag, parseServerDate } from "@/lib/locale";

import {
  useLlmdStacks,
  useUpdateLlmdStack,
  useDeleteLlmdStack,
  useLlmdStackApplied,
  useLlmdStackResource,
  useArgocdConnections,
} from "@/hooks/use-api";
import type { LlmdAppliedResource } from "@/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

type EditState = { namespace: string; values_yaml: string };

function Field({ label, children, mono }: { label: string; children: React.ReactNode; mono?: boolean }) {
  return (
    <div className="space-y-1">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={`text-sm ${mono ? "font-mono break-all" : ""}`}>{children}</div>
    </div>
  );
}

function YamlBlock({ text }: { text: string }) {
  return (
    <pre className="max-h-96 overflow-auto rounded-md border bg-muted/40 p-3 text-xs font-mono leading-relaxed">
      {text || "—"}
    </pre>
  );
}

function JsonBlock({ value }: { value: unknown }) {
  return (
    <pre className="max-h-80 overflow-auto rounded-md border bg-muted/40 p-3 text-xs font-mono leading-relaxed">
      {JSON.stringify(value ?? {}, null, 2)}
    </pre>
  );
}

/** A deployed-resource row that expands to show the live manifest from ArgoCD. */
function ResourceRow({ stackId, r }: { stackId: string; r: LlmdAppliedResource }) {
  const [open, setOpen] = useState(false);
  const canOpen = !!(r.kind && r.name && r.namespace);
  const ref = open && canOpen
    ? { kind: r.kind!, name: r.name!, namespace: r.namespace!, version: r.version, group: r.group }
    : null;
  const { data, isLoading } = useLlmdStackResource(stackId, ref);

  return (
    <div>
      <button
        type="button"
        disabled={!canOpen}
        onClick={() => setOpen((o) => !o)}
        className={`flex w-full items-center justify-between gap-3 px-3 py-2 text-sm ${canOpen ? "hover:bg-muted/40" : "cursor-default"}`}
      >
        <span className="flex items-center gap-1.5 font-mono truncate">
          {canOpen && <ChevronRight className={`size-3.5 shrink-0 transition-transform ${open ? "rotate-90" : ""}`} />}
          {r.kind}<span className="text-muted-foreground">/{r.name}</span>
        </span>
        <span className="flex items-center gap-1.5 shrink-0">
          {r.status && <Badge variant="secondary">{r.status}</Badge>}
          {r.health && <Badge variant={r.health === "Healthy" ? "default" : "secondary"}>{r.health}</Badge>}
        </span>
      </button>
      {open && (
        <div className="border-t bg-muted/20 p-2">
          {isLoading ? (
            <div className="flex justify-center py-4"><Loader2 className="size-4 animate-spin text-muted-foreground" /></div>
          ) : (
            <YamlBlock text={data?.manifest_yaml ?? ""} />
          )}
        </div>
      )}
    </div>
  );
}

export default function LlmdDetailPage() {
  const t = useTranslations("llmd");
  const localeTag = useLocaleTag();
  const params = useParams();
  const router = useRouter();
  const id = String(params.id);

  const { data: stacks, isLoading } = useLlmdStacks();
  const stack = stacks?.find((s) => s.id === id);
  const { data: applied, isLoading: appliedLoading } = useLlmdStackApplied(id);
  const { data: connections } = useArgocdConnections();
  const updateMut = useUpdateLlmdStack();
  const deleteMut = useDeleteLlmdStack();

  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<EditState | null>(null);

  const startEdit = () => {
    if (!stack) return;
    setForm({ namespace: stack.namespace, values_yaml: stack.values_yaml });
    setEditing(true);
  };

  const handleSave = () => {
    if (!stack || !form) return;
    updateMut.mutate(
      { id: stack.id, body: { namespace: form.namespace, values_yaml: form.values_yaml } },
      {
        onSuccess: () => { toast.success(t("updateSuccess")); setEditing(false); },
        onError: (e) => toast.error(e instanceof Error ? e.message : t("saveFailed")),
      },
    );
  };

  const handleDelete = () => {
    if (!stack) return;
    if (!window.confirm(t("deleteConfirm", { name: stack.name }))) return;
    deleteMut.mutate(stack.id, {
      onSuccess: () => { toast.success(t("deleteSuccess")); router.push("/admin/llmd"); },
      onError: (e) => toast.error(e instanceof Error ? e.message : t("deleteFailed")),
    });
  };

  const fmtDate = (d: string | null) => (d ? parseServerDate(d).toLocaleString(localeTag) : "-");

  if (isLoading) {
    return <div className="flex justify-center py-16"><Loader2 className="size-6 animate-spin text-muted-foreground" /></div>;
  }
  if (!stack) {
    return (
      <div className="space-y-4">
        <Link href="/admin/llmd" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="size-4" />{t("backToList")}
        </Link>
        <p className="text-sm text-muted-foreground">{t("notFound")}</p>
      </div>
    );
  }

  const connName = connections?.find((c) => c.id === stack.argocd_connection_id)?.name ?? stack.argocd_connection_id ?? "-";

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="space-y-3">
        <Link href="/admin/llmd" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="size-4" />{t("backToList")}
        </Link>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Network className="size-5" />
            <h1 className="text-2xl font-bold">{stack.name}</h1>
            <Badge variant="secondary">{t("syncLabel")}: {stack.sync_status}</Badge>
            <Badge variant={stack.health_status === "Healthy" ? "default" : "secondary"}>
              {t("healthLabel")}: {stack.health_status}
            </Badge>
          </div>
          <div className="flex items-center gap-2">
            {!editing && (
              <Button variant="outline" size="sm" onClick={startEdit}><Pencil className="size-3.5" />{t("editButton")}</Button>
            )}
            <Button variant="outline" size="sm" className="text-destructive hover:text-destructive" onClick={handleDelete}>
              <Trash2 className="size-3.5" />{t("deleteButton")}
            </Button>
          </div>
        </div>
        {stack.status_message && <p className="text-sm text-muted-foreground">{stack.status_message}</p>}
      </div>

      {/* Configuration */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="text-base">{t("configSection")}</CardTitle>
          {editing && (
            <div className="flex items-center gap-2">
              <Button variant="ghost" size="sm" onClick={() => setEditing(false)}><X className="size-3.5" />{t("cancel")}</Button>
              <Button size="sm" onClick={handleSave} disabled={updateMut.isPending}>
                {updateMut.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <Save className="size-3.5" />}{t("save")}
              </Button>
            </div>
          )}
        </CardHeader>
        <CardContent className="space-y-5">
          {/* Identity (not editable) */}
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
            <Field label={t("targetModel")} mono>{stack.target_model_name}</Field>
            <Field label={t("connection")}>{connName}</Field>
            <Field label={t("argoAppName")} mono>{stack.argo_app_name}</Field>
            <Field label={t("chart")} mono>{stack.chart_name} {stack.chart_version}</Field>
            <Field label={t("chartRepo")} mono>{stack.chart_repo}</Field>
            <Field label={t("createdBy")}>{stack.created_by ?? "-"}</Field>
            <Field label={t("createdAt")}>{fmtDate(stack.created_at)}</Field>
            <Field label={t("updatedAt")}>{fmtDate(stack.updated_at)}</Field>
          </div>

          <div className="h-px bg-border" />

          {/* namespace + values.yaml */}
          {!editing || !form ? (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
                <Field label={t("namespace")} mono>{stack.namespace}</Field>
              </div>
              <div className="space-y-2">
                <div className="text-xs text-muted-foreground">{t("valuesYaml")}</div>
                <YamlBlock text={stack.values_yaml} />
              </div>
            </div>
          ) : (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                <div className="space-y-2">
                  <Label htmlFor="llmd-ns">{t("namespace")}</Label>
                  <Input id="llmd-ns" value={form.namespace} onChange={(e) => setForm({ ...form, namespace: e.target.value })} />
                </div>
              </div>
              <div className="space-y-2">
                <Label htmlFor="llmd-values">{t("valuesYaml")}</Label>
                <textarea
                  id="llmd-values"
                  value={form.values_yaml}
                  onChange={(e) => setForm({ ...form, values_yaml: e.target.value })}
                  spellCheck={false}
                  className="w-full min-h-[22rem] rounded-md border border-input bg-transparent px-3 py-2 font-mono text-xs leading-relaxed"
                />
                <p className="text-xs text-muted-foreground">{t("valuesYamlHint")}</p>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* How values were applied */}
      <Card>
        <CardHeader><CardTitle className="text-base">{t("appliedSection")}</CardTitle></CardHeader>
        <CardContent className="space-y-5">
          {appliedLoading ? (
            <div className="flex justify-center py-6"><Loader2 className="size-5 animate-spin text-muted-foreground" /></div>
          ) : (
            <>
              {applied?.live_error && (
                <div className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
                  <AlertTriangle className="size-4 shrink-0 text-amber-500 mt-0.5" />
                  <div>
                    <div className="font-medium text-amber-600 dark:text-amber-400">{t("liveErrorTitle")}</div>
                    <div className="text-muted-foreground">{applied.live_error}</div>
                  </div>
                </div>
              )}
              <div className="space-y-2">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">{t("deployedResources")}</span>
                  {applied?.revision && <span className="text-xs text-muted-foreground font-mono">rev: {applied.revision}</span>}
                </div>
                {applied && applied.resources.length > 0 ? (
                  <div className="rounded-md border divide-y">
                    {applied.resources.map((r, i) => (
                      <ResourceRow key={i} stackId={stack.id} r={r} />
                    ))}
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">{applied?.live_error ? t("noResourcesError") : t("noResources")}</p>
                )}
              </div>

              <div className="space-y-2">
                <div className="text-sm font-medium">{t("effectiveValues")}</div>
                <p className="text-xs text-muted-foreground">{t("effectiveValuesHint")}</p>
                <JsonBlock value={applied?.effective_values} />
              </div>

              {applied?.live_values && (
                <div className="space-y-2">
                  <div className="text-sm font-medium">{t("liveValues")}</div>
                  <p className="text-xs text-muted-foreground">{t("liveValuesHint")}</p>
                  <JsonBlock value={applied.live_values} />
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
