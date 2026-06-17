"use client";

import { useState, useMemo } from "react";
import Link from "next/link";
import { toast } from "sonner";
import { useMyKeys, useMyTeams, useDeleteKey, useRevealKey } from "@/hooks/use-api";
import { ModelLimitOverrides } from "@/components/model-limit-overrides";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Trash2,
  Plus,
  AlertCircle,
  RefreshCw,
  Loader2,
  Key,
  Search,
  Copy,
  Check,
} from "lucide-react";
import type { ApiKey } from "@/types";
import { useTranslations } from "next-intl";
import { useLocaleTag } from "@/lib/locale";

/* ── helpers (same as team detail page) ── */

function formatDate(dateStr: string, localeTag: string): string {
  return new Date(dateStr).toLocaleDateString(localeTag, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function maskKey(token: string): string {
  if (token.length <= 8) return token;
  return token.slice(0, 8) + "...";
}

/* ── Delete dialog ── */

function DeleteKeyDialog({
  keyItem,
  onDelete,
  isDeleting,
}: {
  keyItem: ApiKey;
  onDelete: (keyHash: string) => void;
  isDeleting: boolean;
}) {
  const t = useTranslations("keys");
  const tc = useTranslations("common");

  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="ghost" size="icon-xs" className="text-destructive hover:text-destructive">
          <Trash2 className="size-3.5" />
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("deleteTitle")}</DialogTitle>
          <DialogDescription>
            &quot;{keyItem.key_alias || keyItem.token.slice(0, 8)}&quot; {t("deleteDescription")}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">{tc("cancel")}</Button>
          </DialogClose>
          <Button
            variant="destructive"
            disabled={isDeleting}
            onClick={() => onDelete(keyItem.token)}
          >
            {isDeleting && <Loader2 className="size-4 animate-spin" />}
            {tc("delete")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* ── Page ── */

export default function AllKeysPage() {
  const localeTag = useLocaleTag();
  const { data: keys, isLoading, isError, error, refetch } = useMyKeys();
  const { data: teams } = useMyTeams();
  const deleteKeyMutation = useDeleteKey();
  const revealKeyMutation = useRevealKey();
  const [deletingKeyId, setDeletingKeyId] = useState<string | null>(null);
  const [copiedKeyId, setCopiedKeyId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [teamFilter, setTeamFilter] = useState<string>("all");
  const t = useTranslations("keys");
  const tc = useTranslations("common");

  // Build team name lookup
  const teamNameMap = useMemo(() => {
    const map = new Map<string, string>();
    teams?.forEach((t) => map.set(t.team_id, t.team_alias));
    return map;
  }, [teams]);

  // Unique team IDs from keys for filter dropdown
  const keyTeamIds = useMemo(() => {
    if (!keys) return [];
    const ids = new Set<string>();
    keys.forEach((k) => {
      if (k.team_id) ids.add(k.team_id);
    });
    return Array.from(ids);
  }, [keys]);

  // Filtered keys
  const filteredKeys = useMemo(() => {
    if (!keys) return [];
    return keys.filter((k) => {
      // Team filter
      if (teamFilter !== "all" && k.team_id !== teamFilter) return false;
      // Search filter (alias, key name, key token prefix)
      if (searchQuery) {
        const q = searchQuery.toLowerCase();
        const alias = (k.key_alias || "").toLowerCase();
        const name = (k.key_name || "").toLowerCase();
        const token = k.token.toLowerCase();
        if (!alias.includes(q) && !name.includes(q) && !token.includes(q)) return false;
      }
      return true;
    });
  }, [keys, teamFilter, searchQuery]);

  const handleDeleteKey = (keyHash: string) => {
    setDeletingKeyId(keyHash);
    deleteKeyMutation.mutate(keyHash, {
      onSettled: () => setDeletingKeyId(null),
    });
  };

  if (isLoading) {
    return (
      <div className="space-y-6">
        <div className="h-8 w-48 animate-pulse rounded bg-muted" />
        <div className="h-4 w-32 animate-pulse rounded bg-muted" />
        <div className="h-64 w-full animate-pulse rounded-xl bg-muted" />
      </div>
    );
  }

  if (isError) {
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-bold">{t("title")}</h1>
        <div className="flex flex-col items-center gap-4 rounded-xl border border-destructive/20 bg-destructive/5 p-8">
          <AlertCircle className="size-10 text-destructive" />
          <p className="text-sm text-destructive">
            {t("loadError")}{" "}
            {error?.message ?? tc("unknownError")}
          </p>
          <Button variant="outline" size="sm" onClick={() => refetch()}>
            <RefreshCw className="size-4" />
            {tc("retry")}
          </Button>
        </div>
      </div>
    );
  }

  const allKeys = keys ?? [];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Key className="size-6" />
            {t("title")}
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            {t("subtitle")}
          </p>
        </div>
        <Button asChild size="sm">
          <Link href="/keys/new">
            <Plus className="size-4" />
            {t("createKey")}
          </Link>
        </Button>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">{t("allKeys")}</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold">{allKeys.length}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">{t("teamCount")}</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold">{keyTeamIds.length}</p>
          </CardContent>
        </Card>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1 max-w-xs">
          <Search className="absolute left-2.5 top-2.5 size-4 text-muted-foreground" />
          <Input
            placeholder={t("searchPlaceholder")}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="pl-9"
          />
        </div>
        <Select value={teamFilter} onValueChange={setTeamFilter}>
          <SelectTrigger className="w-48">
            <SelectValue placeholder={t("teamFilter")} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t("allTeams")}</SelectItem>
            {keyTeamIds.map((tid) => (
              <SelectItem key={tid} value={tid}>
                {teamNameMap.get(tid) || tid.slice(0, 12) + "..."}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Keys table */}
      {filteredKeys.length === 0 ? (
        <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed p-8">
          <Key className="size-8 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">
            {allKeys.length === 0
              ? t("empty")
              : t("emptySearch")}
          </p>
          {allKeys.length === 0 && (
            <Button asChild variant="outline" size="sm">
              <Link href="/keys/new">
                <Plus className="size-4" />
                {t("createFirst")}
              </Link>
            </Button>
          )}
        </div>
      ) : (
        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("colAlias")}</TableHead>
                <TableHead>{t("colKey")}</TableHead>
                <TableHead>{t("colTeam")}</TableHead>
                <TableHead className="hidden lg:table-cell">TPM</TableHead>
                <TableHead className="hidden lg:table-cell">RPM</TableHead>
                <TableHead>{t("colCreatedAt")}</TableHead>
                <TableHead className="w-12" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {filteredKeys.map((key) => (
                <TableRow key={key.token}>
                  <TableCell className="font-medium">
                    {key.key_alias || "-"}
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-1">
                      <span className="font-mono text-xs text-muted-foreground">{maskKey(key.token)}</span>
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        title={t("copyTitle")}
                        disabled={revealKeyMutation.isPending}
                        onClick={() => {
                          revealKeyMutation.mutate(key.token, {
                            onSuccess: async (res) => {
                              try {
                                await navigator.clipboard.writeText(res.key);
                              } catch {
                                const ta = document.createElement("textarea");
                                ta.value = res.key;
                                ta.style.position = "fixed";
                                ta.style.opacity = "0";
                                document.body.appendChild(ta);
                                ta.select();
                                document.execCommand("copy");
                                document.body.removeChild(ta);
                              }
                              setCopiedKeyId(key.token);
                              toast.success(t("copySuccess"));
                              setTimeout(() => setCopiedKeyId(null), 2000);
                            },
                            onError: (err) => toast.error(err instanceof Error ? err.message : t("copyError")),
                          });
                        }}
                      >
                        {copiedKeyId === key.token ? <Check className="size-3.5 text-green-600" /> : <Copy className="size-3.5" />}
                      </Button>
                    </div>
                  </TableCell>
                  <TableCell>
                    {key.team_id ? (
                      <Link
                        href={`/teams/${key.team_id}`}
                        className="text-sm text-primary hover:underline"
                      >
                        {teamNameMap.get(key.team_id) || key.team_id.slice(0, 12) + "..."}
                      </Link>
                    ) : (
                      <span className="text-xs text-muted-foreground">-</span>
                    )}
                  </TableCell>
                  <TableCell className="hidden lg:table-cell text-sm text-muted-foreground">
                    {key.tpm_limit?.toLocaleString() ?? "-"}
                    <ModelLimitOverrides limits={key.model_tpm_limit} inherited={key.model_tpm_inherited} />
                  </TableCell>
                  <TableCell className="hidden lg:table-cell text-sm text-muted-foreground">
                    {key.rpm_limit?.toLocaleString() ?? "-"}
                    <ModelLimitOverrides limits={key.model_rpm_limit} inherited={key.model_rpm_inherited} />
                  </TableCell>
                  <TableCell className="text-sm text-muted-foreground">
                    {formatDate(key.created_at, localeTag)}
                  </TableCell>
                  <TableCell>
                    <DeleteKeyDialog
                      keyItem={key}
                      onDelete={handleDeleteKey}
                      isDeleting={deletingKeyId === key.token}
                    />
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
