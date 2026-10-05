"use client";

import { useState } from "react";
import { Cpu, Loader2, Pencil, Plus, RefreshCw, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import {
  useCreateGpuProfile, useDeleteGpuProfile, useGpuNodes, useGpuProfiles, useK8sClusters, useUpdateGpuProfile,
} from "@/hooks/use-api";
import type { GpuNodeGroup, GpuProfile, GpuProfileInput } from "@/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { linesToTolerations, tolerationsToLines } from "@/lib/placement";

type FormState = {
  name: string; label_key: string; label_value: string; gpu_resource_key: string;
  tolerations: string; vram_gb: string; description: string; enabled: boolean;
};
const EMPTY: FormState = {
  name: "", label_key: "", label_value: "", gpu_resource_key: "nvidia.com/gpu",
  tolerations: "", vram_gb: "", description: "", enabled: true,
};

function toForm(p: GpuProfile): FormState {
  return {
    name: p.name, label_key: p.label_key ?? "", label_value: p.label_value, gpu_resource_key: p.gpu_resource_key,
    tolerations: tolerationsToLines(p.tolerations), vram_gb: p.vram_gb == null ? "" : String(p.vram_gb),
    description: p.description ?? "", enabled: p.enabled,
  };
}

function toBody(f: FormState): GpuProfileInput {
  return {
    name: f.name.trim(),
    label_key: f.label_key.trim() || null,
    label_value: f.label_value.trim(),
    gpu_resource_key: f.gpu_resource_key.trim() || "nvidia.com/gpu",
    tolerations: linesToTolerations(f.tolerations),
    vram_gb: f.vram_gb.trim() ? Number(f.vram_gb) : null,
    description: f.description.trim() || null,
    enabled: f.enabled,
  };
}

/**
 * Admin card: GPU type profiles for one cluster (select at the top; "포털 기본"
 * = the mounted kubeconfig cluster) plus a read-only node scan that offers
 * one-click profile creation per discovered label value.
 */
export function GpuProfilesCard() {
  const t = useTranslations("gpuProfiles");
  const { data: clusters } = useK8sClusters();
  const [clusterId, setClusterId] = useState<string | null>(null);
  const { data, isLoading } = useGpuProfiles(clusterId);
  const [scanOn, setScanOn] = useState(false);
  const nodes = useGpuNodes(clusterId, scanOn);
  const createMut = useCreateGpuProfile(clusterId);
  const updateMut = useUpdateGpuProfile(clusterId);
  const deleteMut = useDeleteGpuProfile(clusterId);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<GpuProfile | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY);

  const labelKey = data?.label_key ?? "gpu-type";
  const profiles = data?.profiles ?? [];

  function openCreate(prefill?: Partial<FormState>) {
    setEditing(null);
    setForm({ ...EMPTY, ...prefill });
    setDialogOpen(true);
  }
  function openEdit(p: GpuProfile) {
    setEditing(p);
    setForm(toForm(p));
    setDialogOpen(true);
  }
  function save() {
    if (!form.name.trim() || !form.label_value.trim()) {
      toast.error(t("requiredError"));
      return;
    }
    const body = toBody(form);
    const opts = {
      onSuccess: () => { toast.success(editing ? t("updateSuccess") : t("createSuccess")); setDialogOpen(false); },
      onError: (e: unknown) => toast.error(e instanceof Error ? e.message : t("saveFailed")),
    };
    if (editing) updateMut.mutate({ id: editing.id, body }, opts);
    else createMut.mutate(body, opts);
  }
  function remove(p: GpuProfile) {
    if (!window.confirm(t("deleteConfirm", { name: p.name }))) return;
    deleteMut.mutate(p.id, {
      onSuccess: () => toast.success(t("deleteSuccess")),
      onError: (e) => toast.error(e instanceof Error ? e.message : t("deleteFailed")),
    });
  }

  const saving = createMut.isPending || updateMut.isPending;
  const fmtGpu = (g: GpuNodeGroup) => {
    const keys = Object.keys(g.allocatable);
    return keys.map((k) => `${g.available[k] ?? 0}/${g.allocatable[k] ?? 0}${keys.length > 1 ? ` ${k}` : ""}`).join(" · ");
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle className="text-base flex items-center gap-2"><Cpu className="size-4" />{t("title")}</CardTitle>
            <CardDescription>{t("description")}</CardDescription>
          </div>
          <div className="flex items-center gap-2">
            <select
              aria-label={t("clusterSelect")}
              className="h-8 rounded-md border border-input bg-transparent px-2 text-sm"
              value={clusterId ?? ""}
              onChange={(e) => { setClusterId(e.target.value || null); setScanOn(false); }}
            >
              <option value="">{t("portalDefaultCluster")}</option>
              {(clusters ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
            <Button size="sm" onClick={() => openCreate()}><Plus className="size-3.5" />{t("addButton")}</Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        <p className="text-xs text-muted-foreground">
          {t("labelKeyInfo", { key: labelKey })}
        </p>

        {isLoading ? (
          <div className="flex justify-center py-6"><Loader2 className="size-5 animate-spin text-muted-foreground" /></div>
        ) : profiles.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("empty")}</p>
        ) : (
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("colName")}</TableHead>
                  <TableHead>{t("colLabel")}</TableHead>
                  <TableHead>{t("colResourceKey")}</TableHead>
                  <TableHead>{t("colTolerations")}</TableHead>
                  <TableHead>{t("colVram")}</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {profiles.map((p) => (
                  <TableRow key={p.id} className={p.enabled ? "" : "opacity-60"}>
                    <TableCell className="font-medium">
                      {p.name}
                      {!p.enabled && <Badge variant="secondary" className="ml-2 text-[10px]">{t("disabled")}</Badge>}
                      {p.description && <div className="text-xs text-muted-foreground">{p.description}</div>}
                    </TableCell>
                    <TableCell className="font-mono text-xs">{p.effective_label_key}={p.label_value}</TableCell>
                    <TableCell className="font-mono text-xs">{p.gpu_resource_key}</TableCell>
                    <TableCell className="font-mono text-xs whitespace-pre-line">{tolerationsToLines(p.tolerations) || "—"}</TableCell>
                    <TableCell className="tabular-nums text-xs">{p.vram_gb != null ? `${p.vram_gb} GB` : "—"}</TableCell>
                    <TableCell className="text-right">
                      <Button variant="ghost" size="icon-xs" onClick={() => openEdit(p)} title={t("edit")}><Pencil className="size-3.5" /></Button>
                      <Button variant="ghost" size="icon-xs" className="text-destructive hover:text-destructive" onClick={() => remove(p)} title={t("delete")}>
                        <Trash2 className="size-3.5" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}

        {/* Node discovery */}
        <div className="space-y-2 rounded-md border border-dashed p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <div className="text-sm font-medium">{t("nodesTitle")}</div>
              <div className="text-xs text-muted-foreground">{t("nodesHint", { key: labelKey })}</div>
            </div>
            <Button variant="outline" size="sm" onClick={() => { setScanOn(true); nodes.refetch(); }} disabled={nodes.isFetching}>
              {nodes.isFetching ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}
              {t("scanButton")}
            </Button>
          </div>
          {scanOn && nodes.data && (
            <>
              {nodes.data.errors.length > 0 && (
                <p className="text-xs text-destructive">{nodes.data.errors.join(" · ")}</p>
              )}
              {nodes.data.groups.length === 0 && nodes.data.errors.length === 0 && (
                <p className="text-xs text-muted-foreground">{t("nodesNone")}</p>
              )}
              {nodes.data.groups.length > 0 && (
                <ul className="divide-y rounded-md border text-sm">
                  {nodes.data.groups.map((g) => (
                    <li key={g.label_value ?? "__none__"} className="flex flex-wrap items-center gap-3 px-3 py-2">
                      <code className="text-xs">{g.label_value ? `${nodes.data!.label_key}=${g.label_value}` : t("nodesUnlabelled")}</code>
                      <span className="text-xs text-muted-foreground">{t("nodesCount", { count: g.nodes })}</span>
                      <span className="text-xs tabular-nums">{t("nodesAvailable")} {fmtGpu(g)}</span>
                      <span className="ml-auto">
                        {g.profile_name ? (
                          <Badge variant="outline">{g.profile_name}</Badge>
                        ) : g.label_value ? (
                          <Button size="xs" variant="outline" onClick={() => openCreate({ name: g.label_value!, label_value: g.label_value! })}>
                            <Plus className="size-3" />{t("makeProfile")}
                          </Button>
                        ) : (
                          <span className="text-xs text-amber-600">{t("nodesLabelMissing")}</span>
                        )}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </div>
      </CardContent>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle>{editing ? t("editTitle") : t("addTitle")}</DialogTitle></DialogHeader>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="space-y-1">
              <Label htmlFor="gp-name">{t("colName")}</Label>
              <Input id="gp-name" className="font-mono" placeholder="a100-80g" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="gp-label-value">{t("labelValue")}</Label>
              <Input id="gp-label-value" className="font-mono" placeholder="a100-80g" value={form.label_value} onChange={(e) => setForm({ ...form, label_value: e.target.value })} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="gp-label-key">{t("labelKeyOverride")}</Label>
              <Input id="gp-label-key" className="font-mono" placeholder={labelKey} value={form.label_key} onChange={(e) => setForm({ ...form, label_key: e.target.value })} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="gp-resource-key">{t("colResourceKey")}</Label>
              <Input id="gp-resource-key" className="font-mono" value={form.gpu_resource_key} onChange={(e) => setForm({ ...form, gpu_resource_key: e.target.value })} />
            </div>
            <div className="space-y-1 sm:col-span-2">
              <Label htmlFor="gp-tolerations">{t("colTolerations")}</Label>
              <textarea
                id="gp-tolerations" rows={3} value={form.tolerations} placeholder={"nvidia.com/gpu=present:NoSchedule\ngpu-pool=a100:NoSchedule"}
                onChange={(e) => setForm({ ...form, tolerations: e.target.value })}
                className="w-full rounded-md border border-input bg-transparent px-3 py-2 font-mono text-sm leading-relaxed placeholder:text-muted-foreground/50"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="gp-vram">{t("colVram")}</Label>
              <Input id="gp-vram" type="number" min={1} placeholder="80" value={form.vram_gb} onChange={(e) => setForm({ ...form, vram_gb: e.target.value })} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="gp-desc">{t("descriptionLabel")}</Label>
              <Input id="gp-desc" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
            </div>
            <label className="flex items-center gap-2 text-sm sm:col-span-2">
              <input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} />
              {t("enabled")}
            </label>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)} disabled={saving}>{t("cancel")}</Button>
            <Button onClick={save} disabled={saving}>{saving && <Loader2 className="size-4 animate-spin" />}{t("save")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
