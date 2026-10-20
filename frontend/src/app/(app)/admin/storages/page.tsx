"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import { useCreateStorage, useStorages } from "@/hooks/use-api";
import type { StorageKind } from "@/types";
import { StorageBoundOn } from "@/components/storage-bound-on";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

const KINDS: StorageKind[] = ["weights", "cache", "data", "other"];

export default function AdminStoragesPage() {
  const t = useTranslations("storages");
  const router = useRouter();
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const { data: storages, isLoading } = useStorages({ kind: kind || undefined, q: q.trim() || undefined });
  const create = useCreateStorage();
  const [name, setName] = useState("");

  function add() {
    create.mutate(
      { name: name.trim(), description: null, kind: "weights" },
      {
        onSuccess: (s) => router.push(`/admin/storages/${s.id}`),
        onError: (e) => toast.error(e instanceof Error ? e.message : t("saveError")),
      },
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">{t("title")}</h1>
        <p className="text-muted-foreground mt-1">{t("subtitle")}</p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Input className="w-64" placeholder={t("search")} value={q} onChange={(e) => setQ(e.target.value)} />
        <select
          className="h-9 rounded-md border border-input bg-transparent px-3 text-sm"
          value={kind}
          onChange={(e) => setKind(e.target.value)}
          aria-label={t("kind")}
        >
          <option value="">{t("allKinds")}</option>
          {KINDS.map((k) => <option key={k} value={k}>{t(`kind_${k}`)}</option>)}
        </select>
        <div className="ml-auto flex items-center gap-2">
          <Input
            id="new-storage-name"
            className="w-56 font-mono"
            placeholder="llama-weights"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <Button onClick={add} disabled={!name.trim() || create.isPending} data-testid="storage-create">{t("newStorage")}</Button>
        </div>
      </div>

      {isLoading ? (
        <div className="h-32 animate-pulse rounded-lg bg-muted" />
      ) : !storages || storages.length === 0 ? (
        <div className="rounded-lg border border-dashed p-8 text-center text-muted-foreground">{t("empty")}</div>
      ) : (
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("colName")}</TableHead>
                <TableHead>{t("colKind")}</TableHead>
                <TableHead>{t("colDescription")}</TableHead>
                <TableHead>{t("colBoundOn")}</TableHead>
                <TableHead>{t("colUsage")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {storages.map((s) => (
                <TableRow key={s.id} data-testid={`storage-row-${s.name}`}>
                  <TableCell className="font-mono font-medium">
                    <Link className="hover:underline" href={`/admin/storages/${s.id}`}>{s.name}</Link>
                  </TableCell>
                  <TableCell><Badge variant="secondary">{t(`kind_${s.kind}`)}</Badge></TableCell>
                  <TableCell className="text-sm text-muted-foreground">{s.description ?? "-"}</TableCell>
                  <TableCell><StorageBoundOn boundOn={s.bound_on} /></TableCell>
                  <TableCell className="text-sm">
                    {t("usageCount", { recipes: s.recipe_count ?? 0, deployments: s.deployment_count ?? 0 })}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}
