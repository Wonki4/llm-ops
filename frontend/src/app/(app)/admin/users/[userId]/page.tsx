"use client";

import { use, useState } from "react";
import Link from "next/link";
import { toast } from "sonner";
import {
  ArrowLeft,
  Loader2,
  ShieldCheck,
  Mail,
  Calendar,
  Key as KeyIcon,
  Users as UsersIcon,
  DollarSign,
  Pencil,
  UserMinus,
  UserPlus,
} from "lucide-react";
import { useTranslations } from "next-intl";

import {
  useAdminUserDetail,
  useAdminUpdateKeyLimits,
  useAdminRemoveUserFromTeam,
  useAdminAssignUserToTeam,
  useDiscoverTeams,
} from "@/hooks/use-api";
import type { AdminUserKey, AdminUserTeam } from "@/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

function formatBudget(value: number | null, unlimited: string): string {
  if (value == null) return unlimited;
  return `$${value.toFixed(2)}`;
}

function formatDate(dateStr: string | null): string {
  if (!dateStr) return "-";
  return new Date(dateStr).toLocaleString("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatDateShort(dateStr: string | null): string {
  if (!dateStr) return "-";
  return new Date(dateStr).toLocaleDateString("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
}

export default function AdminUserDetailPage({
  params,
}: {
  params: Promise<{ userId: string }>;
}) {
  const t = useTranslations("adminUsers");
  const tc = useTranslations("common");

  const { userId: rawUserId } = use(params);
  const userId = decodeURIComponent(rawUserId);
  const { data, isLoading, error } = useAdminUserDetail(userId);
  const updateLimitsMutation = useAdminUpdateKeyLimits();
  const removeFromTeamMutation = useAdminRemoveUserFromTeam();

  const [editingKey, setEditingKey] = useState<AdminUserKey | null>(null);
  const [tpmInput, setTpmInput] = useState("");
  const [rpmInput, setRpmInput] = useState("");
  const [removingTeam, setRemovingTeam] = useState<AdminUserTeam | null>(null);
  const [assignOpen, setAssignOpen] = useState(false);
  const [assignTeamId, setAssignTeamId] = useState("");
  const [assignRole, setAssignRole] = useState<"user" | "admin">("user");
  const [assignSearch, setAssignSearch] = useState("");
  const assignMutation = useAdminAssignUserToTeam();
  const { data: allTeams } = useDiscoverTeams();

  const openKeyEditor = (key: AdminUserKey) => {
    setEditingKey(key);
    setTpmInput(key.tpm_limit == null ? "" : String(key.tpm_limit));
    setRpmInput(key.rpm_limit == null ? "" : String(key.rpm_limit));
  };

  const parseLimit = (value: string): number | null => {
    const trimmed = value.trim();
    if (trimmed === "") return null;
    const n = Number(trimmed);
    return Number.isFinite(n) && n >= 0 ? Math.floor(n) : NaN as unknown as number;
  };

  const handleSaveLimits = () => {
    if (!editingKey) return;
    const tpm = parseLimit(tpmInput);
    const rpm = parseLimit(rpmInput);
    if (Number.isNaN(tpm) || Number.isNaN(rpm)) {
      toast.error(t("tpmRpmValidationError"));
      return;
    }
    updateLimitsMutation.mutate(
      { userId, token: editingKey.token, tpmLimit: tpm, rpmLimit: rpm },
      {
        onSuccess: () => {
          toast.success(t("keyLimitsUpdated"));
          setEditingKey(null);
        },
        onError: (err) =>
          toast.error(err instanceof Error ? err.message : t("updateFailed")),
      },
    );
  };

  const handleRemoveFromTeam = () => {
    if (!removingTeam) return;
    removeFromTeamMutation.mutate(
      { userId, teamId: removingTeam.team_id },
      {
        onSuccess: () => {
          toast.success(t("removedFromTeam"));
          setRemovingTeam(null);
        },
        onError: (err) =>
          toast.error(err instanceof Error ? err.message : t("removeFromTeamFailed")),
      },
    );
  };

  const handleAssignToTeam = () => {
    if (!assignTeamId) {
      toast.error(t("selectTeamError"));
      return;
    }
    assignMutation.mutate(
      { userId, teamId: assignTeamId, role: assignRole },
      {
        onSuccess: () => {
          toast.success(t("addedToTeam"));
          setAssignOpen(false);
          setAssignTeamId("");
          setAssignRole("user");
          setAssignSearch("");
        },
        onError: (err) =>
          toast.error(err instanceof Error ? err.message : t("addToTeamFailed")),
      },
    );
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="space-y-4">
        <Link href="/admin/users">
          <Button variant="ghost" size="sm">
            <ArrowLeft className="size-4 mr-1" />
            {t("backToUsers")}
          </Button>
        </Link>
        <div className="rounded-lg border border-dashed p-8 text-center">
          <p className="text-muted-foreground">
            {error instanceof Error ? error.message : t("userLoadError")}
          </p>
        </div>
      </div>
    );
  }

  const { user, keys, teams } = data;

  return (
    <div className="space-y-6">
      <div>
        <Link href="/admin/users">
          <Button variant="ghost" size="sm" className="-ml-2 mb-2">
            <ArrowLeft className="size-4 mr-1" />
            {t("backToUsers")}
          </Button>
        </Link>
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold font-mono">{user.user_id}</h1>
            <p className="text-muted-foreground mt-1">
              {user.display_name || t("noName")}
              {user.email && (
                <>
                  <span className="mx-2">·</span>
                  <span className="inline-flex items-center gap-1">
                    <Mail className="size-3.5" />
                    {user.email}
                  </span>
                </>
              )}
            </p>
          </div>
          {user.global_role === "super_user" ? (
            <Badge className="bg-purple-100 text-purple-700 hover:bg-purple-100">
              <ShieldCheck className="size-3 mr-1" />
              {t("badgeAdmin")}
            </Badge>
          ) : (
            <Badge variant="outline">{t("badgeUser")}</Badge>
          )}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              {t("statTotalUsage")}
            </CardTitle>
            <DollarSign className="size-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold tabular-nums">
              ${user.spend.toFixed(2)}
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              {t("statLimit", { limit: formatBudget(user.max_budget, t("unlimited")) })}
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              {t("statKeys")}
            </CardTitle>
            <KeyIcon className="size-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{t("countUnit", { count: keys.length })}</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              {t("statTeams")}
            </CardTitle>
            <UsersIcon className="size-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{t("countUnit", { count: teams.length })}</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              {t("statJoinedAt")}
            </CardTitle>
            <Calendar className="size-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-lg font-semibold">
              {formatDateShort(user.created_at)}
            </div>
          </CardContent>
        </Card>
      </div>

      <Tabs defaultValue="teams" className="space-y-4">
        <div className="flex items-center justify-between">
          <TabsList>
            <TabsTrigger value="teams">{t("tabTeams", { count: teams.length })}</TabsTrigger>
            <TabsTrigger value="keys">{t("tabKeys", { count: keys.length })}</TabsTrigger>
          </TabsList>
          <Button size="sm" onClick={() => setAssignOpen(true)}>
            <UserPlus className="size-3.5 mr-1" />
            {t("addToTeamButton")}
          </Button>
        </div>

        <TabsContent value="teams" className="space-y-2">
          {teams.length === 0 ? (
            <div className="rounded-lg border border-dashed p-8 text-center">
              <p className="text-sm text-muted-foreground">{t("noTeams")}</p>
            </div>
          ) : (
            <div className="rounded-lg border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("colTeam")}</TableHead>
                    <TableHead>{t("colRole")}</TableHead>
                    <TableHead className="text-right">{t("colUsage")}</TableHead>
                    <TableHead className="text-right">{t("colLimit")}</TableHead>
                    <TableHead>{t("colExpiry")}</TableHead>
                    <TableHead className="w-16 text-right" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {teams.map((t_) => (
                    <TableRow key={t_.team_id}>
                      <TableCell>
                        <Link
                          href={`/teams/${t_.team_id}`}
                          className="hover:underline"
                        >
                          <span className="font-medium">
                            {t_.team_alias || t_.team_id}
                          </span>
                          {t_.team_alias && (
                            <span className="block font-mono text-xs text-muted-foreground">
                              {t_.team_id}
                            </span>
                          )}
                        </Link>
                      </TableCell>
                      <TableCell>
                        {t_.is_admin ? (
                          <Badge className="bg-blue-100 text-blue-700 hover:bg-blue-100">
                            {t("badgeTeamAdmin")}
                          </Badge>
                        ) : (
                          <Badge variant="outline">{t("badgeMember")}</Badge>
                        )}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        ${t_.spend.toFixed(2)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-muted-foreground">
                        {formatBudget(t_.max_budget, t("unlimited"))}
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        {formatDateShort(t_.expires_at)}
                        {t_.expiry_status && t_.expiry_status !== "active" && (
                          <Badge variant="outline" className="ml-2 text-xs">
                            {t_.expiry_status}
                          </Badge>
                        )}
                      </TableCell>
                      <TableCell className="text-right">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="text-destructive hover:text-destructive"
                          onClick={() => setRemovingTeam(t_)}
                        >
                          <UserMinus className="size-3.5 mr-1" />
                          {t("leaveButton")}
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </TabsContent>

        <TabsContent value="keys" className="space-y-2">
          {keys.length === 0 ? (
            <div className="rounded-lg border border-dashed p-8 text-center">
              <p className="text-sm text-muted-foreground">{t("noKeys")}</p>
            </div>
          ) : (
            <div className="rounded-lg border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Alias</TableHead>
                    <TableHead>{t("colTeam")}</TableHead>
                    <TableHead>{t("colModel")}</TableHead>
                    <TableHead className="text-right">{t("colUsage")}</TableHead>
                    <TableHead className="text-right">{t("colLimit")}</TableHead>
                    <TableHead className="text-right">TPM</TableHead>
                    <TableHead className="text-right">RPM</TableHead>
                    <TableHead>{t("colCreatedAt")}</TableHead>
                    <TableHead className="w-16 text-right" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {keys.map((k) => (
                    <TableRow key={k.token}>
                      <TableCell>
                        <div className="font-medium">
                          {k.key_alias || k.key_name || "-"}
                        </div>
                        <div className="font-mono text-xs text-muted-foreground">
                          {k.token.slice(0, 12)}...
                        </div>
                      </TableCell>
                      <TableCell>
                        {k.team_id ? (
                          <Link
                            href={`/teams/${k.team_id}`}
                            className="font-mono text-xs hover:underline"
                          >
                            {k.team_id}
                          </Link>
                        ) : (
                          <span className="text-muted-foreground">-</span>
                        )}
                      </TableCell>
                      <TableCell>
                        {k.models.length === 0 ? (
                          <span className="text-xs text-muted-foreground">{t("modelAll")}</span>
                        ) : (
                          <div className="flex flex-wrap gap-1">
                            {k.models.slice(0, 3).map((m) => (
                              <Badge key={m} variant="outline" className="text-xs">
                                {m}
                              </Badge>
                            ))}
                            {k.models.length > 3 && (
                              <Badge variant="outline" className="text-xs">
                                +{k.models.length - 3}
                              </Badge>
                            )}
                          </div>
                        )}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        ${k.spend.toFixed(2)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-muted-foreground">
                        {formatBudget(k.max_budget, t("unlimited"))}
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-muted-foreground">
                        {k.tpm_limit ?? "-"}
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-muted-foreground">
                        {k.rpm_limit ?? "-"}
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        {formatDate(k.created_at)}
                      </TableCell>
                      <TableCell className="text-right">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => openKeyEditor(k)}
                        >
                          <Pencil className="size-3.5 mr-1" />
                          {tc("edit")}
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </TabsContent>
      </Tabs>

      <Dialog
        open={!!editingKey}
        onOpenChange={(open) => !open && setEditingKey(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("editKeyLimitsTitle")}</DialogTitle>
            <DialogDescription>
              {editingKey?.key_alias || editingKey?.key_name || editingKey?.token.slice(0, 12)}
              {" "}{t("editKeyLimitsDescription")}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <Label htmlFor="tpm-input">{t("tpmLabel")}</Label>
              <Input
                id="tpm-input"
                type="number"
                min={0}
                placeholder={t("unlimited")}
                value={tpmInput}
                onChange={(e) => setTpmInput(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="rpm-input">{t("rpmLabel")}</Label>
              <Input
                id="rpm-input"
                type="number"
                min={0}
                placeholder={t("unlimited")}
                value={rpmInput}
                onChange={(e) => setRpmInput(e.target.value)}
              />
            </div>
          </div>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline">{tc("cancel")}</Button>
            </DialogClose>
            <Button
              onClick={handleSaveLimits}
              disabled={updateLimitsMutation.isPending}
            >
              {updateLimitsMutation.isPending && (
                <Loader2 className="size-4 mr-1 animate-spin" />
              )}
              {tc("save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={assignOpen}
        onOpenChange={(open) => {
          setAssignOpen(open);
          if (!open) {
            setAssignTeamId("");
            setAssignRole("user");
            setAssignSearch("");
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("addToTeamTitle")}</DialogTitle>
            <DialogDescription>
              <span className="font-medium">{user.user_id}</span> {t("addToTeamDescription")}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <Label htmlFor="team-search">{t("teamSearchLabel")}</Label>
              <Input
                id="team-search"
                placeholder={t("teamSearchPlaceholder")}
                value={assignSearch}
                onChange={(e) => setAssignSearch(e.target.value)}
              />
              <div className="max-h-60 overflow-y-auto rounded-md border">
                {(() => {
                  const memberTeamIds = new Set(teams.map((t_) => t_.team_id));
                  const q = assignSearch.trim().toLowerCase();
                  const candidates = (allTeams ?? [])
                    .filter((t_) => !memberTeamIds.has(t_.team_id))
                    .filter((t_) => {
                      if (!q) return true;
                      return (
                        t_.team_id.toLowerCase().includes(q) ||
                        (t_.team_alias ?? "").toLowerCase().includes(q)
                      );
                    });
                  if (candidates.length === 0) {
                    return (
                      <div className="p-4 text-sm text-muted-foreground text-center">
                        {t("noAvailableTeams")}
                      </div>
                    );
                  }
                  return candidates.map((t_) => (
                    <button
                      key={t_.team_id}
                      type="button"
                      onClick={() => setAssignTeamId(t_.team_id)}
                      className={`w-full text-left px-3 py-2 text-sm hover:bg-muted/50 border-b last:border-b-0 ${
                        assignTeamId === t_.team_id ? "bg-muted" : ""
                      }`}
                    >
                      <div className="font-medium">
                        {t_.team_alias || t_.team_id}
                      </div>
                      {t_.team_alias && (
                        <div className="font-mono text-xs text-muted-foreground">
                          {t_.team_id}
                        </div>
                      )}
                    </button>
                  ));
                })()}
              </div>
            </div>
            <div className="space-y-2">
              <Label>{t("roleLabel")}</Label>
              <div className="flex gap-2">
                <Button
                  type="button"
                  variant={assignRole === "user" ? "default" : "outline"}
                  size="sm"
                  onClick={() => setAssignRole("user")}
                >
                  {t("badgeMember")}
                </Button>
                <Button
                  type="button"
                  variant={assignRole === "admin" ? "default" : "outline"}
                  size="sm"
                  onClick={() => setAssignRole("admin")}
                >
                  {t("badgeTeamAdmin")}
                </Button>
              </div>
            </div>
          </div>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline">{tc("cancel")}</Button>
            </DialogClose>
            <Button
              onClick={handleAssignToTeam}
              disabled={!assignTeamId || assignMutation.isPending}
            >
              {assignMutation.isPending && (
                <Loader2 className="size-4 mr-1 animate-spin" />
              )}
              {t("addButton")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={!!removingTeam}
        onOpenChange={(open) => !open && setRemovingTeam(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("forceRemoveTitle")}</DialogTitle>
            <DialogDescription>
              <span className="font-medium">{user.user_id}</span>
              {" "}{t("forceRemoveDescriptionPre")}{" "}
              <span className="font-medium">
                {removingTeam?.team_alias || removingTeam?.team_id}
              </span>
              {" "}{t("forceRemoveDescriptionPost")}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline">{tc("cancel")}</Button>
            </DialogClose>
            <Button
              variant="destructive"
              onClick={handleRemoveFromTeam}
              disabled={removeFromTeamMutation.isPending}
            >
              {removeFromTeamMutation.isPending && (
                <Loader2 className="size-4 mr-1 animate-spin" />
              )}
              {t("forceRemoveButton")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
