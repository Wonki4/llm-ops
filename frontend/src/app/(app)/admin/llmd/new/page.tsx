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
  useModelDeployments,
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
  target_model_name: string;
  argocd_connection_id: string;
  namespace: string;
  replicas: number;
  model_server_type: string;
  target_port: number;
  endpoint_selector: string;
  values_override: string; // raw JSON, deep-merged into the generated values
};

const EMPTY: FormState = {
  name: "",
  target_model_name: "",
  argocd_connection_id: "",
  namespace: "default",
  replicas: 1,
  model_server_type: "vllm",
  target_port: 8000,
  endpoint_selector: "",
  values_override: "",
};

const MODEL_SERVER_TYPES = ["vllm", "sglang", "triton-tensorrt-llm", "trtllm-serve"];

function parseOverride(text: string): Record<string, unknown> {
  const t = text.trim();
  if (!t) return {};
  const parsed = JSON.parse(t);
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new Error("must be a JSON object");
  }
  return parsed as Record<string, unknown>;
}

export default function NewLlmdStackPage() {
  const t = useTranslations("llmd");
  const router = useRouter();
  const { data: connections } = useArgocdConnections();
  const { data: deployments } = useModelDeployments();
  const createMut = useCreateLlmdStack();
  const previewMut = useLlmdStackPreview();
  const [form, setForm] = useState<FormState>(EMPTY);
  const [manifests, setManifests] = useState<LlmdPreviewManifest[]>([]);

  const previewBody = useMemo<PreviewLlmdStackBody>(() => {
    let override: Record<string, unknown> = {};
    try {
      override = parseOverride(form.values_override);
    } catch {
      /* keep last valid preview while the JSON is mid-edit */
    }
    return {
      name: form.name,
      target_model_name: form.target_model_name,
      namespace: form.namespace,
      replicas: form.replicas,
      model_server_type: form.model_server_type,
      target_port: form.target_port,
      endpoint_selector: form.endpoint_selector || null,
      values_override: override,
    };
  }, [form]);
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
    if (!form.name.trim() || !form.target_model_name) {
      toast.error(t("nameModelRequired"));
      return;
    }
    if (!form.argocd_connection_id) {
      toast.error(t("connectionRequired"));
      return;
    }
    let override: Record<string, unknown>;
    try {
      override = parseOverride(form.values_override);
    } catch {
      toast.error(t("overrideInvalid"));
      return;
    }
    const body: CreateLlmdStackBody = {
      name: form.name,
      target_model_name: form.target_model_name,
      argocd_connection_id: form.argocd_connection_id,
      namespace: form.namespace,
      replicas: form.replicas,
      model_server_type: form.model_server_type,
      target_port: form.target_port,
      endpoint_selector: form.endpoint_selector || null,
      values_override: override,
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
              <div className="space-y-2">
                <Label htmlFor="llmd-model">{t("targetModel")}</Label>
                <select
                  id="llmd-model"
                  value={form.target_model_name}
                  onChange={(e) => setForm({ ...form, target_model_name: e.target.value })}
                  className="w-full h-9 rounded-md border border-input bg-transparent px-3 text-sm"
                >
                  <option value="">{t("targetModelPlaceholder")}</option>
                  {(deployments ?? []).map((d) => (
                    <option key={d.id} value={d.model_name}>{d.model_name}</option>
                  ))}
                </select>
                <p className="text-xs text-muted-foreground">{t("targetModelHint")}</p>
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
                <Input
                  id="llmd-selector"
                  value={form.endpoint_selector}
                  onChange={(e) => setForm({ ...form, endpoint_selector: e.target.value })}
                  placeholder={t("endpointSelectorPlaceholder")}
                />
                <p className="text-xs text-muted-foreground">{t("endpointSelectorHint")}</p>
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
                <p className="text-xs text-muted-foreground">{t("valuesOverrideHint")}</p>
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
