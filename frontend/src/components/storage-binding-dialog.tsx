"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { useClusterPvcs, useCreateStorageBinding, useK8sClusters, useUpdateStorageBinding } from "@/hooks/use-api";
import type { StorageBinding, StorageBindingInput } from "@/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const MANUAL = "__manual__";

export function StorageBindingDialog({
  storageId, binding, open, onClose,
}: { storageId: string; binding: StorageBinding | null; open: boolean; onClose: () => void }) {
  const t = useTranslations("storages");
  const { data: clusters } = useK8sClusters();
  const create = useCreateStorageBinding(storageId);
  const update = useUpdateStorageBinding(storageId);
  const [form, setForm] = useState<StorageBindingInput>(() => ({
    cluster_id: binding?.cluster_id ?? null,
    namespace: binding?.namespace ?? "",
    type: binding?.type ?? "pvc",
    claim_name: binding?.claim_name ?? "",
    nfs_server: binding?.nfs_server ?? "",
    nfs_path: binding?.nfs_path ?? "",
    sub_path: binding?.sub_path ?? "",
  }));
  const [manual, setManual] = useState(false);
  const set = (patch: Partial<StorageBindingInput>) => setForm((f) => ({ ...f, ...patch }));
  const ns = (form.namespace ?? "").trim();
  const pvcs = useClusterPvcs(form.cluster_id, ns, open && form.type === "pvc");
  const pvcList = pvcs.data?.pvcs ?? [];
  const showManual = manual || !!pvcs.data?.error || (!!form.claim_name && !pvcList.some((p) => p.name === form.claim_name));

  const valid =
    form.type === "pvc"
      ? !!ns && !!(form.claim_name ?? "").trim()
      : !!(form.nfs_server ?? "").trim() && !!(form.nfs_path ?? "").trim();

  function save() {
    if (!valid) return;
    const body: StorageBindingInput = {
      cluster_id: form.cluster_id,
      namespace: ns || null,
      type: form.type,
      claim_name: form.type === "pvc" ? (form.claim_name ?? "").trim() || null : null,
      nfs_server: form.type === "nfs" ? (form.nfs_server ?? "").trim() || null : null,
      nfs_path: form.type === "nfs" ? (form.nfs_path ?? "").trim() || null : null,
      sub_path: (form.sub_path ?? "").trim() || null,
    };
    const opts = {
      onSuccess: () => { toast.success(t("saved")); onClose(); },
      onError: (e: unknown) => toast.error(e instanceof Error ? e.message : t("saveError")),
    };
    if (binding) update.mutate({ id: binding.id, body }, opts);
    else create.mutate(body, opts);
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent>
        <DialogHeader><DialogTitle>{binding ? t("editBinding") : t("addBinding")}</DialogTitle></DialogHeader>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="space-y-1">
            <Label htmlFor="binding-cluster">{t("cluster")}</Label>
            <select
              id="binding-cluster"
              className="w-full h-9 rounded-md border border-input bg-transparent px-3 text-sm"
              value={form.cluster_id ?? ""}
              onChange={(e) => set({ cluster_id: e.target.value || null })}
            >
              <option value="">{t("clusterDefault")}</option>
              {(clusters ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="binding-type">{t("type")}</Label>
            <select
              id="binding-type"
              className="w-full h-9 rounded-md border border-input bg-transparent px-3 text-sm"
              value={form.type}
              onChange={(e) => set({ type: e.target.value as "pvc" | "nfs" })}
            >
              <option value="pvc">PVC</option>
              <option value="nfs">NFS</option>
            </select>
          </div>
          <div className="space-y-1 sm:col-span-2">
            <Label htmlFor="binding-namespace">{t("namespace")}</Label>
            <Input id="binding-namespace" className="font-mono" value={form.namespace ?? ""} onChange={(e) => set({ namespace: e.target.value })} />
            <p className="text-xs text-muted-foreground">{form.type === "pvc" ? t("namespaceHintPvc") : t("namespaceHintNfs")}</p>
          </div>
          {form.type === "pvc" ? (
            <div className="space-y-1 sm:col-span-2">
              <Label htmlFor="binding-claim">{t("claimName")}</Label>
              {!showManual && pvcList.length > 0 ? (
                <select
                  id="binding-claim"
                  className="w-full h-9 rounded-md border border-input bg-transparent px-3 font-mono text-sm"
                  value={form.claim_name ?? ""}
                  onChange={(e) => (e.target.value === MANUAL ? setManual(true) : set({ claim_name: e.target.value }))}
                >
                  <option value="" />
                  {pvcList.map((p) => (
                    <option key={p.name} value={p.name}>
                      {p.name} · {p.phase ?? "?"}{p.capacity ? ` · ${p.capacity}` : ""}
                    </option>
                  ))}
                  <option value={MANUAL}>{t("claimManual")}</option>
                </select>
              ) : (
                <Input id="binding-claim" className="font-mono" value={form.claim_name ?? ""} onChange={(e) => set({ claim_name: e.target.value })} />
              )}
              {pvcs.data?.error && <p className="text-xs text-amber-600">{t("pvcLookupFailed", { error: pvcs.data.error })}</p>}
            </div>
          ) : (
            <>
              <div className="space-y-1">
                <Label htmlFor="binding-nfs-server">{t("nfsServer")}</Label>
                <Input id="binding-nfs-server" className="font-mono" value={form.nfs_server ?? ""} onChange={(e) => set({ nfs_server: e.target.value })} />
              </div>
              <div className="space-y-1">
                <Label htmlFor="binding-nfs-path">{t("nfsPath")}</Label>
                <Input id="binding-nfs-path" className="font-mono" placeholder="/export/models" value={form.nfs_path ?? ""} onChange={(e) => set({ nfs_path: e.target.value })} />
              </div>
            </>
          )}
          <div className="space-y-1 sm:col-span-2">
            <Label htmlFor="binding-sub-path">{t("subPath")}</Label>
            <Input id="binding-sub-path" className="font-mono" value={form.sub_path ?? ""} onChange={(e) => set({ sub_path: e.target.value })} />
            <p className="text-xs text-muted-foreground">{t("subPathHint")}</p>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>{t("cancel")}</Button>
          <Button onClick={save} disabled={!valid || create.isPending || update.isPending} data-testid="binding-save">{t("save")}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
