"use client";

import { use, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import {
  useDeleteStorage, useDeleteStorageBinding, useStorage, useStorageBindingStatus, useUpdateStorage,
} from "@/hooks/use-api";
import type { StorageBinding, StorageDetail, StorageKind } from "@/types";
import { StorageBindingDialog } from "@/components/storage-binding-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

const KINDS: StorageKind[] = ["weights", "cache", "data", "other"];

export default function StorageDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data, isLoading } = useStorage(id);
  if (isLoading || !data) return <div className="h-32 animate-pulse rounded-lg bg-muted" />;
  return <StorageDetailView key={data.updated_at ?? data.id} storage={data} />;
}

function target(b: StorageBinding) {
  return b.type === "pvc" ? b.claim_name : `${b.nfs_server}:${b.nfs_path}`;
}

function StorageDetailView({ storage }: { storage: StorageDetail }) {
  const t = useTranslations("storages");
  const router = useRouter();
  const update = useUpdateStorage(storage.id);
  const remove = useDeleteStorage();
  const removeBinding = useDeleteStorageBinding(storage.id);
  const { data: status } = useStorageBindingStatus(storage.id);
  const [form, setForm] = useState({ name: storage.name, description: storage.description ?? "", kind: storage.kind });
  const [dialog, setDialog] = useState<{ binding: StorageBinding | null } | null>(null);
  const inUse = storage.usage.recipes.length + storage.usage.deployments.length + storage.usage.clusters.length > 0;

  function save() {
    update.mutate(
      { name: form.name.trim(), description: form.description.trim() || null, kind: form.kind },
      { onSuccess: () => toast.success(t("saved")), onError: (e) => toast.error(e instanceof Error ? e.message : t("saveError")) },
    );
  }

  function del() {
    if (!confirm(t("deleteConfirm", { name: storage.name }))) return;
    remove.mutate(storage.id, {
      onSuccess: () => router.push("/admin/storages"),
      onError: (e) => toast.error(e instanceof Error ? e.message : t("saveError")),
    });
  }

  async function delBinding(b: StorageBinding) {
    const dry = await removeBinding.mutateAsync({ id: b.id, dryRun: true });
    const n = dry.affected_deployments.length;
    const msg = n
      ? t("deleteBindingAffected", { count: n, names: dry.affected_deployments.map((d) => d.model_name).join(", ") })
      : t("deleteBindingConfirm");
    if (!confirm(msg)) return;
    removeBinding.mutate({ id: b.id, dryRun: false }, { onError: (e) => toast.error(e instanceof Error ? e.message : t("saveError")) });
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-2">
        <h1 className="font-mono text-2xl font-bold">{storage.name}</h1>
        <Button variant="destructive" size="sm" onClick={del} disabled={inUse} title={inUse ? t("inUse") : undefined}>
          {t("delete")}
        </Button>
      </div>

      <Card>
        <CardContent className="grid grid-cols-1 gap-3 pt-6 sm:grid-cols-3">
          <div className="space-y-1">
            <Label htmlFor="storage-name">{t("name")}</Label>
            <Input id="storage-name" className="font-mono" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            <p className="text-xs text-muted-foreground">{t("nameHint")}</p>
          </div>
          <div className="space-y-1">
            <Label htmlFor="storage-kind">{t("kind")}</Label>
            <select
              id="storage-kind"
              className="w-full h-9 rounded-md border border-input bg-transparent px-3 text-sm"
              value={form.kind}
              onChange={(e) => setForm({ ...form, kind: e.target.value as StorageKind })}
            >
              {KINDS.map((k) => <option key={k} value={k}>{t(`kind_${k}`)}</option>)}
            </select>
          </div>
          <div className="space-y-1 sm:col-span-3">
            <Label htmlFor="storage-description">{t("description")}</Label>
            <Input id="storage-description" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          </div>
          <div className="sm:col-span-3 flex justify-end">
            <Button onClick={save} disabled={update.isPending}>{t("save")}</Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <div>
            <CardTitle className="text-base">{t("bindings")}</CardTitle>
            <p className="text-xs text-muted-foreground mt-1">{t("bindingsHint")}</p>
          </div>
          <Button size="sm" onClick={() => setDialog({ binding: null })} data-testid="binding-add">{t("addBinding")}</Button>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("colCluster")}</TableHead>
                <TableHead>{t("colNamespace")}</TableHead>
                <TableHead>{t("colType")}</TableHead>
                <TableHead>{t("colTarget")}</TableHead>
                <TableHead>{t("colSubPath")}</TableHead>
                <TableHead>{t("colStatus")}</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {storage.bindings.map((b) => {
                const st = status?.[b.id];
                return (
                  <TableRow key={b.id} data-testid={`binding-row-${b.id}`}>
                    <TableCell>{b.cluster_name}</TableCell>
                    <TableCell className="font-mono text-xs">{b.namespace ?? t("allNamespaces")}</TableCell>
                    <TableCell><Badge variant="outline" className="uppercase">{b.type}</Badge></TableCell>
                    <TableCell className="font-mono text-xs">{target(b)}</TableCell>
                    <TableCell className="font-mono text-xs">{b.sub_path ?? "-"}</TableCell>
                    <TableCell>
                      {st ? (
                        <Badge variant={st === "Bound" ? "secondary" : "destructive"}>{t(`status_${st}`)}</Badge>
                      ) : "—"}
                    </TableCell>
                    <TableCell className="space-x-2 text-right whitespace-nowrap">
                      <Button size="xs" variant="outline" onClick={() => setDialog({ binding: b })}>{t("edit")}</Button>
                      <Button size="xs" variant="destructive" onClick={() => delBinding(b)}>{t("delete")}</Button>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">{t("usage")}</CardTitle></CardHeader>
        <CardContent className="space-y-2 text-sm">
          {!inUse && <p className="text-muted-foreground">{t("usageNone")}</p>}
          {storage.usage.recipes.length > 0 && (
            <p>{t("usageRecipes")}: {storage.usage.recipes.map((r, i) => (
              <span key={r.id}>{i > 0 && ", "}<Link className="underline" href={`/admin/recipes/${r.id}`}>{r.name}</Link></span>
            ))}</p>
          )}
          {storage.usage.deployments.length > 0 && (
            <p>{t("usageDeployments")}: {storage.usage.deployments.map((d, i) => (
              <span key={d.id} data-testid={`usage-deployment-${d.id}`}>
                {i > 0 && ", "}
                <Link className="underline" href={`/admin/deployments/${d.id}`}>{d.model_name}</Link>
                {d.stale && <Badge variant="destructive" className="ml-1">{t("stale")}</Badge>}
              </span>
            ))}</p>
          )}
          {storage.usage.clusters.length > 0 && (
            <p>{t("usageClusters")}: {storage.usage.clusters.map((c) => c.name).join(", ")}</p>
          )}
        </CardContent>
      </Card>

      {dialog && (
        <StorageBindingDialog
          key={dialog.binding?.id ?? "new"}
          storageId={storage.id}
          binding={dialog.binding}
          open
          onClose={() => setDialog(null)}
        />
      )}
    </div>
  );
}
