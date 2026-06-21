"use client";

import { useState } from "react";
import { Loader2, Plus, Pencil, Trash2, Network } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import {
  useLlmdStacks,
  useCreateLlmdStack,
  useUpdateLlmdStack,
  useDeleteLlmdStack,
  useArgocdConnections,
  type CreateLlmdStackBody,
} from "@/hooks/use-api";
import type { LlmdStackSummary } from "@/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";

type FormState = {
  name: string;
  model_ref: string;
  served_model_name: string;
  argocd_connection_id: string;
  namespace: string;
  replicas: number;
  gpu_count: number;
  gpu_resource_key: string;
};

const EMPTY: FormState = {
  name: "",
  model_ref: "",
  served_model_name: "",
  argocd_connection_id: "",
  namespace: "default",
  replicas: 1,
  gpu_count: 1,
  gpu_resource_key: "nvidia.com/gpu",
};

export default function LlmdPage() {
  const t = useTranslations("llmd");
  const { data: stacks, isLoading } = useLlmdStacks();
  const { data: connections } = useArgocdConnections();
  const createMut = useCreateLlmdStack();
  const updateMut = useUpdateLlmdStack();
  const deleteMut = useDeleteLlmdStack();

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<LlmdStackSummary | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY);

  const openCreate = () => { setEditing(null); setForm(EMPTY); setDialogOpen(true); };
  const openEdit = (s: LlmdStackSummary) => {
    setEditing(s);
    setForm({
      name: s.name, model_ref: s.model_ref, served_model_name: s.served_model_name,
      argocd_connection_id: s.argocd_connection_id ?? "", namespace: s.namespace,
      replicas: s.replicas, gpu_count: s.gpu_count, gpu_resource_key: s.gpu_resource_key,
    });
    setDialogOpen(true);
  };

  const handleSave = () => {
    if (!form.name.trim() || !form.model_ref.trim() || !form.served_model_name.trim()) {
      toast.error(t("nameModelRequired"));
      return;
    }
    if (!editing && !form.argocd_connection_id) {
      toast.error(t("connectionRequired"));
      return;
    }
    if (editing) {
      updateMut.mutate(
        { id: editing.id, body: {
          served_model_name: form.served_model_name, namespace: form.namespace,
          replicas: form.replicas, gpu_count: form.gpu_count, gpu_resource_key: form.gpu_resource_key,
        } },
        {
          onSuccess: () => { toast.success(t("updateSuccess")); setDialogOpen(false); },
          onError: (e) => toast.error(e instanceof Error ? e.message : t("saveFailed")),
        },
      );
    } else {
      const body: CreateLlmdStackBody = {
        name: form.name, model_ref: form.model_ref, served_model_name: form.served_model_name,
        argocd_connection_id: form.argocd_connection_id, namespace: form.namespace,
        replicas: form.replicas, gpu_count: form.gpu_count, gpu_resource_key: form.gpu_resource_key,
      };
      createMut.mutate(body, {
        onSuccess: () => { toast.success(t("createSuccess")); setDialogOpen(false); },
        onError: (e) => toast.error(e instanceof Error ? e.message : t("saveFailed")),
      });
    }
  };

  const handleDelete = (s: LlmdStackSummary) => {
    if (!window.confirm(t("deleteConfirm", { name: s.name }))) return;
    deleteMut.mutate(s.id, {
      onSuccess: () => toast.success(t("deleteSuccess")),
      onError: (e) => toast.error(e instanceof Error ? e.message : t("deleteFailed")),
    });
  };

  const saving = createMut.isPending || updateMut.isPending;

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-4">
          <div>
            <CardTitle className="text-base flex items-center gap-2"><Network className="size-4" />{t("title")}</CardTitle>
            <CardDescription>{t("description")}</CardDescription>
          </div>
          <Button size="sm" onClick={openCreate}><Plus className="size-4" />{t("addButton")}</Button>
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

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader><DialogTitle>{editing ? t("editTitle") : t("addTitle")}</DialogTitle></DialogHeader>
          <div className="space-y-4 max-h-[60vh] overflow-y-auto pr-1">
            <div className="space-y-2">
              <Label htmlFor="llmd-name">{t("name")}</Label>
              <Input id="llmd-name" value={form.name} disabled={!!editing} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2">
                <Label htmlFor="llmd-model">{t("modelRef")}</Label>
                <Input id="llmd-model" value={form.model_ref} disabled={!!editing} onChange={(e) => setForm({ ...form, model_ref: e.target.value })} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="llmd-served">{t("servedName")}</Label>
                <Input id="llmd-served" value={form.served_model_name} onChange={(e) => setForm({ ...form, served_model_name: e.target.value })} />
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="llmd-conn">{t("connection")}</Label>
              <select
                id="llmd-conn"
                disabled={!!editing}
                value={form.argocd_connection_id}
                onChange={(e) => setForm({ ...form, argocd_connection_id: e.target.value })}
                className="w-full h-9 rounded-md border border-input bg-transparent px-3 text-sm"
              >
                <option value="">{t("connectionPlaceholder")}</option>
                {(connections ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
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
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>{t("cancel")}</Button>
            <Button onClick={handleSave} disabled={saving}>
              {saving && <Loader2 className="size-4 animate-spin" />}{t("save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
