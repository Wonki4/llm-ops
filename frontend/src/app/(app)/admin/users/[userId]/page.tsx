"use client";

import { use, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
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

function formatBudget(value: number | null, unlimitedLabel: string): string {
  if (value == null) return unlimitedLabel;
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
  const t = useTranslations("adminUserDetail");
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
      toast.error(t("errorInvalidLimits"));
      return;
    }
    updateLimitsMutation.mutate(
      { userId, token: editingKey.token, tpmLimit: tpm, rpmLimit: rpm },
      {
        onSuccess: () => {
          toast.success(t("toastLimitsSaved"));
          setEditingKey(null);
        },
        onError: (err) =>
          toast.error(err instanceof Error ? err.message : t("errorLimitsSave")),
      },
    );
  };

  const handleRemoveFromTeam = () => {
    if (!removingTeam) return;
    removeFromTeamMutation.mutate(
      { userId, teamId: removingTeam.team_id },
      {
        onSuccess: () => {
          toast.success(t("toastRemoved"));
          setRemovingTeam(null);
        },
        onError: (err) =>
          toast.error(err instanceof Error ? err.message : t("errorRemove")),
      },
    );
  };

  const handleAssignToTeam = () => {
    if (!assignTeamId) {
      toast.error(t("errorSelectTeam"));
      return;
    }
    assignMutation.mutate(
      { userId, teamId: assignTeamId, role: assignRole },
      {
        onSuccess: () => {
          toast.success(t("toastAssigned"));
          setAssignOpen(false);
          setAssignTeamId("");
          setAssignRole("user");
          setAssignSearch("");
        },
        onError: (err) =>
          toast.error(err instanceof Error ? err.message : t("errorAssign")),
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
            {t("back")}
          </Button>
        </Link>
        <div className="rounded-lg border border-dashed p-8 text-center">
          <p className="text-muted-foreground">
            {error instanceof Error ? error.message : t("loadError")}
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
            {t("back")}
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
              {t("roleAdmin")}
            </Badge>
          ) : (
            <Badge variant="outline">{t("roleUser")}</Badge>
          )}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              {t("cardTotalSpend")}
            </CardTitle>
            <DollarSign className="size-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold tabular-nums">
              ${user.spend.toFixed(2)}
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              {t("cardBudgetHint", { value: formatBudget(user.max_budget, t("unlimited")) })}
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              {t("cardKeys")}
            </CardTitle>
            <KeyIcon className="size-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{t("cardKeysCount", { count: keys.length })}</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              {t("cardTeams")}
            </CardTitle>
            <UsersIcon className="size-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{t("cardTeamsCount", { count: teams.length })}</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              {t("cardCreated")}
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
            {t("assignBtn")}
          </Button>
        </div>

        <TabsContent value="teams" className="space-y-2">
          {teams.length === 0 ? (
            <div className="rounded-lg border border-dashed p-8 text-center">
              <p className="text-sm text-muted-foreground">{t("teamsEmpty")}</p>
            </div>
          ) : (
            <div className="rounded-lg border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("colTeam")}</TableHead>
                    <TableHead>{t("colRole")}</TableHead>
                    <TableHead className="text-right">{t("colSpend")}</TableHead>
                    <TableHead className="text-right">{t("colBudget")}</TableHead>
                    <TableHead>{t("colExpiry")}</TableHead>
                    <TableHead className="w-16 text-right" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {teams.map((team) => (
                    <TableRow key={team.team_id}>
                      <TableCell>
                        <Link
                          href={`/teams/${team.team_id}`}
                          className="hover:underline"
                        >
                          <span className="font-medium">
                            {team.team_alias || team.team_id}
                          </span>
                          {team.team_alias && (
                            <span className="block font-mono text-xs text-muted-foreground">
                              {team.team_id}
                            </span>
                          )}
                        </Link>
                      </TableCell>
                      <TableCell>
                        {team.is_admin ? (
                          <Badge className="bg-blue-100 text-blue-700 hover:bg-blue-100">
                            {t("badgeTeamAdmin")}
                          </Badge>
                        ) : (
                          <Badge variant="outline">{t("badgeMember")}</Badge>
                        )}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        ${team.spend.toFixed(2)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-muted-foreground">
                        {formatBudget(team.max_budget, t("unlimited"))}
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        {formatDateShort(team.expires_at)}
                        {team.expiry_status && team.expiry_status !== "active" && (
                          <Badge variant="outline" className="ml-2 text-xs">
                            {team.expiry_status}
                          </Badge>
                        )}
                      </TableCell>
                      <TableCell className="text-right">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="text-destructive hover:text-destructive"
                          onClick={() => setRemovingTeam(team)}
                        >
                          <UserMinus className="size-3.5 mr-1" />
                          {t("removeBtn")}
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
              <p className="text-sm text-muted-foreground">{t("keysEmpty")}</p>
            </div>
          ) : (
            <div className="rounded-lg border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("colAlias")}</TableHead>
                    <TableHead>{t("colTeam")}</TableHead>
                    <TableHead>{t("colModels")}</TableHead>
                    <TableHead className="text-right">{t("colSpend")}</TableHead>
                    <TableHead className="text-right">{t("colBudget")}</TableHead>
                    <TableHead className="text-right">{t("colTpm")}</TableHead>
                    <TableHead className="text-right">{t("colRpm")}</TableHead>
                    <TableHead>{t("colKeyCreated")}</TableHead>
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
                          <span className="text-xs text-muted-foreground">{t("modelsAll")}</span>
                        ) : (
                          <div className="flex flex-wrap gap-1">
                            {k.models.slice(0, 3).map((m) => (
                              <Badge key={m} variant="outline" className="text-xs">
                                {m}
                              </Badge>
                            ))}
                            {k.models.length > 3 && (
                              <Badge variant="outline" className="text-xs">
                                {t("modelsMore", { count: k.models.length - 3 })}
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
                          {t("editBtn")}
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
            <DialogTitle>{t("limitsDialogTitle")}</DialogTitle>
            <DialogDescription>
              {t("limitsDialogDescription", {
                name: editingKey?.key_alias || editingKey?.key_name || editingKey?.token.slice(0, 12) || "",
              })}
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
              <Button variant="outline">{t("limitsCancel")}</Button>
            </DialogClose>
            <Button
              onClick={handleSaveLimits}
              disabled={updateLimitsMutation.isPending}
            >
              {updateLimitsMutation.isPending && (
                <Loader2 className="size-4 mr-1 animate-spin" />
              )}
              {t("limitsSave")}
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
            <DialogTitle>{t("assignDialogTitle")}</DialogTitle>
            <DialogDescription>
              {t("assignDialogDescription", { user: user.user_id })}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <Label htmlFor="team-search">{t("assignSearchLabel")}</Label>
              <Input
                id="team-search"
                placeholder={t("assignSearchPlaceholder")}
                value={assignSearch}
                onChange={(e) => setAssignSearch(e.target.value)}
              />
              <div className="max-h-60 overflow-y-auto rounded-md border">
                {(() => {
                  const memberTeamIds = new Set(teams.map((team) => team.team_id));
                  const q = assignSearch.trim().toLowerCase();
                  const candidates = (allTeams ?? [])
                    .filter((team) => !memberTeamIds.has(team.team_id))
                    .filter((team) => {
                      if (!q) return true;
                      return (
                        team.team_id.toLowerCase().includes(q) ||
                        (team.team_alias ?? "").toLowerCase().includes(q)
                      );
                    });
                  if (candidates.length === 0) {
                    return (
                      <div className="p-4 text-sm text-muted-foreground text-center">
                        {t("assignEmpty")}
                      </div>
                    );
                  }
                  return candidates.map((team) => (
                    <button
                      key={team.team_id}
                      type="button"
                      onClick={() => setAssignTeamId(team.team_id)}
                      className={`w-full text-left px-3 py-2 text-sm hover:bg-muted/50 border-b last:border-b-0 ${
                        assignTeamId === team.team_id ? "bg-muted" : ""
                      }`}
                    >
                      <div className="font-medium">
                        {team.team_alias || team.team_id}
                      </div>
                      {team.team_alias && (
                        <div className="font-mono text-xs text-muted-foreground">
                          {team.team_id}
                        </div>
                      )}
                    </button>
                  ));
                })()}
              </div>
            </div>
            <div className="space-y-2">
              <Label>{t("assignRoleLabel")}</Label>
              <div className="flex gap-2">
                <Button
                  type="button"
                  variant={assignRole === "user" ? "default" : "outline"}
                  size="sm"
                  onClick={() => setAssignRole("user")}
                >
                  {t("assignMember")}
                </Button>
                <Button
                  type="button"
                  variant={assignRole === "admin" ? "default" : "outline"}
                  size="sm"
                  onClick={() => setAssignRole("admin")}
                >
                  {t("assignTeamAdmin")}
                </Button>
              </div>
            </div>
          </div>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline">{t("assignCancel")}</Button>
            </DialogClose>
            <Button
              onClick={handleAssignToTeam}
              disabled={!assignTeamId || assignMutation.isPending}
            >
              {assignMutation.isPending && (
                <Loader2 className="size-4 mr-1 animate-spin" />
              )}
              {t("assignBtnConfirm")}
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
            <DialogTitle>{t("removeDialogTitle")}</DialogTitle>
            <DialogDescription>
              {t("removeDialogDescription", {
                user: user.user_id,
                team: removingTeam?.team_alias || removingTeam?.team_id || "",
              })}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline">{t("removeCancel")}</Button>
            </DialogClose>
            <Button
              variant="destructive"
              onClick={handleRemoveFromTeam}
              disabled={removeFromTeamMutation.isPending}
            >
              {removeFromTeamMutation.isPending && (
                <Loader2 className="size-4 mr-1 animate-spin" />
              )}
              {t("removeConfirm")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
