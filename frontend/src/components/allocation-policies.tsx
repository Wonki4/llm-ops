"use client";

import { useState } from "react";
import { useSearchParams } from "next/navigation";
import { Loader2, Settings, Save, EyeOff, X, Plus } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import { usePortalSettings, useUpdatePortalSettings, useHiddenTeams, useUpdateHiddenTeams, useDefaultTeamRules, useUpdateDefaultTeamRules, useDiscoverTeams } from "@/hooks/use-api";
import { PageTabs, usePageTab } from "@/components/page-tabs";
import type { PortalSettings, DefaultTeamRule } from "@/hooks/use-api";
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

export function AllocationPolicies() {
  const [tab]=usePageTab(["teams","keys"],"teams");const params=useSearchParams();const t=useTranslations("portalPolicies");
  const query=usePortalSettings();
  if(query.isError)return <p role="alert">{query.error.message}</p>;
  if(!query.data)return <p aria-busy="true">…</p>;
  return <div className="max-w-3xl space-y-6"><h1 className="text-2xl font-bold">{t("title")}</h1><PageTabs label={t("title")} active={tab} tabs={["teams","keys"].map(id=>{const q=new URLSearchParams(params.toString());q.set("tab",id);return {id,label:t(id),href:`?${q}`};})} /><PolicyFields key={`${tab}-${JSON.stringify(query.data)}`} tab={tab} settings={query.data} /></div>;
}
function PolicyFields({tab,settings}:{tab:string;settings:PortalSettings}) {
  const t = useTranslations("settings");
  const tc = useTranslations("common");

  const updateMutation = useUpdatePortalSettings();
  const { data: hiddenTeams } = useHiddenTeams();
  const updateHiddenTeams = useUpdateHiddenTeams();

  const { data: allTeams } = useDiscoverTeams();
  const teamAlias = new Map((allTeams ?? []).map((tm) => [tm.team_id, tm.team_alias]));
  const teamLabel = (id: string) => {
    const alias = teamAlias.get(id);
    return alias ? `${alias} (${id.slice(0, 8)}…)` : id;
  };


  const [tpmLimit, setTpmLimit] = useState(String(settings.default_tpm_limit));
  const [rpmLimit, setRpmLimit] = useState(String(settings.default_rpm_limit));
  const [personalKeysBeta, setPersonalKeysBeta] = useState(!!settings.personal_keys_beta_enabled);
  const [defaultTeamIds, setDefaultTeamIds] = useState<string[]>((settings.default_team_id || "").split(",").map(s=>s.trim()).filter(Boolean));
  const { data: teamRules } = useDefaultTeamRules();
  const updateTeamRules = useUpdateDefaultTeamRules();
  const [newRulePrefix, setNewRulePrefix] = useState("");
  const [newRuleTeamIds, setNewRuleTeamIds] = useState<string[]>([]);

  const handleSave = () => {
    updateMutation.mutate(
      tab === "teams" ? {default_team_id: defaultTeamIds.join(",")} : {
        default_tpm_limit: Number(tpmLimit), default_rpm_limit: Number(rpmLimit), personal_keys_beta_enabled: personalKeysBeta,
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

  return <div className="space-y-6">
        {/* ── 일반 ── */}
        {tab === "keys" && <div className="space-y-6">
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
              <div className="flex items-start gap-3 rounded-md border p-3">
                <input
                  id="personal-keys-beta"
                  type="checkbox"
                  className="mt-1"
                  checked={personalKeysBeta}
                  onChange={(e) => setPersonalKeysBeta(e.target.checked)}
                  data-testid="personal-keys-beta"
                />
                <div className="space-y-1">
                  <Label htmlFor="personal-keys-beta" className="flex items-center gap-2">
                    {t("personalKeysSetting")}
                    <span className="text-xs font-normal text-muted-foreground">
                      {personalKeysBeta ? t("personalKeysOn") : t("personalKeysOff")}
                    </span>
                  </Label>
                  <p className="text-xs text-muted-foreground">{t("personalKeysSettingHint")}</p>
                </div>
              </div>
            </CardContent>
          </Card>



          {saveButton}
        </div>}

        {/* ── 팀 ── */}
        {tab === "teams" && <div className="space-y-6">
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
        </div>}

    </div>;
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
