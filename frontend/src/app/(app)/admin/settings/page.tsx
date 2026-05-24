"use client";

import { useState, useEffect } from "react";
import { Loader2, Settings, Save, EyeOff, X, Plus } from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { usePortalSettings, useUpdatePortalSettings, useHiddenTeams, useUpdateHiddenTeams, useDefaultTeamRules, useUpdateDefaultTeamRules, useCatalogList, useUpdateCatalogList } from "@/hooks/use-api";
import type { DefaultTeamRule } from "@/hooks/use-api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";

export default function PortalSettingsPage() {
  const t = useTranslations("adminSettings");
  const { data: settings, isLoading } = usePortalSettings();
  const updateMutation = useUpdatePortalSettings();
  const { data: hiddenTeams } = useHiddenTeams();
  const updateHiddenTeams = useUpdateHiddenTeams();
  const { data: catalogListData } = useCatalogList();
  const updateCatalogList = useUpdateCatalogList();
  const catalogs: string[] = catalogListData?.catalogs ?? [];
  const [suffixInput, setSuffixInput] = useState("");

  function handleAddSuffix() {
    const name = suffixInput.trim();
    if (!name) return;
    if (catalogs.includes(name)) {
      toast.error(t("errorSuffixExists"));
      return;
    }
    updateCatalogList.mutate([...catalogs, name], {
      onSuccess: () => {
        toast.success(t("toastSuffixAdded", { name }));
        setSuffixInput("");
      },
      onError: (err) => toast.error(err instanceof Error ? err.message : t("errorSuffixAdd")),
    });
  }

  function handleRemoveSuffix(name: string) {
    if (catalogs.length <= 1) {
      toast.error(t("errorSuffixMin"));
      return;
    }
    updateCatalogList.mutate(catalogs.filter((c) => c !== name), {
      onSuccess: () => toast.success(t("toastSuffixRemoved", { name })),
      onError: (err) => toast.error(err instanceof Error ? err.message : t("errorSuffixRemove")),
    });
  }

  const [tpmLimit, setTpmLimit] = useState("");
  const [rpmLimit, setRpmLimit] = useState("");
  const [defaultTeamId, setDefaultTeamId] = useState("");
  const [newHiddenTeamId, setNewHiddenTeamId] = useState("");
  const { data: teamRules } = useDefaultTeamRules();
  const updateTeamRules = useUpdateDefaultTeamRules();
  const [newRulePrefix, setNewRulePrefix] = useState("");
  const [newRuleTeams, setNewRuleTeams] = useState("");

  useEffect(() => {
    if (settings) {
      setTpmLimit(String(settings.default_tpm_limit));
      setRpmLimit(String(settings.default_rpm_limit));
      setDefaultTeamId(settings.default_team_id || "");
    }
  }, [settings]);

  const handleSave = () => {
    updateMutation.mutate(
      {
        default_tpm_limit: Number(tpmLimit),
        default_rpm_limit: Number(rpmLimit),
        default_team_id: defaultTeamId || undefined,
      },
      {
        onSuccess: () => toast.success(t("toastSaved")),
        onError: (err) =>
          toast.error(err instanceof Error ? err.message : t("errorSave")),
      },
    );
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h1 className="text-2xl font-bold">{t("title")}</h1>
        <p className="text-muted-foreground mt-1">
          {t("subtitle")}
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Settings className="size-4" />
            {t("limitsCardTitle")}
          </CardTitle>
          <CardDescription>
            {t("limitsCardDescription")}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="tpm-limit">{t("tpmLabel")}</Label>
              <Input
                id="tpm-limit"
                type="number"
                min="0"
                value={tpmLimit}
                onChange={(e) => setTpmLimit(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="rpm-limit">{t("rpmLabel")}</Label>
              <Input
                id="rpm-limit"
                type="number"
                min="0"
                value={rpmLimit}
                onChange={(e) => setRpmLimit(e.target.value)}
              />
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Settings className="size-4" />
            {t("autoEnrollCardTitle")}
          </CardTitle>
          <CardDescription>
            {t("autoEnrollCardDescription")}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          {/* Base team */}
          <div className="space-y-2">
            <Label htmlFor="default-team-id">{t("baseTeamLabel")}</Label>
            <Input
              id="default-team-id"
              value={defaultTeamId}
              onChange={(e) => setDefaultTeamId(e.target.value)}
              placeholder={t("baseTeamPlaceholder")}
            />
            <p className="text-xs text-muted-foreground">
              {t("baseTeamHelp")}
            </p>
          </div>

          <div className="border-t pt-4 space-y-3">
            <div>
              <Label>{t("rulesLabel")}</Label>
              <p className="text-xs text-muted-foreground mt-1">
                {t("rulesHelp")}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Input
                placeholder={t("rulePrefixPlaceholder")}
                value={newRulePrefix}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => setNewRulePrefix(e.target.value)}
                className="w-32"
              />
              <Input
                placeholder={t("ruleTeamsPlaceholder")}
                value={newRuleTeams}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => setNewRuleTeams(e.target.value)}
                className="flex-1"
              />
              <Button
                variant="outline"
                size="sm"
                disabled={!newRulePrefix.trim() || !newRuleTeams.trim() || updateTeamRules.isPending}
                onClick={() => {
                  const teams = newRuleTeams.split(",").map((s: string) => s.trim()).filter(Boolean);
                  if (teams.length === 0) return;
                  const updated: DefaultTeamRule[] = [
                    ...(teamRules || []),
                    { prefix: newRulePrefix.trim().toUpperCase(), teams },
                  ];
                  updateTeamRules.mutate(updated, {
                    onSuccess: () => {
                      toast.success(t("toastRuleAdded"));
                      setNewRulePrefix("");
                      setNewRuleTeams("");
                    },
                    onError: (err: unknown) => toast.error(err instanceof Error ? err.message : t("errorRuleAdd")),
                  });
                }}
              >
                <Plus className="size-4" />
                {t("addBtn")}
              </Button>
            </div>
            {teamRules && teamRules.length > 0 ? (
              <div className="space-y-2">
                {teamRules.map((rule: DefaultTeamRule, idx: number) => (
                  <div key={idx} className="flex items-center gap-2 rounded-md border p-2">
                    <Badge variant="default" className="shrink-0">{rule.prefix}</Badge>
                    <div className="flex flex-wrap gap-1 flex-1">
                      {rule.teams.map((teamId: string) => (
                        <Badge key={teamId} variant="secondary">{teamId}</Badge>
                      ))}
                    </div>
                    <button
                      type="button"
                      className="rounded-full p-1 hover:bg-muted"
                      onClick={() => {
                        const updated = teamRules.filter((_: DefaultTeamRule, i: number) => i !== idx);
                        updateTeamRules.mutate(updated, {
                          onSuccess: () => toast.success(t("toastRuleRemoved")),
                          onError: (err: unknown) => toast.error(err instanceof Error ? err.message : t("errorRuleRemove")),
                        });
                      }}
                    >
                      <X className="size-3" />
                    </button>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">{t("rulesEmpty")}</p>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <EyeOff className="size-4" />
            {t("hideCardTitle")}
          </CardTitle>
          <CardDescription>
            {t("hideCardDescription")}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center gap-2">
            <Input
              placeholder={t("hidePlaceholder")}
              value={newHiddenTeamId}
              onChange={(e) => setNewHiddenTeamId(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  if (!newHiddenTeamId.trim()) return;
                  const updated = [...(hiddenTeams || []), newHiddenTeamId.trim()];
                  updateHiddenTeams.mutate(updated, {
                    onSuccess: () => {
                      toast.success(t("toastHideAdded"));
                      setNewHiddenTeamId("");
                    },
                    onError: (err) => toast.error(err instanceof Error ? err.message : t("errorHideAdd")),
                  });
                }
              }}
            />
            <Button
              variant="outline"
              size="sm"
              disabled={!newHiddenTeamId.trim() || updateHiddenTeams.isPending}
              onClick={() => {
                if (!newHiddenTeamId.trim()) return;
                const updated = [...(hiddenTeams || []), newHiddenTeamId.trim()];
                updateHiddenTeams.mutate(updated, {
                  onSuccess: () => {
                    toast.success(t("toastHideAdded"));
                    setNewHiddenTeamId("");
                  },
                  onError: (err) => toast.error(err instanceof Error ? err.message : t("errorHideAdd")),
                });
              }}
            >
              <Plus className="size-4" />
              {t("addBtn")}
            </Button>
          </div>
          {hiddenTeams && hiddenTeams.length > 0 ? (
            <div className="flex flex-wrap gap-2">
              {hiddenTeams.map((teamId) => (
                <Badge key={teamId} variant="secondary" className="gap-1 pr-1">
                  {teamId}
                  <button
                    type="button"
                    className="ml-1 rounded-full p-0.5 hover:bg-muted-foreground/20"
                    onClick={() => {
                      const updated = hiddenTeams.filter((id) => id !== teamId);
                      updateHiddenTeams.mutate(updated, {
                        onSuccess: () => toast.success(t("toastHideRemoved")),
                        onError: (err) => toast.error(err instanceof Error ? err.message : t("errorHideRemove")),
                      });
                    }}
                  >
                    <X className="size-3" />
                  </button>
                </Badge>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">{t("hideEmpty")}</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Settings className="size-4" />
            {t("cacheCardTitle")}
          </CardTitle>
          <CardDescription>
            {t("cacheCardDescription")}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center gap-2">
            <Input
              value={suffixInput}
              onChange={(e) => setSuffixInput(e.target.value)}
              placeholder={t("cachePlaceholder")}
              className="h-9"
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  handleAddSuffix();
                }
              }}
            />
            <Button size="sm" onClick={handleAddSuffix} disabled={updateCatalogList.isPending}>
              <Plus className="size-3.5" />
              {t("addBtn")}
            </Button>
          </div>
          <div className="space-y-2">
            {catalogs.length > 0 ? (
              catalogs.map((c) => (
                <div key={c} className="flex items-center justify-between rounded-md border px-3 py-2">
                  <span className="text-sm font-mono">{c}</span>
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    className="text-destructive hover:text-destructive"
                    disabled={catalogs.length <= 1}
                    onClick={() => handleRemoveSuffix(c)}
                  >
                    <X className="size-3.5" />
                  </Button>
                </div>
              ))
            ) : (
              <p className="text-sm text-muted-foreground">{t("cacheEmpty")}</p>
            )}
          </div>
        </CardContent>
      </Card>

      <Button
        onClick={handleSave}
        disabled={updateMutation.isPending}
      >
        {updateMutation.isPending ? (
          <Loader2 className="size-4 animate-spin" />
        ) : (
          <Save className="size-4" />
        )}
        {t("save")}
      </Button>
    </div>
  );
}
