"use client";

import { useState } from "react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import {
  useServingRecipes,
  useCreateServingRecipe,
  useUpdateServingRecipe,
  useDeleteServingRecipe,
} from "@/hooks/use-api";
import type { ServingRecipe, ServingRecipeInput } from "@/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { DeployFromRecipeDialog } from "@/components/deploy-from-recipe-dialog";

const BLANK: ServingRecipeInput = {
  name: "", description: null, model_path: "", image: "", gpu_count: 1,
  gpu_resource_key: "nvidia.com/gpu", cpu_request: null, cpu_limit: null,
  memory_request: null, memory_limit: null, node_selector: null, tolerations: null,
  pvc_name: null, pvc_mount_path: null, vllm_extra_args: null, env: null,
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

export default function AdminRecipesPage() {
  const t = useTranslations("servingRecipes");
  const tc = useTranslations("common");
  const { data: recipes, isLoading } = useServingRecipes();
  const createMut = useCreateServingRecipe();
  const updateMut = useUpdateServingRecipe();
  const deleteMut = useDeleteServingRecipe();

  const [editing, setEditing] = useState<ServingRecipe | null>(null);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<ServingRecipeInput>(BLANK);
  const [argsText, setArgsText] = useState("");
  const [envText, setEnvText] = useState("");
  const [nsText, setNsText] = useState("");
  const [deployTarget, setDeployTarget] = useState<ServingRecipe | null>(null);

  function openCreate() {
    setEditing(null); setForm(BLANK); setArgsText(""); setEnvText(""); setNsText(""); setOpen(true);
  }
  function openEdit(r: ServingRecipe) {
    setEditing(r); setForm(toInput(r));
    setArgsText(listToLines(r.vllm_extra_args)); setEnvText(mapToLines(r.env)); setNsText(mapToLines(r.node_selector));
    setOpen(true);
  }
  function submit() {
    if (!form.name.trim() || !form.model_path.trim() || !form.image.trim()) {
      toast.error(t("requiredError")); return;
    }
    const body: ServingRecipeInput = {
      ...form,
      vllm_extra_args: linesToList(argsText),
      env: linesToMap(envText),
      node_selector: linesToMap(nsText),
    };
    const onDone = {
      onSuccess: () => { setOpen(false); },
      onError: (e: unknown) => toast.error(e instanceof Error ? e.message : t("saveError")),
    };
    if (editing) updateMut.mutate({ id: editing.id, body }, onDone);
    else createMut.mutate(body, onDone);
  }
  function remove(r: ServingRecipe) {
    if (!confirm(t("deleteConfirm", { name: r.name }))) return;
    deleteMut.mutate(r.id, { onError: (e) => toast.error(e instanceof Error ? e.message : t("saveError")) });
  }

  const saving = createMut.isPending || updateMut.isPending;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">{t("title")}</h1>
          <p className="text-muted-foreground mt-1">{t("subtitle")}</p>
        </div>
        <Button onClick={openCreate}>{t("newRecipe")}</Button>
      </div>

      {isLoading ? (
        <div className="h-32 animate-pulse rounded-lg bg-muted" />
      ) : !recipes || recipes.length === 0 ? (
        <div className="rounded-lg border border-dashed p-8 text-center text-muted-foreground">{t("empty")}</div>
      ) : (
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("colName")}</TableHead>
                <TableHead>{t("colModel")}</TableHead>
                <TableHead>{t("colImage")}</TableHead>
                <TableHead>{t("colGpu")}</TableHead>
                <TableHead className="text-right">{t("colActions")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {recipes.map((r) => (
                <TableRow key={r.id}>
                  <TableCell className="font-medium">{r.name}</TableCell>
                  <TableCell className="font-mono text-xs">{r.model_path}</TableCell>
                  <TableCell className="font-mono text-xs">{r.image}</TableCell>
                  <TableCell>{r.gpu_count} × {r.gpu_resource_key}</TableCell>
                  <TableCell className="text-right space-x-2 whitespace-nowrap">
                    <Button size="xs" onClick={() => setDeployTarget(r)}>{t("deploy")}</Button>
                    <Button size="xs" variant="outline" onClick={() => openEdit(r)}>{t("edit")}</Button>
                    <Button size="xs" variant="destructive" onClick={() => remove(r)}>{t("delete")}</Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editing ? t("editTitle") : t("createTitle")}</DialogTitle>
          </DialogHeader>
          <div className="grid grid-cols-2 gap-3">
            <Labeled label={t("name")}><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Labeled>
            <Labeled label={t("image")}><Input value={form.image} onChange={(e) => setForm({ ...form, image: e.target.value })} /></Labeled>
            <Labeled label={t("modelPath")} span2><Input value={form.model_path} onChange={(e) => setForm({ ...form, model_path: e.target.value })} /></Labeled>
            <Labeled label={t("description")} span2><Input value={form.description ?? ""} onChange={(e) => setForm({ ...form, description: e.target.value || null })} /></Labeled>
            <Labeled label={t("gpuCount")}><Input type="number" min={0} value={form.gpu_count} onChange={(e) => setForm({ ...form, gpu_count: Number(e.target.value) })} /></Labeled>
            <Labeled label={t("gpuResourceKey")}><Input value={form.gpu_resource_key} onChange={(e) => setForm({ ...form, gpu_resource_key: e.target.value })} /></Labeled>
            <Labeled label={t("cpuRequest")}><Input value={form.cpu_request ?? ""} onChange={(e) => setForm({ ...form, cpu_request: e.target.value || null })} /></Labeled>
            <Labeled label={t("cpuLimit")}><Input value={form.cpu_limit ?? ""} onChange={(e) => setForm({ ...form, cpu_limit: e.target.value || null })} /></Labeled>
            <Labeled label={t("memoryRequest")}><Input value={form.memory_request ?? ""} onChange={(e) => setForm({ ...form, memory_request: e.target.value || null })} /></Labeled>
            <Labeled label={t("memoryLimit")}><Input value={form.memory_limit ?? ""} onChange={(e) => setForm({ ...form, memory_limit: e.target.value || null })} /></Labeled>
            <Labeled label={t("pvcName")}><Input value={form.pvc_name ?? ""} onChange={(e) => setForm({ ...form, pvc_name: e.target.value || null })} /></Labeled>
            <Labeled label={t("pvcMountPath")}><Input value={form.pvc_mount_path ?? ""} onChange={(e) => setForm({ ...form, pvc_mount_path: e.target.value || null })} /></Labeled>
            <Labeled label={t("vllmArgs")} span2><Area value={argsText} onChange={setArgsText} /></Labeled>
            <Labeled label={t("env")} span2><Area value={envText} onChange={setEnvText} /></Labeled>
            <Labeled label={t("nodeSelector")} span2><Area value={nsText} onChange={setNsText} /></Labeled>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)} disabled={saving}>{tc("cancel")}</Button>
            <Button onClick={submit} disabled={saving}>{editing ? t("save") : t("create")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <DeployFromRecipeDialog
        key={deployTarget?.id ?? "closed"}
        recipe={deployTarget}
        onClose={() => setDeployTarget(null)}
      />
    </div>
  );
}

function Labeled({ label, span2, children }: { label: string; span2?: boolean; children: React.ReactNode }) {
  return (
    <div className={span2 ? "col-span-2 space-y-1" : "space-y-1"}>
      <Label className="text-xs">{label}</Label>
      {children}
    </div>
  );
}
function Area({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <textarea
      rows={3}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="w-full rounded-md border border-input bg-transparent px-3 py-2 font-mono text-xs focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
    />
  );
}
