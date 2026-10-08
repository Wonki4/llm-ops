"use client";

import { useState, useMemo } from "react";
import Link from "next/link";
import {
  Search,
  Loader2,
  Boxes,
  Users,
  ChevronRight,
  X,
} from "lucide-react";
import { useTranslations } from "next-intl";

import { useModels, useMyTeams, useMe } from "@/hooks/use-api";
import { ModelTable } from "@/components/model-table";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ModelDetailSheet } from "@/components/model-detail-sheet";
import type { ModelStatus, ModelWithCatalog, Team } from "@/types";
import { buildAccessGroupIndex, expandModelGrants } from "@/lib/access-groups";
import { expandPersonalScope, grantState } from "@/lib/personal-access";

// ─── Constants ────────────────────────────────────────────────

const ALL_PROXY_MODELS = "all-proxy-models";
/** Synthetic "team" id for the user's personal-key scope (Beta). */
const PERSONAL_ID = "__personal__";

const STATUS_OPTIONS: { value: ModelStatus }[] = [
  { value: "testing" },
  { value: "prerelease" },
  { value: "lts" },
  { value: "deprecating" },
  { value: "deprecated" },
];

// ─── Helpers ──────────────────────────────────────────────────

/** A team has "all models" access when it lists the all-proxy sentinel or has no
 * explicit models (LiteLLM treats an empty list as no restriction). */
function hasAllModels(team: Team): boolean {
  return team.models.includes(ALL_PROXY_MODELS) || team.models.length === 0;
}

/** The explicit model names a team lists, excluding the all-proxy sentinel. */
function explicitModels(team: Team): string[] {
  return team.models.filter((m) => m !== ALL_PROXY_MODELS);
}

// A resolved row in the model table: the team's model name plus its merged
// catalog/litellm record (null when the name has no matching deployed/catalog model).
type ModelRow = { name: string; model: ModelWithCatalog | null; viaGroup?: string | null };

// ─── Main Component ───────────────────────────────────────────

export default function ModelDashboardPage() {
  const t = useTranslations("modelsDashboard");

  // Data fetching
  const { data: teams, isLoading: teamsLoading, isError: teamsError } = useMyTeams();
  const { data: models, isLoading: modelsLoading } = useModels();
  const { data: me } = useMe();
  const [detailModel, setDetailModel] = useState<ModelWithCatalog | null>(null);

  // Personal-key scope (Beta): shown only to users who hold a grant; the
  // beta switch only decides whether a new personal key can be minted.
  const personalState = grantState(me?.models);
  const showPersonal = personalState !== "none";

  const modelsByName = useMemo(
    () => new Map((models ?? []).map((m) => [m.model_name, m])),
    [models],
  );
  const accessGroupIndex = useMemo(() => buildAccessGroupIndex(models), [models]);

  // Selected team (defaults to the first team once loaded; the personal
  // entry when the user has no team but holds a personal grant)
  const [selectedTeamId, setSelectedTeamId] = useState<string | null>(null);
  const isPersonal = selectedTeamId === PERSONAL_ID || (selectedTeamId === null && (!teams || teams.length === 0) && showPersonal);
  const selectedTeam = useMemo<Team | null>(() => {
    if (isPersonal || !teams || teams.length === 0) return null;
    return teams.find((t) => t.team_id === selectedTeamId) ?? teams[0];
  }, [teams, selectedTeamId, isPersonal]);
  const personalRows = useMemo(
    () => expandPersonalScope(me?.models, accessGroupIndex, modelsByName, models),
    [me?.models, accessGroupIndex, modelsByName, models],
  );

  // Filter state for the model table (applied live)
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("");

  // ── Rows for the selected team ──
  const teamRows = useMemo<ModelRow[]>(() => {
    if (!selectedTeam && !isPersonal) return [];

    let rows: ModelRow[];
    if (isPersonal || !selectedTeam) {
      rows = personalRows;
    } else if (hasAllModels(selectedTeam)) {
      rows = (models ?? [])
        .filter((m) => m.catalog && m.catalog.visible !== false)
        .map((m) => ({ name: m.model_name, model: m }));
    } else {
      // Access-group grants expand into their member models (tagged viaGroup).
      rows = expandModelGrants(explicitModels(selectedTeam), accessGroupIndex, modelsByName);
    }

    const q = query.trim().toLowerCase();
    if (q) {
      rows = rows.filter(
        (r) =>
          r.name.toLowerCase().includes(q) ||
          r.model?.catalog?.display_name?.toLowerCase().includes(q),
      );
    }

    if (statusFilter) {
      rows = rows.filter((r) => r.model?.catalog?.status === statusFilter);
    }

    return rows.sort((a, b) => {
      const an = a.model?.catalog?.display_name ?? a.name;
      const bn = b.model?.catalog?.display_name ?? b.name;
      return an.localeCompare(bn);
    });
  }, [selectedTeam, isPersonal, personalRows, models, modelsByName, accessGroupIndex, query, statusFilter]);

  // ── Filter handlers ──
  function resetFilters() {
    setQuery("");
    setStatusFilter("");
  }

  function selectTeam(teamId: string) {
    setSelectedTeamId(teamId);
    resetFilters();
  }

  const hasActiveFilters = !!(query.trim() || statusFilter);

  function teamModelLabel(team: Team): string {
    return hasAllModels(team)
      ? t("byTeam.allModels")
      : t("byTeam.modelCount", { count: expandModelGrants(explicitModels(team), accessGroupIndex, modelsByName).length });
  }
  const personalLabel =
    personalState === "all" ? t("byTeam.personalAllModels") : t("byTeam.modelCount", { count: personalRows.length });
  const headerTitle = isPersonal ? t("byTeam.personal") : selectedTeam?.team_alias ?? "";
  const headerLabel = isPersonal ? personalLabel : selectedTeam ? teamModelLabel(selectedTeam) : "";

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold">{t("title")}</h1>
        <p className="text-muted-foreground mt-1">{t("byTeam.description")}</p>
      </div>

      {teamsLoading || modelsLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="size-6 animate-spin text-muted-foreground" />
        </div>
      ) : teamsError ? (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          {t("error.loadFailed")}
        </div>
      ) : (!teams || teams.length === 0) && !showPersonal ? (
        <div className="flex flex-col items-center justify-center rounded-lg border border-dashed py-16 text-center">
          <Users className="size-10 text-muted-foreground mb-3" />
          <p className="text-muted-foreground mb-4">{t("byTeam.noTeams")}</p>
          <Button asChild variant="outline" size="sm">
            <Link href="/teams/discover">{t("byTeam.discoverTeams")}</Link>
          </Button>
        </div>
      ) : (
        <div className="flex flex-col gap-4 md:flex-row md:items-start">
          {/* ── Left: team list ── */}
          <div className="md:w-56 md:shrink-0">
            <div className="rounded-lg border md:overflow-hidden">
              {/* Header — matches the right card header height */}
              <div className="flex h-14 items-center border-b px-4">
                <span className="text-sm font-semibold text-muted-foreground">
                  {t("byTeam.myTeams")}
                </span>
              </div>
              {showPersonal && (
                <button
                  type="button"
                  onClick={() => selectTeam(PERSONAL_ID)}
                  data-testid="dashboard-personal"
                  className={`flex w-full items-center justify-between gap-2 border-b px-3 py-2.5 text-left transition-colors last:border-b-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset ${
                    isPersonal ? "bg-primary/10 font-medium text-primary" : "hover:bg-muted/50"
                  }`}
                >
                  <span className="flex min-w-0 items-center gap-1.5 text-sm">
                    <span className="truncate">{t("byTeam.personal")}</span>
                    <Badge variant="outline" className="px-1 py-0 text-[9px] uppercase">Beta</Badge>
                  </span>
                  <Badge variant="secondary" className="shrink-0 text-[10px] px-1.5 py-0">{personalLabel}</Badge>
                </button>
              )}
              {(teams ?? []).map((team) => {
                const active = !isPersonal && selectedTeam?.team_id === team.team_id;
                return (
                  <button
                    key={team.team_id}
                    type="button"
                    onClick={() => selectTeam(team.team_id)}
                    className={`flex w-full items-center justify-between gap-2 border-b px-3 py-2.5 text-left transition-colors last:border-b-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset ${
                      active
                        ? "bg-primary/10 font-medium text-primary"
                        : "hover:bg-muted/50"
                    }`}
                  >
                    <span className="truncate text-sm">{team.team_alias}</span>
                    <Badge
                      variant="secondary"
                      className="shrink-0 text-[10px] px-1.5 py-0"
                    >
                      {teamModelLabel(team)}
                    </Badge>
                  </button>
                );
              })}
            </div>
          </div>

          {/* ── Right: selected team's (or the personal scope's) models ── */}
          <div className="min-w-0 flex-1">
            {(selectedTeam || isPersonal) && (
              <>
                <div className="rounded-lg border">
                  {/* Header */}
                  <div className="flex h-14 items-center justify-between gap-2 border-b px-4">
                    <div className="flex min-w-0 items-center gap-2">
                      <h2 className="truncate text-base font-semibold">{headerTitle}</h2>
                      <span className="text-sm text-muted-foreground">· {headerLabel}</span>
                      {isPersonal && (me?.max_budget != null || me?.tpm_limit != null || me?.rpm_limit != null) && (
                        <span className="hidden truncate text-xs text-muted-foreground sm:inline" data-testid="personal-limits">
                          {me?.max_budget != null ? `· $${me.max_budget}` : ""}
                          {me?.tpm_limit != null ? ` · TPM ${me.tpm_limit.toLocaleString()}` : ""}
                          {me?.rpm_limit != null ? ` · RPM ${me.rpm_limit.toLocaleString()}` : ""}
                        </span>
                      )}
                    </div>
                    {isPersonal ? (
                      me?.personal_keys_beta_enabled && (
                        <Button asChild variant="ghost" size="sm" className="h-8 text-muted-foreground">
                          <Link href="/keys/new?type=personal">
                            {t("byTeam.createPersonalKey")}
                            <ChevronRight className="size-4" />
                          </Link>
                        </Button>
                      )
                    ) : (
                      <Button asChild variant="ghost" size="sm" className="h-8 text-muted-foreground">
                        <Link href={`/teams/${selectedTeam!.team_id}`}>
                          {t("byTeam.teamDetail")}
                          <ChevronRight className="size-4" />
                        </Link>
                      </Button>
                    )}
                  </div>

                  {/* Toolbar — filters applied live */}
                  <div className="flex flex-wrap items-center gap-2 border-b bg-muted/30 px-4 py-2.5">
                    <div className="relative min-w-[180px] max-w-xs flex-1">
                      <Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
                      <Input
                        placeholder={t("filters.modelNamePlaceholder")}
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        className="h-9 pl-8 pr-8"
                      />
                      {query && (
                        <button
                          type="button"
                          onClick={() => setQuery("")}
                          aria-label={t("filters.reset")}
                          className="absolute right-2 top-1/2 -translate-y-1/2 rounded-sm text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        >
                          <X className="size-3.5" />
                        </button>
                      )}
                    </div>

                    <Select
                      value={statusFilter || "__all__"}
                      onValueChange={(v) => setStatusFilter(v === "__all__" ? "" : v)}
                    >
                      <SelectTrigger className="h-9 w-[150px] bg-background">
                        <SelectValue placeholder={t("filters.all")} />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="__all__">{t("filters.all")}</SelectItem>
                        {STATUS_OPTIONS.map((opt) => (
                          <SelectItem key={opt.value} value={opt.value}>
                            <StatusLabel status={opt.value} />
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>

                    {hasActiveFilters && (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={resetFilters}
                        className="h-9 text-muted-foreground"
                      >
                        {t("filters.reset")}
                      </Button>
                    )}
                  </div>

                  {/* Table / empty */}
                  {teamRows.length === 0 ? (
                    <div className="flex flex-col items-center justify-center py-16 text-center">
                      <Boxes className="mb-3 size-10 text-muted-foreground" />
                      <p className="text-muted-foreground">
                        {hasActiveFilters ? t("empty.noResults") : t("byTeam.noModels")}
                      </p>
                    </div>
                  ) : (
                    <ModelTable rows={teamRows} />
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      )}

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

// Status label used inside the filter dropdown (needs the modelStatus namespace).
function StatusLabel({ status }: { status: ModelStatus }) {
  const tms = useTranslations("modelStatus");
  return <>{tms(status)}</>;
}
