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

import { useModels, useMyTeams } from "@/hooks/use-api";
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

// ─── Constants ────────────────────────────────────────────────

const ALL_PROXY_MODELS = "all-proxy-models";

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
type ModelRow = { name: string; model: ModelWithCatalog | null };

// ─── Main Component ───────────────────────────────────────────

export default function ModelDashboardPage() {
  const t = useTranslations("modelsDashboard");

  // Data fetching
  const { data: teams, isLoading: teamsLoading, isError: teamsError } = useMyTeams();
  const { data: models, isLoading: modelsLoading } = useModels();
  const [detailModel, setDetailModel] = useState<ModelWithCatalog | null>(null);

  const modelsByName = useMemo(
    () => new Map((models ?? []).map((m) => [m.model_name, m])),
    [models],
  );

  // Selected team (defaults to the first team once loaded)
  const [selectedTeamId, setSelectedTeamId] = useState<string | null>(null);
  const selectedTeam = useMemo<Team | null>(() => {
    if (!teams || teams.length === 0) return null;
    return teams.find((t) => t.team_id === selectedTeamId) ?? teams[0];
  }, [teams, selectedTeamId]);

  // Filter state for the model table (applied live)
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("");

  // ── Rows for the selected team ──
  const teamRows = useMemo<ModelRow[]>(() => {
    if (!selectedTeam) return [];

    let rows: ModelRow[];
    if (hasAllModels(selectedTeam)) {
      rows = (models ?? [])
        .filter((m) => m.catalog && m.catalog.visible !== false)
        .map((m) => ({ name: m.model_name, model: m }));
    } else {
      rows = explicitModels(selectedTeam).map((name) => ({
        name,
        model: modelsByName.get(name) ?? null,
      }));
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
  }, [selectedTeam, models, modelsByName, query, statusFilter]);

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
      : t("byTeam.modelCount", { count: explicitModels(team).length });
  }

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
      ) : !teams || teams.length === 0 ? (
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
              {teams.map((team) => {
                const active = selectedTeam?.team_id === team.team_id;
                return (
                  <button
                    key={team.team_id}
                    type="button"
                    onClick={() => selectTeam(team.team_id)}
                    className={`flex w-full items-center justify-between gap-2 border-b px-3 py-2.5 text-left last:border-b-0 transition-colors ${
                      active
                        ? "bg-muted font-medium"
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

          {/* ── Right: selected team's models ── */}
          <div className="min-w-0 flex-1">
            {selectedTeam && (
              <>
                <div className="rounded-lg border">
                  {/* Header */}
                  <div className="flex h-14 items-center justify-between gap-2 border-b px-4">
                    <div className="flex min-w-0 items-center gap-2">
                      <h2 className="truncate text-base font-semibold">{selectedTeam.team_alias}</h2>
                      <span className="text-sm text-muted-foreground">
                        · {teamModelLabel(selectedTeam)}
                      </span>
                    </div>
                    <Button asChild variant="ghost" size="sm" className="h-8 text-muted-foreground">
                      <Link href={`/teams/${selectedTeam.team_id}`}>
                        {t("byTeam.teamDetail")}
                        <ChevronRight className="size-4" />
                      </Link>
                    </Button>
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
                          className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
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
