"use client";

import { useState, useMemo, useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, Loader2, Network, FileCode2 } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import {
  useCreateLlmdStack,
  useArgocdConnections,
  useLlmdStackPreview,
  type CreateLlmdStackBody,
  type PreviewLlmdStackBody,
  type LlmdPreviewManifest,
} from "@/hooks/use-api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

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

export default function NewLlmdStackPage() {
  const t = useTranslations("llmd");
  const router = useRouter();
  const { data: connections } = useArgocdConnections();
  const createMut = useCreateLlmdStack();
  const previewMut = useLlmdStackPreview();
  const [form, setForm] = useState<FormState>(EMPTY);
  const [manifests, setManifests] = useState<LlmdPreviewManifest[]>([]);

  // Live "how this deploys" preview — the ArgoCD Application YAML, debounced.
  const previewBody = useMemo<PreviewLlmdStackBody>(
    () => ({
      name: form.name,
      model_ref: form.model_ref,
      served_model_name: form.served_model_name,
      namespace: form.namespace,
      replicas: form.replicas,
      gpu_count: form.gpu_count,
      gpu_resource_key: form.gpu_resource_key,
    }),
    [form],
  );
  const previewKey = JSON.stringify(previewBody);
  const runPreview = previewMut.mutate;
  useEffect(() => {
    const id = setTimeout(
      () => runPreview(JSON.parse(previewKey), { onSuccess: (r) => setManifests(r.manifests) }),
      400,
    );
    return () => clearTimeout(id);
  }, [previewKey, runPreview]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.name.trim() || !form.model_ref.trim() || !form.served_model_name.trim()) {
      toast.error(t("nameModelRequired"));
      return;
    }
    if (!form.argocd_connection_id) {
      toast.error(t("connectionRequired"));
      return;
    }
    const body: CreateLlmdStackBody = {
      name: form.name,
      model_ref: form.model_ref,
      served_model_name: form.served_model_name,
      argocd_connection_id: form.argocd_connection_id,
      namespace: form.namespace,
      replicas: form.replicas,
      gpu_count: form.gpu_count,
      gpu_resource_key: form.gpu_resource_key,
    };
    createMut.mutate(body, {
      onSuccess: () => {
        toast.success(t("createSuccess"));
        router.push("/admin/llmd");
      },
      onError: (err) => toast.error(err instanceof Error ? err.message : t("saveFailed")),
    });
  };

  return (
    <div className="space-y-6 max-w-6xl">
      <div>
        <Link
          href="/admin/llmd"
          className="text-sm text-muted-foreground hover:text-foreground inline-flex items-center gap-1"
        >
          <ArrowLeft className="size-3.5" />
          {t("backToList")}
        </Link>
        <h1 className="text-2xl font-bold mt-2 flex items-center gap-2">
          <Network className="size-5" />
          {t("pageTitle")}
        </h1>
        <p className="text-muted-foreground mt-1">{t("pageDescription")}</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-6 items-start">
        <form onSubmit={handleSubmit} className="lg:col-span-3">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">{t("addTitle")}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="llmd-name">{t("name")}</Label>
                <Input id="llmd-name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-2">
                  <Label htmlFor="llmd-model">{t("modelRef")}</Label>
                  <Input id="llmd-model" value={form.model_ref} onChange={(e) => setForm({ ...form, model_ref: e.target.value })} />
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
                  value={form.argocd_connection_id}
                  onChange={(e) => setForm({ ...form, argocd_connection_id: e.target.value })}
                  className="w-full h-9 rounded-md border border-input bg-transparent px-3 text-sm"
                >
                  <option value="">{t("connectionPlaceholder")}</option>
                  {(connections ?? []).map((c) => (
                    <option key={c.id} value={c.id}>{c.name}</option>
                  ))}
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
            </CardContent>
          </Card>

          <div className="flex items-center justify-end gap-3 mt-6">
            <Link href="/admin/llmd">
              <Button type="button" variant="outline">{t("cancel")}</Button>
            </Link>
            <Button type="submit" disabled={createMut.isPending}>
              {createMut.isPending && <Loader2 className="size-4 animate-spin" />}
              {t("create")}
            </Button>
          </div>
        </form>

        <aside className="lg:col-span-2">
          <Card>
            <CardHeader>
              <CardTitle className="text-base flex items-center gap-2">
                <FileCode2 className="size-4" />
                {t("previewTitle")}
                {previewMut.isPending && <Loader2 className="size-3.5 animate-spin text-muted-foreground" />}
              </CardTitle>
              <p className="text-xs text-muted-foreground">{t("previewHint")}</p>
            </CardHeader>
            <CardContent className="space-y-3">
              {manifests.length === 0 ? (
                <p className="text-sm text-muted-foreground py-6 text-center">{t("previewEmpty")}</p>
              ) : (
                manifests.map((m, i) => (
                  <div key={i} className="rounded-md border overflow-hidden">
                    <div className="flex items-center gap-2 border-b bg-muted/40 px-3 py-1.5">
                      <span className="text-xs font-semibold">{m.kind}</span>
                      <span className="text-xs font-mono text-muted-foreground truncate">{m.name}</span>
                    </div>
                    <pre className="max-h-[32rem] overflow-auto bg-muted/20 p-3 text-xs leading-relaxed">
                      <code className="font-mono">{m.yaml}</code>
                    </pre>
                  </div>
                ))
              )}
            </CardContent>
          </Card>
        </aside>
      </div>
    </div>
  );
}
