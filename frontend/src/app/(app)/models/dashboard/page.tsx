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
import { useLocaleTag } from "@/lib/locale";

import { useModels, useMyTeams } from "@/hooks/use-api";
import { ModelIcon } from "@/components/model-icon";
import { ModalityValue } from "@/components/model-modality";
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ModelDetailSheet } from "@/components/model-detail-sheet";
import type {
  ModelStatus,
  ModelWithCatalog,
  ModelCatalog,
  Team,
} from "@/types";

// ─── Constants ────────────────────────────────────────────────

const ALL_PROXY_MODELS = "all-proxy-models";

const STATUS_OPTIONS: { value: ModelStatus }[] = [
  { value: "testing" },
  { value: "prerelease" },
  { value: "lts" },
  { value: "deprecating" },
  { value: "deprecated" },
];

const STATUS_STYLES: Record<ModelStatus, string> = {
  testing:
    "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
  prerelease:
    "bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400",
  lts: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400",
  deprecating:
    "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400",
  deprecated:
    "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",
};

const STATUS_INDEX: Record<ModelStatus, number> = {
  testing: 0,
  prerelease: 1,
  lts: 2,
  deprecating: 3,
  deprecated: 4,
};

// ─── Helpers ──────────────────────────────────────────────────

function StatusBadge({ status }: { status: ModelStatus }) {
  const tms = useTranslations("modelStatus");
  return <Badge className={STATUS_STYLES[status]}>{tms(status)}</Badge>;
}

function formatDate(dateStr: string | null | undefined, localeTag: string): string {
  if (!dateStr) return "-";
  return new Date(dateStr).toLocaleDateString(localeTag, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
}

function formatCost(cost: number | null | undefined): string {
  if (cost == null) return "-";
  if (cost === 0) return "$ 0";
  return `$ ${(cost * 1_000_000).toFixed(2)} / 1M`;
}

/** Context window = max input tokens (falling back to max tokens), as "N tok". */
function formatContext(model: ModelWithCatalog | null): string {
  const info = model?.litellm_info?.model_info;
  const ctx = info?.max_input_tokens ?? info?.max_tokens ?? null;
  return ctx != null ? `${ctx.toLocaleString()} tok` : "-";
}

function getNextTransition(catalog: ModelCatalog | null): { date: string; status: ModelStatus } | null {
  if (!catalog?.status_schedule) return null;

  const today = new Date();
  today.setHours(0, 0, 0, 0);

  const currentStatusIndex = STATUS_INDEX[catalog.status];
  let nextDate: string | null = null;
  let nextStatus: ModelStatus | null = null;
  let nextTimestamp = Number.POSITIVE_INFINITY;

  for (const { value } of STATUS_OPTIONS) {
    if (STATUS_INDEX[value] <= currentStatusIndex) continue;

    const dateStr = catalog.status_schedule[value];
    if (!dateStr) continue;

    const parsed = new Date(`${dateStr}T00:00:00`);
    const timestamp = parsed.getTime();

    if (Number.isNaN(timestamp) || parsed <= today) continue;
    if (timestamp < nextTimestamp) {
      nextTimestamp = timestamp;
      nextDate = dateStr;
      nextStatus = value;
    }
  }

  return nextDate && nextStatus ? { date: nextDate, status: nextStatus } : null;
}

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
  const localeTag = useLocaleTag();
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
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead className="min-w-[200px]">{t("table.modelName")}</TableHead>
                          <TableHead className="w-[100px]">{t("table.status")}</TableHead>
                          <TableHead className="whitespace-nowrap">{t("table.modality")}</TableHead>
                          <TableHead>{t("table.inputCost")}</TableHead>
                          <TableHead>{t("table.outputCost")}</TableHead>
                          <TableHead className="w-[120px]">{t("table.context")}</TableHead>
                          <TableHead className="w-[170px]">{t("table.nextTransition")}</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {teamRows.map(({ name, model }) => (
                          <TableRow key={name}>
                            <TableCell>
                              {model ? (
                                <Link
                                  href={`/models/${model.model_name.split("/").map(encodeURIComponent).join("/")}`}
                                  className="flex items-center gap-2 text-left hover:underline"
                                >
                                  <ModelIcon
                                    iconUrl={model.catalog?.icon_url}
                                    provider={model.litellm_info?.model_info?.litellm_provider}
                                    modelName={model.model_name}
                                  />
                                  <div className="max-w-[280px] truncate text-sm font-medium">
                                    {model.catalog?.display_name ?? model.model_name}
                                  </div>
                                </Link>
                              ) : (
                                <div className="flex items-center gap-2">
                                  <ModelIcon modelName={name} />
                                  <div className="max-w-[280px] truncate text-sm font-medium text-muted-foreground">
                                    {name}
                                  </div>
                                  <Badge variant="outline" className="text-[10px] px-1.5 py-0 text-muted-foreground">
                                    {t("byTeam.notDeployed")}
                                  </Badge>
                                </div>
                              )}
                            </TableCell>
                            <TableCell>
                              {model?.catalog ? (
                                <StatusBadge status={model.catalog.status} />
                              ) : (
                                <span className="text-xs text-muted-foreground">-</span>
                              )}
                            </TableCell>
                            <TableCell className="whitespace-nowrap">
                              {model?.litellm_info ? (
                                <ModalityValue info={model.litellm_info.model_info} size="size-4" />
                              ) : (
                                <span className="text-xs text-muted-foreground">-</span>
                              )}
                            </TableCell>
                            <TableCell className="font-mono text-xs text-muted-foreground">
                              {formatCost(model?.litellm_info?.model_info?.input_cost_per_token)}
                            </TableCell>
                            <TableCell className="font-mono text-xs text-muted-foreground">
                              {formatCost(model?.litellm_info?.model_info?.output_cost_per_token)}
                            </TableCell>
                            <TableCell className="font-mono text-xs text-muted-foreground">
                              {formatContext(model)}
                            </TableCell>
                            <TableCell>
                              {(() => {
                                const next = getNextTransition(model?.catalog ?? null);
                                if (!next) return <span className="text-sm">-</span>;
                                return (
                                  <div className="flex items-center gap-1.5 whitespace-nowrap">
                                    <span className="text-sm">{formatDate(next.date, localeTag)}</span>
                                    <StatusBadge status={next.status} />
                                  </div>
                                );
                              })()}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
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
