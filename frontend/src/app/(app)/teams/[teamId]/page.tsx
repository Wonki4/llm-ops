"use client";

import { Fragment, use, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useLocaleTag, parseServerDate } from "@/lib/locale";
import { useTeamDetail, useTeamMembers, useTeamUsage, useDeleteKey, useRevealKey, useModels, useChangeMemberRole, useChangeMemberBudget, useSetMemberExpiry, useRemoveTeamMember, useCreateBudgetRequest, useUpdateTeamSettings, useUpdateMemberKeyLimits, usePortalSettings } from "@/hooks/use-api";
import { toast } from "sonner";
import { InputTokens } from "@/components/input-tokens";
import { MemberModelUsage } from "@/components/member-model-usage";
import { presetRange, type UsagePreset } from "@/lib/usage";
import { ModelDetailSheet } from "@/components/model-detail-sheet";
import { ModelIcon } from "@/components/model-icon";
import { ModelTable, type ModelTableRow } from "@/components/model-table";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
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
import { Separator } from "@/components/ui/separator";
import {
  ArrowLeft,
  ArrowRight,
  Shield,
  Trash2,
  Plus,
  AlertCircle,
  RefreshCw,
  Loader2,
  Boxes,
  Key,
  Users,
  DollarSign,
  Eye,
  Zap,
  Globe,
  ChevronRight,
  Save,
  Copy,
  Check,
} from "lucide-react";
import type { ApiKey, TeamMember, ModelWithCatalog, ModelStatus } from "@/types";

const STATUS_STYLES: Record<ModelStatus, string> = {
  testing: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
  prerelease: "bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400",
  lts: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400",
  deprecating: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400",
  deprecated: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",
};

function StatusBadge({ status }: { status: ModelStatus }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ${STATUS_STYLES[status]}`}
    >
      {status}
    </span>
  );
}

function formatBudget(spend: number, maxBudget: number | null, unlimitedLabel: string): string {
  const spendStr = `$${spend.toFixed(2)}`;
  if (maxBudget === null) return `${spendStr} / ${unlimitedLabel}`;
  return `${spendStr} / $${maxBudget.toFixed(2)}`;
}

function budgetPercent(spend: number, maxBudget: number | null): number {
  if (maxBudget === null || maxBudget === 0) return 0;
  return Math.min((spend / maxBudget) * 100, 100);
}

function formatDate(dateStr: string, localeTag: string): string {
  return parseServerDate(dateStr).toLocaleDateString(localeTag, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function formatResetDate(dateStr: string, localeTag: string): string {
  return parseServerDate(dateStr).toLocaleDateString(localeTag, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatBudgetDuration(duration: string | null, unitLabels: Record<string, string>): string {
  if (!duration) return "";
  const match = duration.match(/^(\d+)([dhms])$/);
  if (!match) return duration;
  const [, num, unit] = match;
  return `${num}${unitLabels[unit] || unit}`;
}

function maskKey(token: string): string {
  if (token.length <= 8) return token;
  return token.slice(0, 8) + "...";
}

function DeleteKeyDialog({
  keyItem,
  onDelete,
  isDeleting,
}: {
  keyItem: ApiKey;
  onDelete: (keyHash: string) => void;
  isDeleting: boolean;
}) {
  const t = useTranslations("teamDetail");
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
          <DialogTitle>{t("deleteKeyTitle")}</DialogTitle>
          <DialogDescription>
            &quot;{keyItem.key_alias || keyItem.token.slice(0, 8)}&quot; {t("deleteKeyDesc")}
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

function BudgetRequestDialog({ teamId, currentBudget }: { teamId: string; currentBudget: number | null }) {
  const t = useTranslations("teamDetail");
  const tc = useTranslations("common");
  const [open, setOpen] = useState(false);
  const [amount, setAmount] = useState("");
  const [message, setMessage] = useState("");
  const mutation = useCreateBudgetRequest();

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" className="w-full mt-2">
          <DollarSign className="size-3.5 mr-1" />
          {t("budgetRequestBtn")}
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("budgetRequestTitle")}</DialogTitle>
          <DialogDescription>
            {t("budgetRequestDesc")}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="rounded-md bg-muted p-3 text-sm">
            <div className="flex justify-between">
              <span className="text-muted-foreground">{t("currentBudget")}</span>
              <span className="font-medium">{currentBudget === null ? t("unlimited") : `$${currentBudget.toFixed(2)}`}</span>
            </div>
            {amount && Number(amount) > 0 && (
              <div className="flex justify-between mt-1 pt-1 border-t border-border">
                <span className="text-muted-foreground">{t("newBudget")}</span>
                <span className="font-medium text-primary">${Number(amount).toFixed(2)}</span>
              </div>
            )}
          </div>
          <div>
            <label className="text-sm font-medium">{t("budgetAmountLabel")}</label>
            <Input
              type="number"
              step="0.01"
              min="0"
              placeholder={t("budgetAmountPlaceholder")}
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
            />
          </div>
          <div>
            <label className="text-sm font-medium">{t("budgetReasonLabel")}</label>
            <Input
              placeholder={t("budgetReasonPlaceholder")}
              value={message}
              onChange={(e) => setMessage(e.target.value)}
            />
          </div>
        </div>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="ghost">{tc("cancel")}</Button>
          </DialogClose>
          <Button
            disabled={!amount || Number(amount) <= 0 || mutation.isPending}
            onClick={() => {
              mutation.mutate(
                { team_id: teamId, requested_budget: Number(amount), message: message || undefined },
                {
                  onSuccess: () => {
                    toast.success(t("budgetRequestSuccess"));
                    setOpen(false);
                    setAmount("");
                    setMessage("");
                  },
                  onError: (err) => {
                    toast.error(err instanceof Error ? err.message : t("budgetRequestFail"));
                  },
                },
              );
            }}
          >
            {mutation.isPending ? <Loader2 className="size-4 animate-spin" /> : t("budgetRequestSubmit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function OverviewTab({
  team,
  isAdmin,
  myKeys,
  myMembership,
  modelsByName,
  onMoveToKeys,
  onMoveToModels,
  onSelectModel,
}: {
  team: {
    team_id: string;
    team_alias: string;
    spend: number;
    max_budget: number | null;
    budget_duration: string | null;
    budget_reset_at: string | null;
    models: string[];
    members: string[];
    admins: string[];
    member_count?: number;
    admin_count?: number;
  };
  isAdmin: boolean;
  myKeys: ApiKey[];
  myMembership: { spend: number; max_budget: number | null; budget_duration: string | null; budget_reset_at: string | null };
  modelsByName: Map<string, ModelWithCatalog>;
  onMoveToKeys: () => void;
  onMoveToModels: () => void;
  onSelectModel: (model: ModelWithCatalog) => void;
}) {
  const t = useTranslations("teamDetail");
  const localeTag = useLocaleTag();
  const unitLabels: Record<string, string> = {
    d: t("unitDay"),
    h: t("unitHour"),
    m: t("unitMinute"),
    s: t("unitSecond"),
  };

  const totalMembers = team.member_count ?? team.members.length;
  const totalAdmins = team.admin_count ?? team.admins.length;
  const pct = budgetPercent(team.spend, team.max_budget);
  const mySpend = myMembership.spend;
  const myMaxBudget = myMembership.max_budget;
  const myPct = budgetPercent(mySpend, myMaxBudget);
  const topKeys = [...myKeys].sort((a, b) => b.spend - a.spend).slice(0, 3);
  const hasAllProxyModels = team.models.includes("all-proxy-models");
  const catalogTeamModels = team.models
    .map((modelName) => ({ modelName, model: modelsByName.get(modelName) ?? null }))
    .filter(({ model }) => model?.catalog);
  const scopedModels = catalogTeamModels.slice(0, 5);
  const memberOnly = team.members.filter((member) => !team.admins.includes(member));
  const remainingAdmins = totalAdmins - team.admins.length;
  const remainingMembers = (totalMembers - totalAdmins) - memberOnly.length;

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">{t("cardTeamBudget")}</CardTitle>
            <DollarSign className="size-4 text-muted-foreground" />
          </CardHeader>
          <CardContent className="space-y-2">
            <div className="text-2xl font-bold">{formatBudget(team.spend, team.max_budget, t("unlimited"))}</div>
            <p className="text-xs text-muted-foreground">{t("teamTotalUsage")}</p>
            <div className="h-2 w-full rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-primary transition-all"
                style={{ width: `${team.max_budget === null ? 0 : pct}%` }}
              />
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">{t("cardMyUsage")}</CardTitle>
            <DollarSign className="size-4 text-muted-foreground" />
          </CardHeader>
          <CardContent className="space-y-2">
            <div className="text-2xl font-bold">{formatBudget(mySpend, myMaxBudget, t("unlimited"))}</div>
            <p className="text-xs text-muted-foreground">{t("myBudgetInTeam")}</p>
            <div className="h-2 w-full rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-blue-500 transition-all"
                style={{ width: `${myMaxBudget === null ? 0 : myPct}%` }}
              />
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">{t("cardMyKeys")}</CardTitle>
            <Key className="size-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{t("keyCount", { count: myKeys.length })}</div>
            <p className="text-xs text-muted-foreground">{t("createdApiKeys")}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">{t("cardModels")}</CardTitle>
            <Boxes className="size-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{hasAllProxyModels ? t("allModels") : t("modelCount", { count: catalogTeamModels.length })}</div>
            <p className="text-xs text-muted-foreground">{t("availableModels")}</p>
          </CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">{t("teamBudgetDetail")}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex items-end justify-between gap-3">
                <div>
                  <p className="text-sm text-muted-foreground">{t("teamTotalUsage")}</p>
                  <p className="text-2xl font-bold">{formatBudget(team.spend, team.max_budget, t("unlimited"))}</p>
                </div>
                <p className="text-sm font-medium text-muted-foreground">
                  {team.max_budget === null ? t("unlimited") : `${pct.toFixed(1)}%`}
                </p>
              </div>
              <div className="h-2 w-full rounded-full bg-muted">
                <div
                  className="h-full rounded-full bg-primary transition-all"
                  style={{ width: `${team.max_budget === null ? 0 : pct}%` }}
                />
              </div>
              <div className="space-y-1 text-xs text-muted-foreground">
                <p>{t("budgetCycle")}: {team.budget_duration ? t("budgetCycleValue", { duration: formatBudgetDuration(team.budget_duration, unitLabels) }) : "-"}</p>
                <p>{t("budgetReset")}: {team.budget_reset_at ? formatResetDate(team.budget_reset_at, localeTag) : "-"}</p>
              </div>
              <Separator />
              <div className="flex items-end justify-between gap-3">
                <div>
                  <p className="text-sm text-muted-foreground">{t("cardMyUsage")}</p>
                  <p className="text-2xl font-bold">{formatBudget(mySpend, myMaxBudget, t("unlimited"))}</p>
                </div>
                <p className="text-sm font-medium text-muted-foreground">
                  {myMaxBudget === null ? t("unlimited") : `${myPct.toFixed(1)}%`}
                </p>
              </div>
              <div className="h-2 w-full rounded-full bg-muted">
                <div
                  className="h-full rounded-full bg-blue-500 transition-all"
                  style={{ width: `${myMaxBudget === null ? 0 : myPct}%` }}
                />
              </div>
              <div className="space-y-1 text-xs text-muted-foreground">
                <p>{t("budgetCycle")}: {myMembership.budget_duration ? t("budgetCycleValue", { duration: formatBudgetDuration(myMembership.budget_duration, unitLabels) }) : "-"}</p>
                <p>{t("budgetReset")}: {myMembership.budget_reset_at ? formatResetDate(myMembership.budget_reset_at, localeTag) : "-"}</p>
              </div>
              <BudgetRequestDialog teamId={team.team_id} currentBudget={myMaxBudget} />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">{t("teamMemberInfo")}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-5">
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 text-sm font-medium">
                    <Shield className="size-4" />
                    {t("roleAdmin")}
                  </div>
                  <span className="text-xs text-muted-foreground">{t("memberCountLabel", { count: totalAdmins })}</span>
                </div>
                <div className="flex flex-wrap gap-2">
                  {team.admins.length === 0 ? (
                    <p className="text-sm text-muted-foreground">{t("noAdmins")}</p>
                  ) : (
                    <>
                      {team.admins.map((admin) => (
                        <Badge key={admin} variant="secondary" className="gap-1">
                          <Shield className="size-3" />
                          {admin}
                        </Badge>
                      ))}
                      {remainingAdmins > 0 && (
                        <Badge variant="outline" className="text-muted-foreground">
                          {t("andMore", { count: remainingAdmins })}
                        </Badge>
                      )}
                    </>
                  )}
              </div>
            </div>
              {isAdmin && <p className="text-xs text-muted-foreground">{t("adminActive")}</p>}
            </CardContent>
          </Card>
        </div>

        <div className="space-y-6">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle className="text-base">{t("myKeySummary")}</CardTitle>
              <Button variant="ghost" size="sm" className="h-8 px-2" onClick={onMoveToKeys}>
                {t("viewAll")}
                <ArrowRight className="size-4" />
              </Button>
            </CardHeader>
            <CardContent className="space-y-3">
              {topKeys.length === 0 ? (
                <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
                  {t("noKeysCreated")}{" "}
                  <Link className="underline underline-offset-4" href={`/keys/new?team_id=${team.team_id}`}>
                    {t("createKey")}
                  </Link>
                </div>
              ) : (
                topKeys.map((key) => (
                    <div key={key.token} className="rounded-lg border p-3">
                      <p className="text-sm font-medium">{key.key_alias || "-"}</p>
                    </div>
                  ))
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle className="text-base">{t("availableModelsCard")}</CardTitle>
              <Button variant="ghost" size="sm" className="h-8 px-2" onClick={onMoveToModels}>
                {t("viewAll")}
                <ChevronRight className="size-4" />
              </Button>
            </CardHeader>
            <CardContent className="space-y-2">
              {scopedModels.length === 0 ? (
                <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
                  {t("noModelsAssigned")}
                </div>
              ) : (
                scopedModels.map(({ modelName, model }) => (
                  <div key={modelName} className="flex items-start justify-between gap-3 rounded-lg border p-3">
                    <div className="flex min-w-0 items-start gap-2">
                      <ModelIcon
                        iconUrl={model?.catalog?.icon_url}
                        provider={model?.litellm_info?.model_info?.litellm_provider}
                        modelName={model?.model_name ?? modelName}
                        className="mt-0.5"
                      />
                      <div className="min-w-0 space-y-1">
                        {model ? (
                          <button
                            type="button"
                            onClick={() => onSelectModel(model)}
                            className="cursor-pointer text-left text-sm font-medium hover:underline"
                          >
                            {model.catalog?.display_name || model.model_name}
                          </button>
                        ) : (
                          <p className="text-sm font-medium">{modelName}</p>
                        )}
                        <div className="flex items-center gap-2 text-xs text-muted-foreground">
                          <Globe className="size-3.5" />
                          {model?.litellm_info?.model_info?.litellm_provider || "-"}
                        </div>
                      </div>
                    </div>
                    {model?.catalog ? <StatusBadge status={model.catalog.status} /> : null}
                  </div>
                ))
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

function TeamSettingsTab({
  teamId,
  description,
  defaultMemberBudget,
  defaultMemberTpm,
  defaultMemberRpm,
  membershipDuration,
  defaultTpmLimit,
  defaultRpmLimit,
}: {
  teamId: string;
  description: string | null;
  defaultMemberBudget: number | null;
  defaultMemberTpm: number | null;
  defaultMemberRpm: number | null;
  membershipDuration: string | null;
  defaultTpmLimit: number | null;
  defaultRpmLimit: number | null;
}) {
  const t = useTranslations("teamDetail");
  const tc = useTranslations("common");
  const updateSettings = useUpdateTeamSettings();
  const { data: portalSettings } = usePortalSettings();
  const [desc, setDesc] = useState(description || "");
  const [defaultBudget, setDefaultBudget] = useState(
    defaultMemberBudget != null ? String(defaultMemberBudget) : ""
  );
  const [memberTpm, setMemberTpm] = useState(defaultMemberTpm != null ? String(defaultMemberTpm) : "");
  const [memberRpm, setMemberRpm] = useState(defaultMemberRpm != null ? String(defaultMemberRpm) : "");
  const [duration, setDuration] = useState(membershipDuration || "");
  const [tpmLimit, setTpmLimit] = useState(defaultTpmLimit != null ? String(defaultTpmLimit) : "");
  const [rpmLimit, setRpmLimit] = useState(defaultRpmLimit != null ? String(defaultRpmLimit) : "");

  const handleSave = () => {
    updateSettings.mutate(
      {
        teamId,
        body: {
          description: desc.trim() || null,
          default_member_budget: defaultBudget ? Number(defaultBudget) : null,
          default_member_tpm_limit: memberTpm.trim() === "" ? null : Number(memberTpm),
          default_member_rpm_limit: memberRpm.trim() === "" ? null : Number(memberRpm),
          membership_duration: duration || null,
          default_tpm_limit: tpmLimit ? Number(tpmLimit) : null,
          default_rpm_limit: rpmLimit ? Number(rpmLimit) : null,
        },
      },
      {
        onSuccess: () => toast.success(t("settingsSaved")),
        onError: (err) => toast.error(err instanceof Error ? err.message : t("settingsSaveFail")),
      },
    );
  };

  return (
    <div className="space-y-6 max-w-2xl">
      <h2 className="text-lg font-semibold">{t("settingsTitle")}</h2>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("settingsDescriptionCard")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <textarea
              value={desc}
              onChange={(e) => setDesc(e.target.value)}
              placeholder={t("settingsDescriptionPlaceholder")}
              rows={3}
              maxLength={500}
              className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none resize-y"
            />
            <p className="text-xs text-muted-foreground">{t("settingsDescriptionHint")}</p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("settingsDefaultBudgetCard")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <label className="text-sm font-medium">{t("settingsDefaultBudgetLabel")}</label>
            <input
              type="number"
              step="0.01"
              value={defaultBudget}
              onChange={(e) => setDefaultBudget(e.target.value)}
              placeholder={t("settingsDefaultBudgetPlaceholder")}
              className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
            />
            <p className="text-xs text-muted-foreground">
              {t("settingsDefaultBudgetHint")}
            </p>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">{t("settingsMemberTpmLabel")}</label>
              <input
                type="number"
                min="0"
                step="1"
                value={memberTpm}
                onChange={(e) => setMemberTpm(e.target.value)}
                placeholder={t("settingsMemberRateLimitPlaceholder")}
                className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">{t("settingsMemberRpmLabel")}</label>
              <input
                type="number"
                min="0"
                step="1"
                value={memberRpm}
                onChange={(e) => setMemberRpm(e.target.value)}
                placeholder={t("settingsMemberRateLimitPlaceholder")}
                className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
              />
            </div>
          </div>
          <p className="text-xs text-muted-foreground">{t("settingsMemberRateLimitHint")}</p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("settingsTpmRpmCard")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">TPM (tokens/min)</label>
              <input
                type="number"
                min="0"
                step="1"
                value={tpmLimit}
                onChange={(e) => setTpmLimit(e.target.value)}
                placeholder={t("settingsGlobalDefaultPlaceholder")}
                className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">RPM (requests/min)</label>
              <input
                type="number"
                min="0"
                step="1"
                value={rpmLimit}
                onChange={(e) => setRpmLimit(e.target.value)}
                placeholder={t("settingsGlobalDefaultPlaceholder")}
                className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
              />
            </div>
          </div>
          <p className="text-xs text-muted-foreground">
            {t("settingsTpmRpmHint")}
            {portalSettings
              ? ` (TPM ${portalSettings.default_tpm_limit?.toLocaleString() ?? "-"} / RPM ${portalSettings.default_rpm_limit?.toLocaleString() ?? "-"})`
              : ""}
            {t("settingsTpmRpmHintSuffix")}
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("settingsMembershipDurationCard")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <label className="text-sm font-medium">{t("settingsMembershipDurationLabel")}</label>
            <input
              type="text"
              value={duration}
              onChange={(e) => setDuration(e.target.value)}
              placeholder={t("settingsMembershipDurationPlaceholder")}
              className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
            />
            <p className="text-xs text-muted-foreground">
              {t("settingsMembershipDurationHint")}
            </p>
          </div>
        </CardContent>
      </Card>

      <Button onClick={handleSave} disabled={updateSettings.isPending}>
        {updateSettings.isPending ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
        {tc("save")}
      </Button>
    </div>
  );
}

function UsageTab({ teamId }: { teamId: string }) {
  const t = useTranslations("teamDetail");
  const localeTag = useLocaleTag();
  const [preset, setPreset] = useState<UsagePreset>("30d");
  const initial = presetRange("30d")!;
  const [startDate, setStartDate] = useState(initial.start);
  const [endDate, setEndDate] = useState(initial.end);
  const [sortField, setSortField] = useState<"user_id" | "total_tokens" | "api_requests" | "spend">("spend");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [expanded, setExpanded] = useState<string | null>(null);

  const applyPreset = (p: UsagePreset) => {
    setPreset(p);
    const r = presetRange(p);
    if (r) {
      setStartDate(r.start);
      setEndDate(r.end);
    }
  };

  const granularity: "day" | "month" = preset === "month" ? "month" : "day";
  const { data, isLoading } = useTeamUsage(teamId, startDate, endDate, granularity, sortField, sortDir);

  const toggleSort = (field: typeof sortField) => {
    if (sortField === field) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortField(field);
      setSortDir("desc");
    }
  };
  const sortMark = (field: typeof sortField) =>
    sortField === field ? (sortDir === "asc" ? " ↑" : " ↓") : "";

  const PRESETS: { value: UsagePreset; label: string }[] = [
    { value: "today", label: t("usagePresetToday") },
    { value: "7d", label: t("usagePreset7d") },
    { value: "month", label: t("usagePresetMonth") },
    { value: "30d", label: t("usagePreset30d") },
  ];

  return (
    <div className="space-y-4">
      {/* Filters */}
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex gap-1">
          {PRESETS.map((p) => (
            <Button
              key={p.value}
              variant={preset === p.value ? "default" : "outline"}
              size="sm"
              onClick={() => applyPreset(p.value)}
            >
              {p.label}
            </Button>
          ))}
        </div>
        <div className="flex items-end gap-2">
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">{t("usageStart")}</label>
            <Input
              type="date"
              value={startDate}
              max={endDate}
              className="h-9 w-[150px]"
              onChange={(e) => {
                setPreset("custom");
                setStartDate(e.target.value);
              }}
            />
          </div>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">{t("usageEnd")}</label>
            <Input
              type="date"
              value={endDate}
              min={startDate}
              className="h-9 w-[150px]"
              onChange={(e) => {
                setPreset("custom");
                setEndDate(e.target.value);
              }}
            />
          </div>
        </div>
      </div>

      {/* Totals */}
      {data && (
        <div className="grid grid-cols-4 gap-3">
          <div className="rounded-lg border p-3">
            <div className="text-xs text-muted-foreground">{t("colRequests")}</div>
            <div className="text-xl font-bold tabular-nums">{data.totals.api_requests.toLocaleString(localeTag)}</div>
          </div>
          <div className="rounded-lg border p-3">
            <div className="text-xs text-muted-foreground">{t("colInput")}</div>
            <div className="text-xl font-bold">
              <InputTokens input={data.totals.input_tokens} cacheRead={data.totals.cache_read_tokens} />
            </div>
          </div>
          <div className="rounded-lg border p-3">
            <div className="text-xs text-muted-foreground">{t("colOutput")}</div>
            <div className="text-xl font-bold tabular-nums">{data.totals.output_tokens.toLocaleString(localeTag)}</div>
          </div>
          <div className="rounded-lg border p-3">
            <div className="text-xs text-muted-foreground">{t("colUsage")}</div>
            <div className="text-xl font-bold tabular-nums">${data.totals.spend.toFixed(2)}</div>
          </div>
        </div>
      )}

      {/* Per-user table */}
      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="size-5 animate-spin text-muted-foreground" />
        </div>
      ) : !data || data.members.length === 0 ? (
        <div className="rounded-md border border-dashed p-8 text-center text-sm text-muted-foreground">
          {t("usageEmpty")}
        </div>
      ) : (
        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("colUserId")}</TableHead>
                <TableHead className="text-right">
                  <button type="button" className="hover:text-foreground" onClick={() => toggleSort("api_requests")}>
                    {t("colRequests")}{sortMark("api_requests")}
                  </button>
                </TableHead>
                <TableHead className="text-right">
                  <button type="button" className="hover:text-foreground" onClick={() => toggleSort("total_tokens")}>
                    {t("colInput")}{sortMark("total_tokens")}
                  </button>
                </TableHead>
                <TableHead className="text-right">{t("colOutput")}</TableHead>
                <TableHead className="text-right">
                  <button type="button" className="hover:text-foreground" onClick={() => toggleSort("spend")}>
                    {t("colUsage")}{sortMark("spend")}
                  </button>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.members.map((m) => {
                const isOpen = expanded === m.user_id;
                return (
                  <Fragment key={m.user_id}>
                    <TableRow
                      className="cursor-pointer hover:bg-muted/50"
                      onClick={() => setExpanded(isOpen ? null : m.user_id)}
                    >
                      <TableCell className="font-medium">
                        <span className="inline-flex items-center gap-1">
                          <ChevronRight className={`size-3.5 transition-transform ${isOpen ? "rotate-90" : ""}`} />
                          {m.user_id}
                        </span>
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{m.api_requests.toLocaleString(localeTag)}</TableCell>
                      <TableCell className="text-right">
                        <InputTokens input={m.input_tokens} cacheRead={m.cache_read_tokens} />
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{m.output_tokens.toLocaleString(localeTag)}</TableCell>
                      <TableCell className="text-right tabular-nums">${m.spend.toFixed(2)}</TableCell>
                    </TableRow>
                    {isOpen && (
                      <TableRow className="hover:bg-transparent">
                        <TableCell colSpan={5} className="bg-muted/30 p-0">
                          <MemberModelUsage
                            teamId={teamId}
                            userId={m.user_id}
                            startDate={startDate}
                            endDate={endDate}
                          />
                        </TableCell>
                      </TableRow>
                    )}
                  </Fragment>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}

function MembersTab({ teamId }: { teamId: string }) {
  const t = useTranslations("teamDetail");
  const tc = useTranslations("common");
  const localeTag = useLocaleTag();
  const [page, setPage] = useState(1);
  const pageSize = 50;
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const changeRoleMutation = useChangeMemberRole();
  const changeBudgetMutation = useChangeMemberBudget();
  const setExpiryMutation = useSetMemberExpiry();
  const removeMemberMutation = useRemoveTeamMember();
  const updateKeyLimitsMutation = useUpdateMemberKeyLimits();
  const [keyLimitsTarget, setKeyLimitsTarget] = useState<{
    userId: string;
    token: string;
    keyAlias: string | null;
    currentTpm: number | null;
    currentRpm: number | null;
  } | null>(null);
  const [tpmInput, setTpmInput] = useState("");
  const [rpmInput, setRpmInput] = useState("");
  const [roleChangeTarget, setRoleChangeTarget] = useState<{ userId: string; currentIsAdmin: boolean } | null>(null);
  const [budgetChangeTarget, setBudgetChangeTarget] = useState<{ userId: string; currentBudget: number | null; currentTpm: number | null; currentRpm: number | null } | null>(null);
  const [budgetAmount, setBudgetAmount] = useState("");
  const [budgetTpm, setBudgetTpm] = useState("");
  const [budgetRpm, setBudgetRpm] = useState("");
  const [expiryTarget, setExpiryTarget] = useState<{ userId: string; currentExpiry: string | null } | null>(null);
  const [expiryDate, setExpiryDate] = useState("");
  const [sortField, setSortField] = useState<"user_id" | "spend" | "budget" | "key_count">("user_id");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");

  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch(searchInput);
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [searchInput]);

  const { data, isLoading } = useTeamMembers(teamId, page, pageSize, search, sortField, sortDir);

  const toggleExpand = (userId: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(userId)) next.delete(userId);
      else next.add(userId);
      return next;
    });
  };

  const totalPages = data ? Math.ceil(data.total / pageSize) : 0;

  const toggleSort = (field: "spend" | "budget" | "key_count") => {
    if (sortField === field) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortField(field);
      setSortDir("desc");
    }
    setPage(1);
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">{t("membersTitle")}</h2>
        <Input
          placeholder={t("membersSearchPlaceholder")}
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          className="w-48"
        />
      </div>

      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="h-12 animate-pulse rounded bg-muted" />
          ))}
        </div>
      ) : !data || data.members.length === 0 ? (
        <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed p-8">
          <Users className="size-8 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">
            {search ? t("membersEmptySearch") : t("membersEmpty")}
          </p>
        </div>
      ) : (
        <>
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-10" />
                  <TableHead>{t("colUserId")}</TableHead>
                  <TableHead>{t("colRole")}</TableHead>
                  <TableHead className="hidden sm:table-cell">
                    <button type="button" className="flex items-center gap-1 hover:text-foreground" onClick={() => toggleSort("key_count")}>
                      {t("colKeyCount")} {sortField === "key_count" ? (sortDir === "asc" ? "↑" : "↓") : ""}
                    </button>
                  </TableHead>
                  <TableHead>
                    <button type="button" className="flex items-center gap-1 hover:text-foreground" onClick={() => toggleSort("spend")}>
                      {t("colUsage")} {sortField === "spend" ? (sortDir === "asc" ? "↑" : "↓") : ""}
                    </button>
                  </TableHead>
                  <TableHead className="hidden sm:table-cell">
                    <button type="button" className="flex items-center gap-1 hover:text-foreground" onClick={() => toggleSort("budget")}>
                      {t("colBudget")} {sortField === "budget" ? (sortDir === "asc" ? "↑" : "↓") : ""}
                    </button>
                  </TableHead>
                  <TableHead className="hidden md:table-cell">{t("colRateLimit")}</TableHead>
                  <TableHead className="hidden md:table-cell">{t("colExpiry")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.members.map((member: TeamMember) => {
                  const isExpanded = expanded.has(member.user_id);
                  const pct = budgetPercent(member.total_spend, member.total_max_budget);
                  return (
                    <Fragment key={member.user_id}>
                      <TableRow
                        className={member.key_count > 0 ? "cursor-pointer" : ""}
                        onClick={() => member.key_count > 0 && toggleExpand(member.user_id)}
                      >
                        <TableCell>
                          {member.key_count > 0 && (
                            <ChevronRight
                              className={`size-4 transition-transform ${isExpanded ? "rotate-90" : ""}`}
                            />
                          )}
                        </TableCell>
                        <TableCell className="font-medium">{member.user_id}</TableCell>
                        <TableCell>
                          <div className="flex items-center gap-2">
                            {member.is_admin ? (
                              <Badge variant="default" className="gap-1">
                                <Shield className="size-3" />
                                {t("roleAdmin")}
                              </Badge>
                            ) : (
                              <Badge variant="outline">{t("roleMember")}</Badge>
                            )}
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-6 px-2 text-xs text-muted-foreground"
                              disabled={changeRoleMutation.isPending}
                              onClick={(e) => {
                                e.stopPropagation();
                                setRoleChangeTarget({ userId: member.user_id, currentIsAdmin: member.is_admin });
                              }}
                            >
                              {member.is_admin ? t("demoteToMember") : t("promoteToAdmin")}
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-6 px-2 text-xs text-destructive hover:text-destructive"
                              disabled={removeMemberMutation.isPending}
                              onClick={(e) => {
                                e.stopPropagation();
                                if (!confirm(t("confirmRemoveMember", { userId: member.user_id }))) return;
                                removeMemberMutation.mutate(
                                  { teamId, userId: member.user_id },
                                  {
                                    onSuccess: () => toast.success(t("removeMemberSuccess", { userId: member.user_id })),
                                    onError: (err) => toast.error(err instanceof Error ? err.message : t("removeMemberFail")),
                                  },
                                );
                              }}
                            >
                              <Trash2 className="size-3 mr-0.5" />
                              {tc("delete")}
                            </Button>
                          </div>
                        </TableCell>
                        <TableCell className="hidden sm:table-cell">{t("keyCount", { count: member.key_count })}</TableCell>
                        <TableCell>
                          <div className="flex items-center gap-2">
                            <div className="space-y-1">
                              <span className="text-sm">{formatBudget(member.total_spend, member.total_max_budget, t("unlimited"))}</span>
                              <div className="h-1.5 w-24 rounded-full bg-muted">
                                <div
                                  className="h-full rounded-full bg-primary transition-all"
                                  style={{ width: `${member.total_max_budget === null ? 0 : pct}%` }}
                                />
                              </div>
                            </div>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-6 px-2 text-xs text-muted-foreground"
                              onClick={(e) => {
                                e.stopPropagation();
                                setBudgetChangeTarget({ userId: member.user_id, currentBudget: member.total_max_budget, currentTpm: member.total_tpm_limit, currentRpm: member.total_rpm_limit });
                                setBudgetAmount(member.total_max_budget != null ? String(member.total_max_budget) : "");
                                setBudgetTpm(member.total_tpm_limit != null ? String(member.total_tpm_limit) : "");
                                setBudgetRpm(member.total_rpm_limit != null ? String(member.total_rpm_limit) : "");
                              }}
                            >
                              {t("actionChange")}
                            </Button>
                          </div>
                        </TableCell>
                        <TableCell className="hidden sm:table-cell">
                          <span className="text-sm text-muted-foreground">
                            {member.total_max_budget === null ? t("unlimited") : `$${member.total_max_budget.toFixed(2)}`}
                          </span>
                        </TableCell>
                        <TableCell className="hidden md:table-cell">
                          <span className="text-sm text-muted-foreground">
                            {member.total_tpm_limit == null && member.total_rpm_limit == null
                              ? t("unlimited")
                              : `${member.total_tpm_limit?.toLocaleString() ?? "∞"} / ${member.total_rpm_limit?.toLocaleString() ?? "∞"}`}
                          </span>
                        </TableCell>
                        <TableCell className="hidden md:table-cell">
                          <div className="flex items-center gap-2">
                            <span className="text-sm text-muted-foreground">
                              {member.expires_at
                                ? new Date(member.expires_at).toLocaleDateString(localeTag)
                                : t("indefinite")}
                            </span>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-6 px-2 text-xs text-muted-foreground"
                              onClick={(e) => {
                                e.stopPropagation();
                                setExpiryTarget({ userId: member.user_id, currentExpiry: member.expires_at ?? null });
                                setExpiryDate(member.expires_at ? member.expires_at.split("T")[0] : "");
                              }}
                            >
                              {t("actionSet")}
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                      {isExpanded && member.keys.length > 0 && (
                        <TableRow>
                          <TableCell colSpan={8} className="bg-muted/30 p-0">
                            <div className="space-y-2 px-8 py-3">
                              {member.keys.map((key) => (
                                <div
                                  key={key.token}
                                  className="flex items-center justify-between gap-4 rounded-lg border bg-background p-3"
                                >
                                  <div className="min-w-0 space-y-0.5">
                                    <p className="text-sm font-medium">
                                      {key.key_alias || "-"}
                                    </p>
                                    <p className="font-mono text-xs text-muted-foreground">
                                      {maskKey(key.token)}
                                    </p>
                                  </div>
                                  <div className="flex items-center gap-3">
                                    <div className="flex gap-3 text-right text-xs">
                                      <div>
                                        <div className="text-[10px] uppercase tracking-wide text-muted-foreground">TPM</div>
                                        <div className="font-medium tabular-nums">
                                          {key.tpm_limit != null ? key.tpm_limit.toLocaleString() : t("unlimited")}
                                        </div>
                                      </div>
                                      <div>
                                        <div className="text-[10px] uppercase tracking-wide text-muted-foreground">RPM</div>
                                        <div className="font-medium tabular-nums">
                                          {key.rpm_limit != null ? key.rpm_limit.toLocaleString() : t("unlimited")}
                                        </div>
                                      </div>
                                    </div>
                                    <Button
                                      variant="ghost"
                                      size="sm"
                                      className="h-7 px-2 text-xs text-muted-foreground"
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        setKeyLimitsTarget({
                                          userId: member.user_id,
                                          token: key.token,
                                          keyAlias: key.key_alias,
                                          currentTpm: key.tpm_limit,
                                          currentRpm: key.rpm_limit,
                                        });
                                        setTpmInput(key.tpm_limit != null ? String(key.tpm_limit) : "");
                                        setRpmInput(key.rpm_limit != null ? String(key.rpm_limit) : "");
                                      }}
                                    >
                                      {t("actionEdit")}
                                    </Button>
                                  </div>
                                </div>
                              ))}
                            </div>
                          </TableCell>
                        </TableRow>
                      )}
                    </Fragment>
                  );
                })}
              </TableBody>
            </Table>
          </div>

          <div className="flex items-center justify-between">
            <p className="text-sm text-muted-foreground">
              {t("pagination", {
                total: data.total.toLocaleString(),
                from: ((page - 1) * pageSize + 1).toLocaleString(),
                to: Math.min(page * pageSize, data.total).toLocaleString(),
              })}
            </p>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                <ArrowLeft className="size-4" />
                {t("pagePrev")}
              </Button>
              <span className="text-sm text-muted-foreground">
                {page} / {totalPages}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={page >= totalPages}
                onClick={() => setPage((p) => p + 1)}
              >
                {t("pageNext")}
                <ArrowRight className="size-4" />
              </Button>
            </div>
          </div>
        </>
      )}

      {/* Role Change Confirmation Dialog */}
      <Dialog open={!!roleChangeTarget} onOpenChange={(open) => !open && setRoleChangeTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("roleChangeTitle")}</DialogTitle>
            <DialogDescription>
              <span className="font-semibold text-foreground">{roleChangeTarget?.userId}</span>
              {t("roleChangeDesc", {
                role: roleChangeTarget?.currentIsAdmin ? t("roleMember") : t("roleAdmin"),
              })}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRoleChangeTarget(null)}>
              {tc("cancel")}
            </Button>
            <Button
              disabled={changeRoleMutation.isPending}
              onClick={() => {
                if (!roleChangeTarget) return;
                changeRoleMutation.mutate(
                  {
                    teamId,
                    userId: roleChangeTarget.userId,
                    role: roleChangeTarget.currentIsAdmin ? "member" : "admin",
                  },
                  {
                    onSuccess: () => {
                      toast.success(t("roleChangeSuccess"));
                      setRoleChangeTarget(null);
                    },
                    onError: (err) => {
                      toast.error(err instanceof Error ? err.message : t("roleChangeFail"));
                    },
                  },
                );
              }}
            >
              {changeRoleMutation.isPending ? t("changing") : t("confirm")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Budget Change Dialog */}
      <Dialog open={!!budgetChangeTarget} onOpenChange={(open) => { if (!open) { setBudgetChangeTarget(null); setBudgetAmount(""); setBudgetTpm(""); setBudgetRpm(""); } }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("budgetChangeTitle")}</DialogTitle>
            <DialogDescription>
              <span className="font-semibold text-foreground">{budgetChangeTarget?.userId}</span>
              {t("budgetChangeDesc")}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="rounded-md bg-muted p-3 text-sm">
              <div className="flex justify-between">
                <span className="text-muted-foreground">{t("currentBudget")}</span>
                <span className="font-medium">
                  {budgetChangeTarget?.currentBudget === null ? t("unlimited") : `$${budgetChangeTarget?.currentBudget?.toFixed(2)}`}
                </span>
              </div>
              {budgetAmount && Number(budgetAmount) > 0 && (
                <div className="flex justify-between mt-1 pt-1 border-t border-border">
                  <span className="text-muted-foreground">{t("newBudget")}</span>
                  <span className="font-medium text-primary">${Number(budgetAmount).toFixed(2)}</span>
                </div>
              )}
            </div>
            <div>
              <label className="text-sm font-medium">{t("budgetAmountLabel")}</label>
              <Input
                type="number"
                step="0.01"
                min="0"
                placeholder={t("budgetAmountPlaceholder")}
                value={budgetAmount}
                onChange={(e) => setBudgetAmount(e.target.value)}
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-sm font-medium">{t("memberTpmLabel")}</label>
                <Input
                  type="number"
                  step="1"
                  min="0"
                  placeholder={t("memberRateLimitPlaceholder")}
                  value={budgetTpm}
                  onChange={(e) => setBudgetTpm(e.target.value)}
                />
              </div>
              <div>
                <label className="text-sm font-medium">{t("memberRpmLabel")}</label>
                <Input
                  type="number"
                  step="1"
                  min="0"
                  placeholder={t("memberRateLimitPlaceholder")}
                  value={budgetRpm}
                  onChange={(e) => setBudgetRpm(e.target.value)}
                />
              </div>
            </div>
            <p className="text-xs text-muted-foreground">{t("memberRateLimitHint")}</p>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => { setBudgetChangeTarget(null); setBudgetAmount(""); setBudgetTpm(""); setBudgetRpm(""); }}>
              {tc("cancel")}
            </Button>
            <Button
              disabled={!budgetAmount || Number(budgetAmount) <= 0 || changeBudgetMutation.isPending}
              onClick={() => {
                if (!budgetChangeTarget) return;
                changeBudgetMutation.mutate(
                  {
                    teamId,
                    userId: budgetChangeTarget.userId,
                    maxBudget: Number(budgetAmount),
                    tpmLimit: budgetTpm.trim() === "" ? null : Number(budgetTpm),
                    rpmLimit: budgetRpm.trim() === "" ? null : Number(budgetRpm),
                  },
                  {
                    onSuccess: () => {
                      toast.success(t("budgetChangeSuccess"));
                      setBudgetChangeTarget(null);
                      setBudgetAmount("");
                      setBudgetTpm("");
                      setBudgetRpm("");
                    },
                    onError: (err) => toast.error(err instanceof Error ? err.message : t("budgetChangeFail")),
                  },
                );
              }}
            >
              {changeBudgetMutation.isPending ? t("changing") : t("confirm")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Expiry Setting Dialog */}
      <Dialog open={!!expiryTarget} onOpenChange={(open) => { if (!open) { setExpiryTarget(null); setExpiryDate(""); } }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("expiryDialogTitle")}</DialogTitle>
            <DialogDescription>
              <span className="font-semibold text-foreground">{expiryTarget?.userId}</span>
              {t("expiryDialogDesc")}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            {expiryTarget?.currentExpiry && (
              <div className="rounded-md bg-muted p-3 text-sm">
                <div className="flex justify-between">
                  <span className="text-muted-foreground">{t("currentExpiry")}</span>
                  <span className="font-medium">{new Date(expiryTarget.currentExpiry).toLocaleDateString(localeTag)}</span>
                </div>
              </div>
            )}
            <div>
              <label className="text-sm font-medium">{t("expiryLabel")}</label>
              <input
                type="date"
                value={expiryDate}
                onChange={(e) => setExpiryDate(e.target.value)}
                className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
              />
            </div>
          </div>
          <DialogFooter>
            {expiryTarget?.currentExpiry && (
              <Button
                variant="outline"
                className="text-destructive"
                disabled={setExpiryMutation.isPending}
                onClick={() => {
                  if (!expiryTarget) return;
                  setExpiryMutation.mutate(
                    { teamId, userId: expiryTarget.userId, expiresAt: null },
                    {
                      onSuccess: () => {
                        toast.success(t("expiryRemovedSuccess"));
                        setExpiryTarget(null);
                        setExpiryDate("");
                      },
                      onError: (err) => toast.error(err instanceof Error ? err.message : t("expiryRemovedFail")),
                    },
                  );
                }}
              >
                {t("removeExpiry")}
              </Button>
            )}
            <Button variant="outline" onClick={() => { setExpiryTarget(null); setExpiryDate(""); }}>
              {tc("cancel")}
            </Button>
            <Button
              disabled={!expiryDate || setExpiryMutation.isPending}
              onClick={() => {
                if (!expiryTarget || !expiryDate) return;
                setExpiryMutation.mutate(
                  { teamId, userId: expiryTarget.userId, expiresAt: `${expiryDate}T23:59:59` },
                  {
                    onSuccess: () => {
                      toast.success(t("expirySetSuccess"));
                      setExpiryTarget(null);
                      setExpiryDate("");
                    },
                    onError: (err) => toast.error(err instanceof Error ? err.message : t("expirySetFail")),
                  },
                );
              }}
            >
              {setExpiryMutation.isPending ? t("setting") : t("confirm")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Key Limits Edit Dialog */}
      <Dialog
        open={!!keyLimitsTarget}
        onOpenChange={(open) => {
          if (!open) {
            setKeyLimitsTarget(null);
            setTpmInput("");
            setRpmInput("");
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("keyLimitsTitle")}</DialogTitle>
            <DialogDescription>
              <span className="font-semibold text-foreground">{keyLimitsTarget?.userId}</span>
              {t("keyLimitsDesc", { keyAlias: keyLimitsTarget?.keyAlias ? ` (${keyLimitsTarget.keyAlias})` : "" })}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="rounded-md bg-muted p-3 text-sm">
              <div className="flex justify-between">
                <span className="text-muted-foreground">{t("currentTpm")}</span>
                <span className="font-medium tabular-nums">
                  {keyLimitsTarget?.currentTpm != null ? keyLimitsTarget.currentTpm.toLocaleString() : t("unlimited")}
                </span>
              </div>
              <div className="flex justify-between mt-1">
                <span className="text-muted-foreground">{t("currentRpm")}</span>
                <span className="font-medium tabular-nums">
                  {keyLimitsTarget?.currentRpm != null ? keyLimitsTarget.currentRpm.toLocaleString() : t("unlimited")}
                </span>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-sm font-medium">TPM (tokens/min)</label>
                <Input
                  type="number"
                  min="0"
                  step="1"
                  placeholder={t("unlimited")}
                  value={tpmInput}
                  onChange={(e) => setTpmInput(e.target.value)}
                />
              </div>
              <div>
                <label className="text-sm font-medium">RPM (requests/min)</label>
                <Input
                  type="number"
                  min="0"
                  step="1"
                  placeholder={t("unlimited")}
                  value={rpmInput}
                  onChange={(e) => setRpmInput(e.target.value)}
                />
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setKeyLimitsTarget(null);
                setTpmInput("");
                setRpmInput("");
              }}
            >
              {tc("cancel")}
            </Button>
            <Button
              disabled={updateKeyLimitsMutation.isPending}
              onClick={() => {
                if (!keyLimitsTarget) return;
                updateKeyLimitsMutation.mutate(
                  {
                    teamId,
                    userId: keyLimitsTarget.userId,
                    token: keyLimitsTarget.token,
                    tpmLimit: tpmInput === "" ? null : Number(tpmInput),
                    rpmLimit: rpmInput === "" ? null : Number(rpmInput),
                  },
                  {
                    onSuccess: () => {
                      toast.success(t("keyLimitsSuccess"));
                      setKeyLimitsTarget(null);
                      setTpmInput("");
                      setRpmInput("");
                    },
                    onError: (err) => toast.error(err instanceof Error ? err.message : t("keyLimitsFail")),
                  },
                );
              }}
            >
              {updateKeyLimitsMutation.isPending ? t("changing") : t("confirm")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

export default function TeamDetailPage({
  params,
}: {
  params: Promise<{ teamId: string }>;
}) {
  const t = useTranslations("teamDetail");
  const tc = useTranslations("common");
  const localeTag = useLocaleTag();
  const { teamId } = use(params);
  const [activeTab, setActiveTab] = useState("overview");
  const [deletingKeyId, setDeletingKeyId] = useState<string | null>(null);
  const [copiedKeyId, setCopiedKeyId] = useState<string | null>(null);
  const [detailModel, setDetailModel] = useState<ModelWithCatalog | null>(null);
  const { data, isLoading, isError, error, refetch } = useTeamDetail(teamId);
  const { data: allModels } = useModels();
  const deleteKeyMutation = useDeleteKey();
  const revealKeyMutation = useRevealKey();
  const modelsByName = useMemo(
    () => new Map(allModels?.map((m) => [m.model_name, m]) ?? []),
    [allModels],
  );

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
        <Button variant="ghost" size="sm" asChild>
          <Link href="/teams">
            <ArrowLeft className="size-4" />
            {t("backToMyTeams")}
          </Link>
        </Button>
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

  if (!data) return null;

  const { team, my_keys, is_admin, my_membership } = data;
  // Rows for the team's models tab, matching the model dashboard table.
  // all-proxy / no explicit models = access to every model (LiteLLM default).
  const teamHasAllModels =
    team.models.includes("all-proxy-models") || team.models.length === 0;
  const teamModelRows: ModelTableRow[] = teamHasAllModels
    ? (allModels ?? [])
        .filter((m) => m.catalog && m.catalog.visible !== false)
        .map((m) => ({ name: m.model_name, model: m }))
    : team.models
        .filter((m) => m !== "all-proxy-models")
        .map((name) => ({ name, model: modelsByName.get(name) ?? null }));

  return (
    <div className="space-y-6">
      <Button variant="ghost" size="sm" asChild>
        <Link href="/teams">
          <ArrowLeft className="size-4" />
          {t("backToMyTeams2")}
        </Link>
      </Button>

      <div className="flex items-center gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-2xl font-bold">{team.team_alias}</h1>
            {is_admin && (
              <Badge variant="default" className="gap-1">
                <Shield className="size-3" />
                {t("roleAdmin")}
              </Badge>
            )}
          </div>
          {team.description && (
            <p className="mt-1 text-sm text-muted-foreground whitespace-pre-line">{team.description}</p>
          )}
        </div>
      </div>

      <Separator />

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="overview">{t("tabOverview")}</TabsTrigger>
          <TabsTrigger value="keys">{t("tabMyKeys")}</TabsTrigger>
          <TabsTrigger value="models">{t("tabModels")}</TabsTrigger>
          {is_admin && <TabsTrigger value="members">{t("tabMembers")}</TabsTrigger>}
          {is_admin && <TabsTrigger value="usage">{t("tabUsage")}</TabsTrigger>}
          {is_admin && <TabsTrigger value="settings">{t("tabSettings")}</TabsTrigger>}
        </TabsList>

        <TabsContent value="overview" className="mt-6">
          <OverviewTab
            team={team}
            isAdmin={is_admin}
            myKeys={my_keys}
            myMembership={my_membership}
            modelsByName={modelsByName}
            onMoveToKeys={() => setActiveTab("keys")}
            onMoveToModels={() => setActiveTab("models")}
            onSelectModel={setDetailModel}
          />
        </TabsContent>

        <TabsContent value="keys" className="mt-6 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold">{t("myApiKeys")}</h2>
            <Button asChild size="sm">
              <Link href={`/keys/new?team_id=${teamId}`}>
                <Plus className="size-4" />
                {t("createKey")}
              </Link>
            </Button>
          </div>

          {my_keys.length === 0 ? (
            <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed p-8">
              <p className="text-sm text-muted-foreground">{t("noKeysCreated")}</p>
              <Button asChild variant="outline" size="sm">
                <Link href={`/keys/new?team_id=${teamId}`}>
                  <Plus className="size-4" />
                  {t("createFirstKey")}
                </Link>
              </Button>
            </div>
          ) : (
            <div className="rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("colAlias")}</TableHead>
                    <TableHead>{t("colKey")}</TableHead>
                    <TableHead className="hidden lg:table-cell">TPM</TableHead>
                    <TableHead className="hidden lg:table-cell">RPM</TableHead>
                    <TableHead className="hidden md:table-cell">{t("colExpiry")}</TableHead>
                    <TableHead>{t("colModels")}</TableHead>
                    <TableHead className="hidden md:table-cell">{t("colCreatedAt")}</TableHead>
                    <TableHead className="w-12" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {my_keys.map((key) => {
                    return (
                      <TableRow key={key.token}>
                        <TableCell className="font-medium">
                          {key.key_alias || "-"}
                        </TableCell>
                        <TableCell className="hidden md:table-cell">
                          <div className="flex items-center gap-1">
                            <span className="font-mono text-xs text-muted-foreground">{maskKey(key.token)}</span>
                            <Button
                              variant="ghost"
                              size="icon-xs"
                              title={t("copyKey")}
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
                                    toast.success(t("keyCopied"));
                                    setTimeout(() => setCopiedKeyId(null), 2000);
                                  },
                                  onError: (err) => toast.error(err instanceof Error ? err.message : t("keyCopyFail")),
                                });
                              }}
                            >
                              {copiedKeyId === key.token ? <Check className="size-3.5 text-green-600" /> : <Copy className="size-3.5" />}
                            </Button>
                          </div>
                        </TableCell>
                        <TableCell className="hidden lg:table-cell text-sm text-muted-foreground">
                          {key.tpm_limit?.toLocaleString() ?? "-"}
                        </TableCell>
                        <TableCell className="hidden lg:table-cell text-sm text-muted-foreground">
                          {key.rpm_limit?.toLocaleString() ?? "-"}
                        </TableCell>
                        <TableCell className="hidden text-sm text-muted-foreground md:table-cell">
                          {key.expires ? formatDate(key.expires, localeTag) : "-"}
                        </TableCell>
                        <TableCell>
                          {key.models.length > 0 ? (
                            <Badge variant="secondary" className="gap-1">
                              <Boxes className="size-3" />
                              {key.models.includes("all-proxy-models") ? t("allModels") : t("modelCount", { count: key.models.length })}
                            </Badge>
                          ) : (
                            <span className="text-xs text-muted-foreground">{t("allModels")}</span>
                          )}
                        </TableCell>
                        <TableCell className="hidden text-sm text-muted-foreground md:table-cell">
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
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </TabsContent>

        <TabsContent value="models" className="mt-6 space-y-4">
          <h2 className="text-lg font-semibold">{t("availableModelsTab")}</h2>
          {teamModelRows.length === 0 ? (
            <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed p-8">
              <Boxes className="size-8 text-muted-foreground" />
              <p className="text-sm text-muted-foreground">{t("noModelsAssigned")}</p>
            </div>
          ) : (
            <div className="rounded-lg border">
              <ModelTable rows={teamModelRows} />
            </div>
          )}
        </TabsContent>

        {is_admin && (
          <TabsContent value="members" className="mt-6">
            <MembersTab teamId={teamId} />
          </TabsContent>
        )}

        {is_admin && (
          <TabsContent value="usage" className="mt-6">
            <UsageTab teamId={teamId} />
          </TabsContent>
        )}

        {is_admin && (
          <TabsContent value="settings" className="mt-6">
            <TeamSettingsTab
              teamId={teamId}
              description={team.description ?? null}
              defaultMemberBudget={data.default_member_budget ?? null}
              defaultMemberTpm={data.default_member_tpm_limit ?? null}
              defaultMemberRpm={data.default_member_rpm_limit ?? null}
              membershipDuration={data.membership_duration ?? null}
              defaultTpmLimit={data.default_tpm_limit ?? null}
              defaultRpmLimit={data.default_rpm_limit ?? null}
            />
          </TabsContent>
        )}
      </Tabs>

      <ModelDetailSheet
        model={detailModel}
        open={!!detailModel}
        onOpenChange={(o) => {
          if (!o) setDetailModel(null);
        }}
      />
    </div>
  );
}
