"use client";

import { useState } from "react";
import Link from "next/link";
import { Loader2, Plus, Pencil, Trash2, Network } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import { useLlmdStacks, useUpdateLlmdStack, useDeleteLlmdStack } from "@/hooks/use-api";
import type { LlmdStackSummary } from "@/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";

type EditState = {
  namespace: string;
  replicas: number;
  model_server_type: string;
  target_port: number;
  endpoint_selector: string;
  values_override: string;
};

const MODEL_SERVER_TYPES = ["vllm", "sglang", "triton-tensorrt-llm", "trtllm-serve"];

export default function LlmdPage() {
  const t = useTranslations("llmd");
  const { data: stacks, isLoading } = useLlmdStacks();
  const updateMut = useUpdateLlmdStack();
  const deleteMut = useDeleteLlmdStack();

  const [editing, setEditing] = useState<LlmdStackSummary | null>(null);
  const [form, setForm] = useState<EditState | null>(null);

  const openEdit = (s: LlmdStackSummary) => {
    setEditing(s);
    setForm({
      namespace: s.namespace,
      replicas: s.replicas,
      model_server_type: s.model_server_type,
      target_port: s.target_port,
      endpoint_selector: s.endpoint_selector ?? "",
      values_override: Object.keys(s.values_override ?? {}).length
        ? JSON.stringify(s.values_override, null, 2)
        : "",
    });
  };

  const handleSave = () => {
    if (!editing || !form) return;
    let override: Record<string, unknown> = {};
    try {
      const tx = form.values_override.trim();
      if (tx) {
        const parsed = JSON.parse(tx);
        if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) throw new Error();
        override = parsed as Record<string, unknown>;
      }
    } catch {
      toast.error(t("overrideInvalid"));
      return;
    }
    updateMut.mutate(
      {
        id: editing.id,
        body: {
          namespace: form.namespace,
          replicas: form.replicas,
          model_server_type: form.model_server_type,
          target_port: form.target_port,
          endpoint_selector: form.endpoint_selector || null,
          values_override: override,
        },
      },
      {
        onSuccess: () => { toast.success(t("updateSuccess")); setEditing(null); },
        onError: (e) => toast.error(e instanceof Error ? e.message : t("saveFailed")),
      },
    );
  };

  const handleDelete = (s: LlmdStackSummary) => {
    if (!window.confirm(t("deleteConfirm", { name: s.name }))) return;
    deleteMut.mutate(s.id, {
      onSuccess: () => toast.success(t("deleteSuccess")),
      onError: (e) => toast.error(e instanceof Error ? e.message : t("deleteFailed")),
    });
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-4">
          <div>
            <CardTitle className="text-base flex items-center gap-2"><Network className="size-4" />{t("title")}</CardTitle>
            <CardDescription>{t("description")}</CardDescription>
          </div>
          <Link href="/admin/llmd/new">
            <Button size="sm"><Plus className="size-4" />{t("addButton")}</Button>
          </Link>
        </div>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="flex justify-center py-8"><Loader2 className="size-5 animate-spin text-muted-foreground" /></div>
        ) : stacks && stacks.length > 0 ? (
          <div className="space-y-2">
            {stacks.map((s) => (
              <div key={s.id} className="flex items-center justify-between gap-3 rounded-md border p-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium truncate">{s.name}</span>
                    <Badge variant="secondary">{t("syncLabel")}: {s.sync_status}</Badge>
                    <Badge variant={s.health_status === "Healthy" ? "default" : "secondary"}>
                      {t("healthLabel")}: {s.health_status}
                    </Badge>
                  </div>
                  <div className="text-xs text-muted-foreground mt-0.5 space-x-2 font-mono">
                    <span>→ {s.target_model_name}</span><span>ns: {s.namespace}</span><span>x{s.replicas}</span>
                  </div>
                  {s.status_message && <p className="text-xs text-muted-foreground mt-1">{s.status_message}</p>}
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <Button variant="ghost" size="icon-xs" onClick={() => openEdit(s)}><Pencil className="size-3.5" /></Button>
                  <Button variant="ghost" size="icon-xs" className="text-destructive hover:text-destructive" onClick={() => handleDelete(s)}>
                    <Trash2 className="size-3.5" />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">{t("empty")}</p>
        )}
      </CardContent>

      <Dialog open={!!editing} onOpenChange={(o) => !o && setEditing(null)}>
        <DialogContent className="max-w-lg">
          <DialogHeader><DialogTitle>{t("editTitle")}</DialogTitle></DialogHeader>
          {form && (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-2">
                  <Label htmlFor="llmd-ns">{t("namespace")}</Label>
                  <Input id="llmd-ns" value={form.namespace} onChange={(e) => setForm({ ...form, namespace: e.target.value })} />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="llmd-replicas">{t("replicas")}</Label>
                  <Input id="llmd-replicas" type="number" min={1} value={form.replicas} onChange={(e) => setForm({ ...form, replicas: Number(e.target.value) })} />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-2">
                  <Label htmlFor="llmd-mstype">{t("modelServerType")}</Label>
                  <select
                    id="llmd-mstype"
                    value={form.model_server_type}
                    onChange={(e) => setForm({ ...form, model_server_type: e.target.value })}
                    className="w-full h-9 rounded-md border border-input bg-transparent px-3 text-sm"
                  >
                    {MODEL_SERVER_TYPES.map((m) => <option key={m} value={m}>{m}</option>)}
                  </select>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="llmd-port">{t("targetPort")}</Label>
                  <Input id="llmd-port" type="number" min={1} value={form.target_port} onChange={(e) => setForm({ ...form, target_port: Number(e.target.value) })} />
                </div>
              </div>
              <div className="space-y-2">
                <Label htmlFor="llmd-selector">{t("endpointSelector")}</Label>
                <Input id="llmd-selector" value={form.endpoint_selector} onChange={(e) => setForm({ ...form, endpoint_selector: e.target.value })} placeholder={t("endpointSelectorPlaceholder")} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="llmd-override">{t("valuesOverride")}</Label>
                <textarea
                  id="llmd-override"
                  value={form.values_override}
                  onChange={(e) => setForm({ ...form, values_override: e.target.value })}
                  placeholder={t("valuesOverridePlaceholder")}
                  className="w-full min-h-28 rounded-md border border-input bg-transparent px-3 py-2 font-mono text-xs shadow-xs focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                  spellCheck={false}
                />
              </div>
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditing(null)}>{t("cancel")}</Button>
            <Button onClick={handleSave} disabled={updateMut.isPending}>
              {updateMut.isPending && <Loader2 className="size-4 animate-spin" />}{t("save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
