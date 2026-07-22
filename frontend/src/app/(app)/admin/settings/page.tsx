"use client";

import { useState, useEffect } from "react";
import { Loader2, Settings, Save, EyeOff, X, Plus } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import { usePortalSettings, useUpdatePortalSettings, useHiddenTeams, useUpdateHiddenTeams, useDefaultTeamRules, useUpdateDefaultTeamRules, useCatalogList, useUpdateCatalogList, useDiscoverTeams } from "@/hooks/use-api";
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ClusterSettingsTab } from "@/components/cluster-settings-tab";

export default function PortalSettingsPage() {
  const t = useTranslations("settings");
  const tc = useTranslations("common");

  const { data: settings, isLoading } = usePortalSettings();
  const updateMutation = useUpdatePortalSettings();
  const { data: hiddenTeams } = useHiddenTeams();
  const updateHiddenTeams = useUpdateHiddenTeams();
  const { data: catalogListData } = useCatalogList();
  const updateCatalogList = useUpdateCatalogList();
  const catalogs: string[] = catalogListData?.catalogs ?? [];
  const [suffixInput, setSuffixInput] = useState("");

  const { data: allTeams } = useDiscoverTeams();
  const teamAlias = new Map((allTeams ?? []).map((tm) => [tm.team_id, tm.team_alias]));
  const teamLabel = (id: string) => {
    const alias = teamAlias.get(id);
    return alias ? `${alias} (${id.slice(0, 8)}…)` : id;
  };

  function handleAddSuffix() {
    const name = suffixInput.trim();
    if (!name) return;
    if (catalogs.includes(name)) {
      toast.error(t("suffixAlreadyExists"));
      return;
    }
    updateCatalogList.mutate([...catalogs, name], {
      onSuccess: () => {
        toast.success(t("suffixAddSuccess", { suffix: name }));
        setSuffixInput("");
      },
      onError: (err) => toast.error(err instanceof Error ? err.message : t("addFailed")),
    });
  }

  function handleRemoveSuffix(name: string) {
    if (catalogs.length <= 1) {
      toast.error(t("suffixMinRequired"));
      return;
    }
    updateCatalogList.mutate(catalogs.filter((c) => c !== name), {
      onSuccess: () => toast.success(t("suffixRemoveSuccess", { suffix: name })),
      onError: (err) => toast.error(err instanceof Error ? err.message : t("removeFailed")),
    });
  }

  const [tpmLimit, setTpmLimit] = useState("");
  const [rpmLimit, setRpmLimit] = useState("");
  const [defaultTeamIds, setDefaultTeamIds] = useState<string[]>([]);
  const { data: teamRules } = useDefaultTeamRules();
  const updateTeamRules = useUpdateDefaultTeamRules();
  const [newRulePrefix, setNewRulePrefix] = useState("");
  const [newRuleTeamIds, setNewRuleTeamIds] = useState<string[]>([]);

  useEffect(() => {
    if (settings) {
      setTpmLimit(String(settings.default_tpm_limit));
      setRpmLimit(String(settings.default_rpm_limit));
      setDefaultTeamIds(
        (settings.default_team_id || "")
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
      );
    }
  }, [settings]);

  const handleSave = () => {
    updateMutation.mutate(
      {
        default_tpm_limit: Number(tpmLimit),
        default_rpm_limit: Number(rpmLimit),
        default_team_id: defaultTeamIds.join(",") || undefined,
      },
      {
        onSuccess: () => toast.success(t("saveSuccess")),
        onError: (err) =>
          toast.error(err instanceof Error ? err.message : t("saveFailed")),
      },
    );
  };

  const saveButton = (
    <Button onClick={handleSave} disabled={updateMutation.isPending}>
      {updateMutation.isPending ? (
        <Loader2 className="size-4 animate-spin" />
      ) : (
        <Save className="size-4" />
      )}
      {tc("save")}
    </Button>
  );

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
        <h1 className="text-2xl font-bold">{t("pageTitle")}</h1>
        <p className="text-muted-foreground mt-1">
          {t("pageDescription")}
        </p>
      </div>

      <Tabs defaultValue="general" className="w-full">
        <TabsList variant="line" className="w-full justify-start gap-6 rounded-none border-b">
          <TabsTrigger value="general" className="flex-none px-1">{t("tabGeneral")}</TabsTrigger>
          <TabsTrigger value="teams" className="flex-none px-1">{t("tabTeams")}</TabsTrigger>
          <TabsTrigger value="clusters" className="flex-none px-1">{t("tabClusters")}</TabsTrigger>
        </TabsList>

        {/* ── 일반 ── */}
        <TabsContent value="general" className="mt-4 space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="text-base flex items-center gap-2">
                <Settings className="size-4" />
                {t("apiKeyLimitsTitle")}
              </CardTitle>
              <CardDescription>
                {t("apiKeyLimitsDescription")}
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
              <CardTitle className="flex items-center gap-2 text-base">
                <Settings className="size-4" />
                {t("cacheCatalogTitle")}
              </CardTitle>
              <CardDescription>
                {t("cacheCatalogDescription")}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex items-center gap-2">
                <Input
                  value={suffixInput}
                  onChange={(e) => setSuffixInput(e.target.value)}
                  placeholder={t("newSuffixPlaceholder")}
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
                  {t("addButton")}
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
                  <p className="text-sm text-muted-foreground">{t("noSuffixes")}</p>
                )}
              </div>
            </CardContent>
          </Card>

          {saveButton}
        </TabsContent>

        {/* ── 팀 ── */}
        <TabsContent value="teams" className="mt-4 space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="text-base flex items-center gap-2">
                <Settings className="size-4" />
                {t("autoRegisterTitle")}
              </CardTitle>
              <CardDescription>
                {t("autoRegisterDescription")}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              {/* Base team(s) — stored as one comma-joined default_team_id string */}
              <div className="space-y-2">
                <Label htmlFor="default-team-select">{t("defaultTeamLabel")}</Label>
                {defaultTeamIds.length > 0 && (
                  <div className="flex flex-wrap gap-2">
                    {defaultTeamIds.map((id) => (
                      <TeamBadge
                        key={id}
                        label={teamLabel(id)}
                        onRemove={() =>
                          setDefaultTeamIds(defaultTeamIds.filter((x) => x !== id))
                        }
                      />
                    ))}
                  </div>
                )}
                <TeamAddSelect
                  id="default-team-select"
                  teams={allTeams ?? []}
                  exclude={defaultTeamIds}
                  placeholder={t("teamSelectPlaceholder")}
                  onAdd={(id) => setDefaultTeamIds([...defaultTeamIds, id])}
                />
                <p className="text-xs text-muted-foreground">
                  {t("defaultTeamHelp")}
                </p>
              </div>

              <div className="border-t pt-4 space-y-3">
                <div>
                  <Label>{t("extraTeamRulesLabel")}</Label>
                  <p className="text-xs text-muted-foreground mt-1">
                    {t("extraTeamRulesHelp")}
                  </p>
                </div>
                <div className="flex items-start gap-2">
                  <Input
                    placeholder={t("prefixPlaceholder")}
                    value={newRulePrefix}
                    onChange={(e: React.ChangeEvent<HTMLInputElement>) => setNewRulePrefix(e.target.value)}
                    className="w-32"
                  />
                  <div className="flex-1 space-y-2">
                    <TeamAddSelect
                      teams={allTeams ?? []}
                      exclude={newRuleTeamIds}
                      placeholder={t("teamSelectPlaceholder")}
                      onAdd={(id) => setNewRuleTeamIds([...newRuleTeamIds, id])}
                    />
                    {newRuleTeamIds.length > 0 && (
                      <div className="flex flex-wrap gap-1">
                        {newRuleTeamIds.map((id) => (
                          <TeamBadge
                            key={id}
                            label={teamLabel(id)}
                            onRemove={() =>
                              setNewRuleTeamIds(newRuleTeamIds.filter((x) => x !== id))
                            }
                          />
                        ))}
                      </div>
                    )}
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={!newRulePrefix.trim() || newRuleTeamIds.length === 0 || updateTeamRules.isPending}
                    onClick={() => {
                      const updated: DefaultTeamRule[] = [
                        ...(teamRules || []),
                        { prefix: newRulePrefix.trim().toUpperCase(), teams: newRuleTeamIds },
                      ];
                      updateTeamRules.mutate(updated, {
                        onSuccess: () => {
                          toast.success(t("ruleAddSuccess"));
                          setNewRulePrefix("");
                          setNewRuleTeamIds([]);
                        },
                        onError: (err: unknown) => toast.error(err instanceof Error ? err.message : t("addFailed")),
                      });
                    }}
                  >
                    <Plus className="size-4" />
                    {t("addButton")}
                  </Button>
                </div>
                {teamRules && teamRules.length > 0 ? (
                  <div className="space-y-2">
                    {teamRules.map((rule: DefaultTeamRule, idx: number) => (
                      <div key={idx} className="flex items-center gap-2 rounded-md border p-2">
                        <Badge variant="default" className="shrink-0">{rule.prefix}</Badge>
                        <div className="flex flex-wrap gap-1 flex-1">
                          {rule.teams.map((teamId: string) => (
                            <Badge key={teamId} variant="secondary">{teamLabel(teamId)}</Badge>
                          ))}
                        </div>
                        <button
                          type="button"
                          className="rounded-full p-1 hover:bg-muted"
                          onClick={() => {
                            const updated = teamRules.filter((_: DefaultTeamRule, i: number) => i !== idx);
                            updateTeamRules.mutate(updated, {
                              onSuccess: () => toast.success(t("ruleDeleteSuccess")),
                              onError: (err: unknown) => toast.error(err instanceof Error ? err.message : t("deleteFailed")),
                            });
                          }}
                        >
                          <X className="size-3" />
                        </button>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">{t("noExtraRules")}</p>
                )}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base flex items-center gap-2">
                <EyeOff className="size-4" />
                {t("hideTeamsTitle")}
              </CardTitle>
              <CardDescription>
                {t("hideTeamsDescription")}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <TeamAddSelect
                teams={allTeams ?? []}
                exclude={[...(hiddenTeams?.hidden_teams ?? []), ...(hiddenTeams?.hidden_teams_strict ?? [])]}
                placeholder={t("hiddenTeamSelectPlaceholder")}
                disabled={updateHiddenTeams.isPending}
                onAdd={(id) => {
                  updateHiddenTeams.mutate(
                    {
                      hidden_teams: [...(hiddenTeams?.hidden_teams ?? []), id],
                      hidden_teams_strict: hiddenTeams?.hidden_teams_strict ?? [],
                    },
                    {
                      onSuccess: () => toast.success(t("teamHideSuccess")),
                      onError: (err) => toast.error(err instanceof Error ? err.message : t("addFailed")),
                    },
                  );
                }}
              />
              {(() => {
                const base = hiddenTeams?.hidden_teams ?? [];
                const strictList = hiddenTeams?.hidden_teams_strict ?? [];
                const rows = [
                  ...base.map((id) => ({ id, strict: false })),
                  ...strictList.map((id) => ({ id, strict: true })),
                ];
                if (rows.length === 0) {
                  return <p className="text-sm text-muted-foreground">{t("noHiddenTeams")}</p>;
                }
                const save = (
                  body: { hidden_teams: string[]; hidden_teams_strict: string[] },
                  successMsg: string,
                ) =>
                  updateHiddenTeams.mutate(body, {
                    onSuccess: () => toast.success(successMsg),
                    onError: (err) =>
                      toast.error(err instanceof Error ? err.message : t("removeFailed")),
                  });
                return (
                  <div className="space-y-2">
                    {rows.map(({ id, strict }) => (
                      <div
                        key={id}
                        className="flex items-center justify-between gap-3 rounded-md border px-3 py-2"
                      >
                        <span className="truncate text-sm font-medium">{teamLabel(id)}</span>
                        <div className="flex shrink-0 items-center gap-2">
                          <select
                            className="h-8 rounded-md border border-input bg-background px-2 text-xs"
                            value={strict ? "strict" : "default"}
                            disabled={updateHiddenTeams.isPending}
                            onChange={(e) => {
                              const toStrict = e.target.value === "strict";
                              save(
                                {
                                  hidden_teams: toStrict
                                    ? base.filter((x) => x !== id)
                                    : [...base, id],
                                  hidden_teams_strict: toStrict
                                    ? [...strictList, id]
                                    : strictList.filter((x) => x !== id),
                                },
                                t("teamHideModeSuccess"),
                              );
                            }}
                          >
                            <option value="default">{t("hiddenModeDefault")}</option>
                            <option value="strict">{t("hiddenModeStrict")}</option>
                          </select>
                          <button
                            type="button"
                            className="rounded-full p-1 hover:bg-muted-foreground/20"
                            onClick={() =>
                              save(
                                {
                                  hidden_teams: base.filter((x) => x !== id),
                                  hidden_teams_strict: strictList.filter((x) => x !== id),
                                },
                                t("teamUnhideSuccess"),
                              )
                            }
                          >
                            <X className="size-3" />
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                );
              })()}
            </CardContent>
          </Card>

          {saveButton}
        </TabsContent>

        {/* ── 클러스터 ── */}
        <TabsContent value="clusters" className="mt-4">
          <ClusterSettingsTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function TeamAddSelect({
  id,
  teams,
  exclude,
  placeholder,
  onAdd,
  disabled = false,
}: {
  id?: string;
  teams: { team_id: string; team_alias: string }[];
  exclude: string[];
  placeholder: string;
  onAdd: (teamId: string) => void;
  disabled?: boolean;
}) {
  return (
    <select
      id={id}
      disabled={disabled}
      className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm disabled:opacity-50"
      value=""
      onChange={(e) => {
        if (e.target.value) onAdd(e.target.value);
      }}
    >
      <option value="">{placeholder}</option>
      {teams
        .filter((tm) => !exclude.includes(tm.team_id))
        .map((tm) => (
          <option key={tm.team_id} value={tm.team_id}>
            {tm.team_alias || tm.team_id}
          </option>
        ))}
    </select>
  );
}

function TeamBadge({ label, onRemove }: { label: string; onRemove: () => void }) {
  return (
    <Badge variant="secondary" className="gap-1 pr-1">
      {label}
      <button
        type="button"
        className="ml-1 rounded-full p-0.5 hover:bg-muted-foreground/20"
        onClick={onRemove}
      >
        <X className="size-3" />
      </button>
    </Badge>
  );
}
