"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, Loader2, Network, RotateCcw } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import {
  useCreateLlmdStack,
  useArgocdConnections,
  useModelDeployments,
  useLlmdDefaultValues,
  type CreateLlmdStackBody,
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
  values_yaml: string;
};

const EMPTY: FormState = {
  name: "",
  target_model_name: "",
  argocd_connection_id: "",
  namespace: "default",
  values_yaml: "",
};

export default function NewLlmdStackPage() {
  const t = useTranslations("llmd");
  const router = useRouter();
  const { data: connections } = useArgocdConnections();
  const { data: deployments } = useModelDeployments();
  const createMut = useCreateLlmdStack();
  const defaultsMut = useLlmdDefaultValues();
  const [form, setForm] = useState<FormState>(EMPTY);
  // Once the user edits the YAML, stop auto-overwriting it on target change.
  const [valuesTouched, setValuesTouched] = useState(false);

  const loadDefaults = defaultsMut.mutate;
  // Pre-fill / refresh the starter values.yaml from the chosen target model,
  // unless the user has already edited it.
  useEffect(() => {
    if (valuesTouched) return;
    loadDefaults(form.target_model_name, {
      onSuccess: (r) => setForm((f) => ({ ...f, values_yaml: r.values_yaml })),
    });
  }, [form.target_model_name, valuesTouched, loadDefaults]);

  const resetDefaults = () => {
    defaultsMut.mutate(form.target_model_name, {
      onSuccess: (r) => { setForm((f) => ({ ...f, values_yaml: r.values_yaml })); setValuesTouched(false); },
    });
  };

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
    const body: CreateLlmdStackBody = {
      name: form.name,
      target_model_name: form.target_model_name,
      argocd_connection_id: form.argocd_connection_id,
      namespace: form.namespace,
      values_yaml: form.values_yaml,
    };
    createMut.mutate(body, {
      onSuccess: () => { toast.success(t("createSuccess")); router.push("/admin/llmd"); },
      onError: (err) => toast.error(err instanceof Error ? err.message : t("saveFailed")),
    });
  };

  return (
    <div className="space-y-6 max-w-4xl">
      <div>
        <Link href="/admin/llmd" className="text-sm text-muted-foreground hover:text-foreground inline-flex items-center gap-1">
          <ArrowLeft className="size-3.5" />{t("backToList")}
        </Link>
        <h1 className="text-2xl font-bold mt-2 flex items-center gap-2"><Network className="size-5" />{t("pageTitle")}</h1>
        <p className="text-muted-foreground mt-1">{t("pageDescription")}</p>
      </div>

      <form onSubmit={handleSubmit}>
        <Card>
          <CardHeader><CardTitle className="text-base">{t("addTitle")}</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="llmd-name">{t("name")}</Label>
                <Input id="llmd-name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="llmd-ns">{t("namespace")}</Label>
                <Input id="llmd-ns" value={form.namespace} onChange={(e) => setForm({ ...form, namespace: e.target.value })} />
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
            </div>

            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <Label htmlFor="llmd-values">{t("valuesYaml")}</Label>
                <Button type="button" variant="ghost" size="sm" onClick={resetDefaults} disabled={defaultsMut.isPending}>
                  {defaultsMut.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <RotateCcw className="size-3.5" />}
                  {t("resetToDefault")}
                </Button>
              </div>
              <textarea
                id="llmd-values"
                value={form.values_yaml}
                onChange={(e) => { setForm({ ...form, values_yaml: e.target.value }); setValuesTouched(true); }}
                spellCheck={false}
                className="w-full min-h-[22rem] rounded-md border border-input bg-transparent px-3 py-2 font-mono text-xs leading-relaxed"
              />
              <p className="text-xs text-muted-foreground">{t("valuesYamlHint")}</p>
            </div>
          </CardContent>
        </Card>

        <div className="flex items-center justify-end gap-3 mt-6">
          <Link href="/admin/llmd"><Button type="button" variant="outline">{t("cancel")}</Button></Link>
          <Button type="submit" disabled={createMut.isPending}>
            {createMut.isPending && <Loader2 className="size-4 animate-spin" />}{t("create")}
          </Button>
        </div>
      </form>
    </div>
  );
}
