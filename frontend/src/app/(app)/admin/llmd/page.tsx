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
  served_model_name: string;
  namespace: string;
  replicas: number;
  gpu_count: number;
  gpu_resource_key: string;
};

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
      served_model_name: s.served_model_name,
      namespace: s.namespace,
      replicas: s.replicas,
      gpu_count: s.gpu_count,
      gpu_resource_key: s.gpu_resource_key,
    });
  };

  const handleSave = () => {
    if (!editing || !form) return;
    updateMut.mutate(
      { id: editing.id, body: form },
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
                    <span>{s.model_ref}</span><span>ns: {s.namespace}</span><span>x{s.replicas}</span>
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
              <div className="space-y-2">
                <Label htmlFor="llmd-served">{t("servedName")}</Label>
                <Input id="llmd-served" value={form.served_model_name} onChange={(e) => setForm({ ...form, served_model_name: e.target.value })} />
              </div>
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
                  <Label htmlFor="llmd-gpu">{t("gpuCount")}</Label>
                  <Input id="llmd-gpu" type="number" min={0} value={form.gpu_count} onChange={(e) => setForm({ ...form, gpu_count: Number(e.target.value) })} />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="llmd-gpukey">{t("gpuResourceKey")}</Label>
                  <Input id="llmd-gpukey" value={form.gpu_resource_key} onChange={(e) => setForm({ ...form, gpu_resource_key: e.target.value })} />
                </div>
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
