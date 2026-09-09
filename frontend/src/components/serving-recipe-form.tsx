"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, Loader2, ScrollText } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import { useCreateServingRecipe, useUpdateServingRecipe } from "@/hooks/use-api";
import type { EngineArgs, ServingEngine, ServingRecipe, ServingRecipeInput } from "@/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  ENGINES, ENGINE_ARG_FIELDS, ENGINE_DEFAULT_IMAGE, ENGINE_LABEL_KEY, argLabelKey, type EngineArgField,
} from "@/lib/serving-engines";

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

  function switchEngine(next: ServingEngine) {
    setForm((f) => {
      if (f.engine === next) return f;
      const otherDefault = ENGINE_DEFAULT_IMAGE[f.engine];
      const image = !f.image.trim() || f.image === otherDefault ? ENGINE_DEFAULT_IMAGE[next] : f.image;
      // Structured args are engine-specific; free-text extra args are the user's and stay.
      return { ...f, engine: next, engine_args: null, image };
    });
  }

  function setArg(key: string, value: string | number | boolean | undefined) {
    setForm((f) => {
      const next: EngineArgs = { ...(f.engine_args ?? {}) };
      if (value === undefined || value === "" || value === false) delete next[key];
      else next[key] = value;
      return { ...f, engine_args: Object.keys(next).length ? next : null };
    });
  }

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
          <Field id="recipe-engine" label={t("engine")} span2>
            <div id="recipe-engine" role="radiogroup" className="inline-flex rounded-md border p-0.5">
              {ENGINES.map((e) => (
                <button
                  key={e}
                  type="button"
                  role="radio"
                  aria-checked={form.engine === e}
                  onClick={() => switchEngine(e)}
                  className={
                    "rounded px-3 py-1 text-sm transition-colors " +
                    (form.engine === e ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground")
                  }
                >
                  {t(ENGINE_LABEL_KEY[e])}
                </button>
              ))}
            </div>
          </Field>
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
        <CardHeader>
          <CardTitle className="text-base">{t("sectionEngineArgs")}</CardTitle>
          <p className="text-xs text-muted-foreground">{t("engineArgsHint")}</p>
        </CardHeader>
        <CardContent className="space-y-4">
          <EngineArgsFields engine={form.engine} values={form.engine_args} onChange={setArg} />
          <Field id="recipe-args" label={t("extraArgs")}>
            <Area
              id="recipe-args" value={argsText} onChange={setArgsText}
              placeholder={form.engine === "sglang" ? "--log-level=info\n--schedule-policy=lpm" : "--max-model-len=8192\n--tensor-parallel-size=2"}
            />
          </Field>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">{t("sectionAdvanced")}</CardTitle></CardHeader>
        <CardContent className="space-y-4">
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

function EngineArgsFields({
  engine, values, onChange,
}: { engine: ServingEngine; values: EngineArgs | null; onChange: (key: string, value: string | number | boolean | undefined) => void }) {
  const t = useTranslations("servingRecipes");
  const fields = ENGINE_ARG_FIELDS[engine];
  const scalar = fields.filter((f) => f.type !== "bool");
  const bools = fields.filter((f) => f.type === "bool");
  const get = (key: string) => values?.[key];
  const num = (key: string): number | "" => {
    const v = get(key);
    return typeof v === "number" ? v : "";
  };

  const inputFor = (f: EngineArgField) => {
    const id = `recipe-arg-${f.key}`;
    switch (f.type) {
      case "int":
        return (
          <Input
            id={id} type="number" step={1} min={f.min} placeholder={f.placeholder}
            value={num(f.key)}
            onChange={(e) => onChange(f.key, e.target.value === "" ? undefined : Number(e.target.value))}
          />
        );
      case "float":
        return (
          <Input
            id={id} type="number" step={f.step} min={f.min} max={f.max} placeholder={f.placeholder}
            value={num(f.key)}
            onChange={(e) => onChange(f.key, e.target.value === "" ? undefined : Number(e.target.value))}
          />
        );
      case "text":
        return (
          <Input id={id} placeholder={f.placeholder} value={String(get(f.key) ?? "")} onChange={(e) => onChange(f.key, e.target.value)} />
        );
      case "select":
        return (
          <select
            id={id}
            value={String(get(f.key) ?? "")}
            onChange={(e) => onChange(f.key, e.target.value)}
            className="w-full h-9 rounded-md border border-input bg-transparent px-3 text-sm"
          >
            {f.options.map((o) => <option key={o} value={o}>{o === "" ? "—" : o}</option>)}
          </select>
        );
      default:
        return null;
    }
  };

  return (
    <>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {scalar.map((f) => (
          <div key={f.key} className="space-y-2">
            <Label htmlFor={`recipe-arg-${f.key}`}>
              {t(argLabelKey(f.key))}
              <span className="ml-2 font-mono text-[11px] text-muted-foreground">--{f.key}</span>
            </Label>
            {inputFor(f)}
          </div>
        ))}
      </div>
      <div className="flex flex-wrap gap-x-6 gap-y-2">
        {bools.map((f) => (
          <label key={f.key} className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={get(f.key) === true} onChange={(e) => onChange(f.key, e.target.checked)} />
            {t(argLabelKey(f.key))}
            <span className="font-mono text-[11px] text-muted-foreground">--{f.key}</span>
          </label>
        ))}
      </div>
    </>
  );
}
