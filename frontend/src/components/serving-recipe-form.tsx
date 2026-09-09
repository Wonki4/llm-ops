"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, Loader2, ScrollText } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import { useCreateServingRecipe, useUpdateServingRecipe } from "@/hooks/use-api";
import type { ServingRecipe, ServingRecipeInput } from "@/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export const RECIPES_HREF = "/admin/recipes";

const BLANK: ServingRecipeInput = {
  name: "", description: null, model_path: "", image: "", gpu_count: 1,
  gpu_resource_key: "nvidia.com/gpu", cpu_request: null, cpu_limit: null,
  memory_request: null, memory_limit: null, node_selector: null, tolerations: null,
  pvc_name: null, pvc_mount_path: null, vllm_extra_args: null, env: null,
  engine: "vllm", engine_args: null,
};

const linesToList = (s: string): string[] | null => {
  const v = s.split("\n").map((x) => x.trim()).filter(Boolean);
  return v.length ? v : null;
};
const listToLines = (v: string[] | null): string => (v ?? []).join("\n");
const linesToMap = (s: string): Record<string, string> | null => {
  const out: Record<string, string> = {};
  for (const line of s.split("\n")) {
    const i = line.indexOf("=");
    if (i > 0) out[line.slice(0, i).trim()] = line.slice(i + 1).trim();
  }
  return Object.keys(out).length ? out : null;
};
const mapToLines = (m: Record<string, string> | null): string =>
  Object.entries(m ?? {}).map(([k, v]) => `${k}=${v}`).join("\n");

function toInput(r: ServingRecipe): ServingRecipeInput {
  const { id, created_by, updated_by, created_at, updated_at, ...rest } = r;
  void id; void created_by; void updated_by; void created_at; void updated_at;
  return rest;
}

type RequiredText = "name" | "image" | "model_path" | "gpu_resource_key";
type OptionalText =
  | "description" | "cpu_request" | "cpu_limit" | "memory_request" | "memory_limit"
  | "pvc_name" | "pvc_mount_path";

/** Page chrome shared by the create and edit routes. */
export function RecipePageHeader({ title, description }: { title: string; description?: string }) {
  const t = useTranslations("servingRecipes");
  return (
    <div>
      <Link href={RECIPES_HREF} className="text-sm text-muted-foreground hover:text-foreground inline-flex items-center gap-1">
        <ArrowLeft className="size-3.5" />{t("backToList")}
      </Link>
      <h1 className="text-2xl font-bold mt-2 flex items-center gap-2"><ScrollText className="size-5" />{title}</h1>
      {description && <p className="text-muted-foreground mt-1">{description}</p>}
    </div>
  );
}

/**
 * Full-page serving recipe form. Creates when `recipe` is absent, updates
 * otherwise. Navigates back to the list on success.
 */
export function ServingRecipeForm({ recipe }: { recipe?: ServingRecipe }) {
  const t = useTranslations("servingRecipes");
  const tc = useTranslations("common");
  const router = useRouter();
  const createMut = useCreateServingRecipe();
  const updateMut = useUpdateServingRecipe();

  const [form, setForm] = useState<ServingRecipeInput>(() => (recipe ? toInput(recipe) : BLANK));
  const [argsText, setArgsText] = useState(() => listToLines(recipe?.vllm_extra_args ?? null));
  const [envText, setEnvText] = useState(() => mapToLines(recipe?.env ?? null));
  const [nsText, setNsText] = useState(() => mapToLines(recipe?.node_selector ?? null));

  const saving = createMut.isPending || updateMut.isPending;

  const text = (k: RequiredText) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));
  const optText = (k: OptionalText) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.value || null }));

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!form.name.trim() || !form.model_path.trim() || !form.image.trim()) {
      toast.error(t("requiredError"));
      return;
    }
    const body: ServingRecipeInput = {
      ...form,
      vllm_extra_args: linesToList(argsText),
      env: linesToMap(envText),
      node_selector: linesToMap(nsText),
    };
    const opts = {
      onSuccess: () => {
        toast.success(recipe ? t("saveSuccess") : t("createSuccess"));
        router.push(RECIPES_HREF);
      },
      onError: (err: unknown) => toast.error(err instanceof Error ? err.message : t("saveError")),
    };
    if (recipe) updateMut.mutate({ id: recipe.id, body }, opts);
    else createMut.mutate(body, opts);
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <Card>
        <CardHeader><CardTitle className="text-base">{t("sectionBasic")}</CardTitle></CardHeader>
        <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field id="recipe-name" label={t("name")} required>
            <Input id="recipe-name" value={form.name} onChange={text("name")} />
          </Field>
          <Field id="recipe-image" label={t("image")} required>
            <Input id="recipe-image" className="font-mono" value={form.image} onChange={text("image")} />
          </Field>
          <Field id="recipe-model-path" label={t("modelPath")} required span2>
            <Input id="recipe-model-path" className="font-mono" value={form.model_path} onChange={text("model_path")} />
          </Field>
          <Field id="recipe-description" label={t("description")} span2>
            <Input id="recipe-description" value={form.description ?? ""} onChange={optText("description")} />
          </Field>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">{t("sectionResources")}</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field id="recipe-gpu-count" label={t("gpuCount")}>
              <Input
                id="recipe-gpu-count" type="number" min={0} value={form.gpu_count}
                onChange={(e) => setForm((f) => ({ ...f, gpu_count: Number(e.target.value) }))}
              />
            </Field>
            <Field id="recipe-gpu-key" label={t("gpuResourceKey")}>
              <Input id="recipe-gpu-key" className="font-mono" value={form.gpu_resource_key} onChange={text("gpu_resource_key")} />
            </Field>
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Field id="recipe-cpu-req" label={t("cpuRequest")}>
              <Input id="recipe-cpu-req" placeholder="4" value={form.cpu_request ?? ""} onChange={optText("cpu_request")} />
            </Field>
            <Field id="recipe-cpu-limit" label={t("cpuLimit")}>
              <Input id="recipe-cpu-limit" placeholder="8" value={form.cpu_limit ?? ""} onChange={optText("cpu_limit")} />
            </Field>
            <Field id="recipe-mem-req" label={t("memoryRequest")}>
              <Input id="recipe-mem-req" placeholder="32Gi" value={form.memory_request ?? ""} onChange={optText("memory_request")} />
            </Field>
            <Field id="recipe-mem-limit" label={t("memoryLimit")}>
              <Input id="recipe-mem-limit" placeholder="64Gi" value={form.memory_limit ?? ""} onChange={optText("memory_limit")} />
            </Field>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">{t("sectionStorage")}</CardTitle></CardHeader>
        <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field id="recipe-pvc-name" label={t("pvcName")}>
            <Input id="recipe-pvc-name" className="font-mono" value={form.pvc_name ?? ""} onChange={optText("pvc_name")} />
          </Field>
          <Field id="recipe-pvc-mount" label={t("pvcMountPath")}>
            <Input id="recipe-pvc-mount" className="font-mono" placeholder="/models" value={form.pvc_mount_path ?? ""} onChange={optText("pvc_mount_path")} />
          </Field>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">{t("sectionAdvanced")}</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <Field id="recipe-args" label={t("vllmArgs")}>
            <Area id="recipe-args" value={argsText} onChange={setArgsText} placeholder={"--max-model-len=8192\n--tensor-parallel-size=2"} />
          </Field>
          <Field id="recipe-env" label={t("env")}>
            <Area id="recipe-env" value={envText} onChange={setEnvText} placeholder={"HF_HOME=/models/.cache\nVLLM_LOGGING_LEVEL=INFO"} />
          </Field>
          <Field id="recipe-node-selector" label={t("nodeSelector")}>
            <Area id="recipe-node-selector" value={nsText} onChange={setNsText} placeholder="nvidia.com/gpu.product=NVIDIA-A100-SXM4-80GB" />
          </Field>
        </CardContent>
      </Card>

      <div className="flex items-center justify-end gap-3">
        <Button asChild type="button" variant="outline" disabled={saving}>
          <Link href={RECIPES_HREF}>{tc("cancel")}</Link>
        </Button>
        <Button type="submit" disabled={saving}>
          {saving && <Loader2 className="size-4 animate-spin" />}
          {recipe ? t("save") : t("create")}
        </Button>
      </div>
    </form>
  );
}

function Field({
  id, label, required, span2, children,
}: { id: string; label: string; required?: boolean; span2?: boolean; children: React.ReactNode }) {
  return (
    <div className={span2 ? "space-y-2 sm:col-span-2" : "space-y-2"}>
      <Label htmlFor={id}>
        {label}
        {required && <span className="text-destructive"> *</span>}
      </Label>
      {children}
    </div>
  );
}

function Area({
  id, value, onChange, placeholder,
}: { id: string; value: string; onChange: (v: string) => void; placeholder?: string }) {
  return (
    <textarea
      id={id}
      rows={6}
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
      className="w-full rounded-md border border-input bg-transparent px-3 py-2 font-mono text-sm leading-relaxed placeholder:text-muted-foreground/50 focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
    />
  );
}
