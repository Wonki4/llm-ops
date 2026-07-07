"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, Loader2, Network, RotateCcw } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import {
  useCreateLlmdStack,
  useK8sClusters,
  useModelDeployments,
  useLlmdDefaultValues,
  useExternalServings,
  type CreateLlmdStackBody,
  type ExternalServing,
} from "@/hooks/use-api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

type FormState = {
  name: string;
  target_model_name: string;
  target_kind: "portal" | "external";
  endpoint_selector: string;
  cluster_id: string;
  namespace: string;
  values_yaml: string;
};

const EMPTY: FormState = {
  name: "",
  target_model_name: "",
  target_kind: "portal",
  endpoint_selector: "",
  cluster_id: "",
  namespace: "default",
  values_yaml: "",
};

export default function NewLlmdStackPage() {
  const t = useTranslations("llmd");
  const router = useRouter();
  const { data: clusters } = useK8sClusters();
  const { data: deployments } = useModelDeployments();
  const { data: external } = useExternalServings();
  const servings = external?.servings ?? [];
  const [selectedExternal, setSelectedExternal] = useState<ExternalServing | null>(null);
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
    loadDefaults(
      { target_model_name: form.target_model_name, endpoint_selector: form.endpoint_selector || undefined },
      { onSuccess: (r) => setForm((f) => ({ ...f, values_yaml: r.values_yaml })) },
    );
  }, [form.target_model_name, form.endpoint_selector, valuesTouched, loadDefaults]);

  const resetDefaults = () => {
    defaultsMut.mutate(
      { target_model_name: form.target_model_name, endpoint_selector: form.endpoint_selector || undefined },
      { onSuccess: (r) => { setForm((f) => ({ ...f, values_yaml: r.values_yaml })); setValuesTouched(false); } },
    );
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.name.trim() || !form.target_model_name) {
      toast.error(t("nameModelRequired"));
      return;
    }
    const body: CreateLlmdStackBody = {
      name: form.name,
      target_model_name: form.target_model_name,
      cluster_id: form.cluster_id || null,
      namespace: form.namespace,
      values_yaml: form.values_yaml,
    };
    createMut.mutate(body, {
      onSuccess: () => { toast.success(t("createSuccess")); router.push("/admin/llmd"); },
      onError: (err) => toast.error(err instanceof Error ? err.message : t("saveFailed")),
    });
  };

  const externalKey = (s: ExternalServing) => `ext::${s.cluster_id ?? ""}::${s.namespace}::${s.deployment_name}`;
  const targetSelectValue =
    form.target_kind === "external" && selectedExternal ? externalKey(selectedExternal) : form.target_model_name;

  const onTargetChange = (value: string) => {
    if (value.startsWith("ext::")) {
      const serving = servings.find((s) => externalKey(s) === value);
      if (!serving) return;
      const labels = Object.entries(serving.labels);
      const preferred =
        labels.find(([k]) => k === "app") ?? labels.find(([k]) => k === "app.kubernetes.io/name") ?? labels[0];
      setSelectedExternal(serving);
      setForm((f) => ({
        ...f,
        target_model_name: serving.registration?.model_name || serving.deployment_name,
        target_kind: "external",
        endpoint_selector: preferred ? `${preferred[0]}=${preferred[1]}` : "",
        namespace: serving.namespace,
        cluster_id: serving.cluster_id ?? "",
      }));
    } else {
      setSelectedExternal(null);
      setForm((f) => ({ ...f, target_model_name: value, target_kind: "portal", endpoint_selector: "" }));
    }
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
                  value={targetSelectValue}
                  onChange={(e) => onTargetChange(e.target.value)}
                  className="w-full h-9 rounded-md border border-input bg-transparent px-3 text-sm"
                >
                  <option value="">{t("targetModelPlaceholder")}</option>
                  <optgroup label={t("targetGroupPortal")}>
                    {(deployments ?? []).map((d) => (
                      <option key={d.id} value={d.model_name}>{d.model_name}</option>
                    ))}
                  </optgroup>
                  <optgroup label={t("targetGroupExternal")}>
                    {servings.map((s) => (
                      <option key={externalKey(s)} value={externalKey(s)}>
                        {s.deployment_name} ({s.engine} · {s.namespace})
                      </option>
                    ))}
                  </optgroup>
                </select>
                <p className="text-xs text-muted-foreground">{t("targetModelHint")}</p>
              </div>
              {form.target_kind === "external" && selectedExternal && (
                <div className="space-y-2">
                  <Label htmlFor="llmd-endpoint-label">{t("endpointLabelLabel")}</Label>
                  {Object.keys(selectedExternal.labels).length === 0 ? (
                    <p className="text-xs text-muted-foreground">{t("endpointLabelNone")}</p>
                  ) : (
                    <select
                      id="llmd-endpoint-label"
                      value={form.endpoint_selector}
                      onChange={(e) => setForm({ ...form, endpoint_selector: e.target.value })}
                      className="w-full h-9 rounded-md border border-input bg-transparent px-3 text-sm"
                    >
                      {Object.entries(selectedExternal.labels).map(([k, v]) => (
                        <option key={`${k}=${v}`} value={`${k}=${v}`}>{k}={v}</option>
                      ))}
                    </select>
                  )}
                  <p className="text-xs text-muted-foreground">{t("endpointLabelHint")}</p>
                </div>
              )}
              <div className="space-y-2">
                <Label htmlFor="llmd-cluster">{t("clusterLabel")}</Label>
                <select
                  id="llmd-cluster"
                  value={form.cluster_id}
                  onChange={(e) => setForm({ ...form, cluster_id: e.target.value })}
                  className="w-full h-9 rounded-md border border-input bg-transparent px-3 text-sm"
                >
                  <option value="">{t("clusterDefault")}</option>
                  {(clusters ?? []).map((c) => (
                    <option key={c.id} value={c.id}>{c.name}{c.is_default ? " ★" : ""}</option>
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
