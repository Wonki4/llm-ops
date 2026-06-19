"use client";

import { useState } from "react";
import { Loader2, Plus, Pencil, Trash2, Server, CheckCircle2, XCircle, Plug } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import {
  useK8sClusters,
  useCreateK8sCluster,
  useUpdateK8sCluster,
  useDeleteK8sCluster,
  useTestK8sCluster,
  useTestSavedK8sCluster,
  type CreateK8sClusterBody,
} from "@/hooks/use-api";
import type { K8sClusterSummary, ClusterTestResult } from "@/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";

type FormState = {
  name: string;
  context: string;
  namespace: string;
  kubeconfig: string;
  description: string;
  is_default: boolean;
};

const EMPTY: FormState = {
  name: "",
  context: "",
  namespace: "default",
  kubeconfig: "",
  description: "",
  is_default: false,
};

export function ClusterSettingsTab() {
  const t = useTranslations("settings.clusters");
  const { data: clusters, isLoading } = useK8sClusters();
  const createMut = useCreateK8sCluster();
  const updateMut = useUpdateK8sCluster();
  const deleteMut = useDeleteK8sCluster();
  const testMut = useTestK8sCluster();
  const testSavedMut = useTestSavedK8sCluster();

  const [rowTest, setRowTest] = useState<Record<string, ClusterTestResult>>({});
  const [testingId, setTestingId] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<K8sClusterSummary | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY);
  const [testResult, setTestResult] = useState<ClusterTestResult | null>(null);

  const openCreate = () => {
    setEditing(null);
    setForm(EMPTY);
    setTestResult(null);
    setDialogOpen(true);
  };

  const openEdit = (c: K8sClusterSummary) => {
    setEditing(c);
    setForm({
      name: c.name,
      context: c.context,
      namespace: c.namespace,
      kubeconfig: "", // masked — empty keeps existing
      description: c.description ?? "",
      is_default: c.is_default,
    });
    setTestResult(null);
    setDialogOpen(true);
  };

  const handleTest = () => {
    if (!form.kubeconfig.trim() || !form.context.trim()) {
      toast.error(t("testNeedsConfig"));
      return;
    }
    setTestResult(null);
    testMut.mutate(
      { kubeconfig: form.kubeconfig, context: form.context },
      { onSuccess: (r) => setTestResult(r), onError: (e) => setTestResult({ ok: false, server_version: null, message: e instanceof Error ? e.message : "error" }) },
    );
  };

  const handleSave = () => {
    if (!form.name.trim() || !form.context.trim()) {
      toast.error(t("nameContextRequired"));
      return;
    }
    if (!editing && !form.kubeconfig.trim()) {
      toast.error(t("kubeconfigRequired"));
      return;
    }

    if (editing) {
      const body: Record<string, unknown> = {
        name: form.name,
        context: form.context,
        namespace: form.namespace,
        description: form.description,
        is_default: form.is_default,
      };
      if (form.kubeconfig.trim()) body.kubeconfig = form.kubeconfig;
      updateMut.mutate(
        { id: editing.id, body },
        {
          onSuccess: () => {
            toast.success(t("updateSuccess"));
            setDialogOpen(false);
          },
          onError: (e) => toast.error(e instanceof Error ? e.message : t("saveFailed")),
        },
      );
    } else {
      const body: CreateK8sClusterBody = {
        name: form.name,
        context: form.context,
        namespace: form.namespace,
        kubeconfig: form.kubeconfig,
        description: form.description || null,
        is_default: form.is_default,
      };
      createMut.mutate(body, {
        onSuccess: () => {
          toast.success(t("createSuccess"));
          setDialogOpen(false);
        },
        onError: (e) => toast.error(e instanceof Error ? e.message : t("saveFailed")),
      });
    }
  };

  const handleDelete = (c: K8sClusterSummary) => {
    if (!window.confirm(t("deleteConfirm", { name: c.name }))) return;
    deleteMut.mutate(c.id, {
      onSuccess: () => toast.success(t("deleteSuccess")),
      onError: (e) => toast.error(e instanceof Error ? e.message : t("deleteFailed")),
    });
  };

  const handleTestSaved = (c: K8sClusterSummary) => {
    setTestingId(c.id);
    testSavedMut.mutate(c.id, {
      onSuccess: (r) => {
        setRowTest((prev) => ({ ...prev, [c.id]: r }));
        setTestingId(null);
        if (r.ok) toast.success(t("testOk", { version: r.server_version ?? "" }));
        else toast.error(r.message);
      },
      onError: (e) => {
        setTestingId(null);
        const message = e instanceof Error ? e.message : "error";
        setRowTest((prev) => ({ ...prev, [c.id]: { ok: false, server_version: null, message } }));
        toast.error(message);
      },
    });
  };

  const saving = createMut.isPending || updateMut.isPending;

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-4">
          <div>
            <CardTitle className="text-base flex items-center gap-2">
              <Server className="size-4" />
              {t("title")}
            </CardTitle>
            <CardDescription>{t("description")}</CardDescription>
          </div>
          <Button size="sm" onClick={openCreate}>
            <Plus className="size-4" />
            {t("addButton")}
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="flex justify-center py-8">
            <Loader2 className="size-5 animate-spin text-muted-foreground" />
          </div>
        ) : clusters && clusters.length > 0 ? (
          <div className="space-y-2">
            {clusters.map((c) => (
              <div
                key={c.id}
                className="flex items-center justify-between gap-3 rounded-md border p-3"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium truncate">{c.name}</span>
                    {c.is_default && <Badge variant="default">{t("defaultBadge")}</Badge>}
                  </div>
                  <div className="text-xs text-muted-foreground mt-0.5 space-x-2">
                    <span className="font-mono">context: {c.context}</span>
                    <span className="font-mono">ns: {c.namespace}</span>
                    {c.api_server && <span className="font-mono truncate">{c.api_server}</span>}
                  </div>
                  {c.description && (
                    <p className="text-xs text-muted-foreground mt-1">{c.description}</p>
                  )}
                  {rowTest[c.id] && (
                    <p
                      className={`text-xs mt-1 flex items-center gap-1 ${rowTest[c.id].ok ? "text-green-600" : "text-destructive"}`}
                    >
                      {rowTest[c.id].ok ? (
                        <CheckCircle2 className="size-3.5" />
                      ) : (
                        <XCircle className="size-3.5" />
                      )}
                      {rowTest[c.id].ok
                        ? t("testOk", { version: rowTest[c.id].server_version ?? "" })
                        : rowTest[c.id].message}
                    </p>
                  )}
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    title={t("testButton")}
                    disabled={testingId === c.id}
                    onClick={() => handleTestSaved(c)}
                  >
                    {testingId === c.id ? (
                      <Loader2 className="size-3.5 animate-spin" />
                    ) : (
                      <Plug className="size-3.5" />
                    )}
                  </Button>
                  <Button variant="ghost" size="icon-xs" onClick={() => openEdit(c)}>
                    <Pencil className="size-3.5" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    className="text-destructive hover:text-destructive"
                    onClick={() => handleDelete(c)}
                  >
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
          <DialogHeader>
            <DialogTitle>{editing ? t("editTitle") : t("addTitle")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 max-h-[60vh] overflow-y-auto pr-1">
            <div className="space-y-2">
              <Label htmlFor="cluster-name">{t("name")}</Label>
              <Input
                id="cluster-name"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder={t("namePlaceholder")}
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2">
                <Label htmlFor="cluster-context">{t("context")}</Label>
                <Input
                  id="cluster-context"
                  value={form.context}
                  onChange={(e) => setForm({ ...form, context: e.target.value })}
                  placeholder={t("contextPlaceholder")}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="cluster-namespace">{t("namespace")}</Label>
                <Input
                  id="cluster-namespace"
                  value={form.namespace}
                  onChange={(e) => setForm({ ...form, namespace: e.target.value })}
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="cluster-kubeconfig">{t("kubeconfig")}</Label>
              <textarea
                id="cluster-kubeconfig"
                value={form.kubeconfig}
                onChange={(e) => setForm({ ...form, kubeconfig: e.target.value, })}
                placeholder={editing ? t("kubeconfigEditPlaceholder") : t("kubeconfigPlaceholder")}
                className="w-full min-h-32 rounded-md border border-input bg-transparent px-3 py-2 font-mono text-xs shadow-xs focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                spellCheck={false}
              />
              {editing && (
                <p className="text-xs text-muted-foreground">{t("kubeconfigEditHint")}</p>
              )}
            </div>
            <div className="space-y-2">
              <Label htmlFor="cluster-desc">{t("descriptionLabel")}</Label>
              <Input
                id="cluster-desc"
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
                placeholder={t("descriptionPlaceholder")}
              />
            </div>
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input
                type="checkbox"
                checked={form.is_default}
                onChange={(e) => setForm({ ...form, is_default: e.target.checked })}
                className="size-4"
              />
              {t("isDefault")}
            </label>

            <div className="flex items-center gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={handleTest}
                disabled={testMut.isPending}
              >
                {testMut.isPending ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Plug className="size-4" />
                )}
                {t("testButton")}
              </Button>
              {testResult && (
                <span
                  className={`text-xs flex items-center gap-1 ${testResult.ok ? "text-green-600" : "text-destructive"}`}
                >
                  {testResult.ok ? (
                    <CheckCircle2 className="size-3.5" />
                  ) : (
                    <XCircle className="size-3.5" />
                  )}
                  {testResult.ok
                    ? t("testOk", { version: testResult.server_version ?? "" })
                    : testResult.message}
                </span>
              )}
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>
              {t("cancel")}
            </Button>
            <Button onClick={handleSave} disabled={saving}>
              {saving && <Loader2 className="size-4 animate-spin" />}
              {t("save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
