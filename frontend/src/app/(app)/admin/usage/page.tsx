"use client";

import { Fragment, useEffect, useState } from "react";
import { Search, Loader2, BarChart3, ChevronLeft, ChevronRight, X, CalendarDays, List } from "lucide-react";
import { useTranslations } from "next-intl";
import { useLocaleTag } from "@/lib/locale";

import { useAdminUsage } from "@/hooks/use-api";
import { InputTokens } from "@/components/input-tokens";
import { MemberModelUsage } from "@/components/member-model-usage";
import { UsageCalendar } from "@/components/usage-calendar";
import { presetRange, type UsagePreset } from "@/lib/usage";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const PAGE_SIZE_OPTIONS = [10, 30, 50, 100, 300] as const;
const DEFAULT_PAGE_SIZE = 50;

type SortField = "user_id" | "team" | "total_tokens" | "api_requests" | "spend";

export default function AdminUsagePage() {
  const t = useTranslations("adminUsage");
  const localeTag = useLocaleTag();

  const [preset, setPreset] = useState<UsagePreset>("30d");
  const initial = presetRange("30d")!;
  const [startDate, setStartDate] = useState(initial.start);
  const [endDate, setEndDate] = useState(initial.end);

  const [teamId, setTeamId] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [sortField, setSortField] = useState<SortField>("spend");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<number>(DEFAULT_PAGE_SIZE);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [view, setView] = useState<"table" | "calendar">("table");

  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch(searchInput);
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [searchInput]);

  const applyPreset = (p: UsagePreset) => {
    setPreset(p);
    const r = presetRange(p);
    if (r) {
      setStartDate(r.start);
      setEndDate(r.end);
    }
    setPage(1);
  };

  // Click a calendar day → drill into that day's per-user×team rows in the table.
  const handlePickDay = (day: string) => {
    setPreset("custom");
    setStartDate(day);
    setEndDate(day);
    setPage(1);
    setView("table");
  };

  const { data, isLoading } = useAdminUsage(
    startDate,
    endDate,
    teamId,
    search,
    sortField,
    sortDir,
    page,
    pageSize,
  );
  const totalPages = data ? Math.ceil(data.total / pageSize) : 0;

  const toggleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortField(field);
      setSortDir("desc");
    }
    setPage(1);
  };
  const sortMark = (field: SortField) =>
    sortField === field ? (sortDir === "asc" ? " ↑" : " ↓") : "";

  const PRESETS: { value: UsagePreset; label: string }[] = [
    { value: "today", label: t("usagePresetToday") },
    { value: "7d", label: t("usagePreset7d") },
    { value: "month", label: t("usagePresetMonth") },
    { value: "30d", label: t("usagePreset30d") },
  ];

  const hasFilters = !!(search || teamId);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">{t("pageTitle")}</h1>
        <p className="text-muted-foreground mt-1">{t("pageDescription")}</p>
      </div>

      {/* View toggle */}
      <div className="flex items-center gap-1">
        <Button variant={view === "table" ? "default" : "outline"} size="sm" onClick={() => setView("table")}>
          <List className="size-4 mr-1" />{t("viewTable")}
        </Button>
        <Button variant={view === "calendar" ? "default" : "outline"} size="sm" onClick={() => setView("calendar")}>
          <CalendarDays className="size-4 mr-1" />{t("viewCalendar")}
        </Button>
      </div>

      {/* Date range (table view only) */}
      {view === "table" && (
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
                setPage(1);
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
                setPage(1);
              }}
            />
          </div>
        </div>
      </div>
      )}

      <div className="flex flex-wrap items-center gap-3">
        {view === "table" && (
          <div className="relative flex-1 max-w-sm">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" />
            <Input
              placeholder={t("searchPlaceholder")}
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              className="pl-8 h-9"
            />
          </div>
        )}
        <select
          className="h-9 rounded-md border border-input bg-background px-2 text-sm"
          value={teamId}
          onChange={(e) => {
            setTeamId(e.target.value);
            setPage(1);
          }}
        >
          <option value="">{t("teamAll")}</option>
          {data?.teams.map((tm) => (
            <option key={tm.team_id} value={tm.team_id}>
              {tm.team_alias || tm.team_id}
            </option>
          ))}
        </select>
        {hasFilters && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setSearchInput("");
              setSearch("");
              setTeamId("");
              setPage(1);
            }}
          >
            <X className="size-3.5 mr-1" />
            {t("reset")}
          </Button>
        )}
      </div>

      {view === "calendar" ? (
        <UsageCalendar teamId={teamId} onPickDay={handlePickDay} />
      ) : (
      <>
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

      {/* Per user×team table */}
      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="size-6 animate-spin text-muted-foreground" />
        </div>
      ) : !data || data.rows.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-lg border border-dashed py-16">
          <BarChart3 className="size-10 text-muted-foreground mb-3" />
          <p className="text-muted-foreground">{t("usageEmpty")}</p>
        </div>
      ) : (
        <>
          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>
                    <button type="button" className="hover:text-foreground" onClick={() => toggleSort("user_id")}>
                      {t("colUser")}{sortMark("user_id")}
                    </button>
                  </TableHead>
                  <TableHead>
                    <button type="button" className="hover:text-foreground" onClick={() => toggleSort("team")}>
                      {t("colTeam")}{sortMark("team")}
                    </button>
                  </TableHead>
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
                {data.rows.map((r) => {
                  const rowKey = `${r.team_id ?? "_"}::${r.user_id}`;
                  const canExpand = !!r.team_id;
                  const isOpen = expanded === rowKey;
                  return (
                    <Fragment key={rowKey}>
                      <TableRow
                        className={canExpand ? "cursor-pointer hover:bg-muted/50" : ""}
                        onClick={() => canExpand && setExpanded(isOpen ? null : rowKey)}
                      >
                        <TableCell>
                          <span className="inline-flex items-center gap-1">
                            {canExpand && (
                              <ChevronRight className={`size-3.5 transition-transform ${isOpen ? "rotate-90" : ""}`} />
                            )}
                            <span className="flex flex-col">
                              <span className="font-medium">{r.display_name || r.email || r.user_id}</span>
                              <span className="text-xs text-muted-foreground font-mono">{r.user_id}</span>
                            </span>
                          </span>
                        </TableCell>
                        <TableCell>
                          <span className="flex flex-col">
                            <span>{r.team_alias || t("noTeam")}</span>
                            {r.team_id && (
                              <span className="text-xs text-muted-foreground font-mono">{r.team_id}</span>
                            )}
                          </span>
                        </TableCell>
                        <TableCell className="text-right tabular-nums">{r.api_requests.toLocaleString(localeTag)}</TableCell>
                        <TableCell className="text-right">
                          <InputTokens input={r.input_tokens} cacheRead={r.cache_read_tokens} />
                        </TableCell>
                        <TableCell className="text-right tabular-nums">{r.output_tokens.toLocaleString(localeTag)}</TableCell>
                        <TableCell className="text-right tabular-nums">${r.spend.toFixed(2)}</TableCell>
                      </TableRow>
                      {isOpen && r.team_id && (
                        <TableRow className="hover:bg-transparent">
                          <TableCell colSpan={6} className="bg-muted/30 p-0">
                            <MemberModelUsage
                              teamId={r.team_id}
                              userId={r.user_id}
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

          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <p className="text-sm text-muted-foreground">
                {t("pagination", {
                  total: data.total,
                  start: (page - 1) * pageSize + 1,
                  end: Math.min(page * pageSize, data.total),
                })}
              </p>
              <select
                className="h-8 rounded-md border border-input bg-background px-2 text-sm"
                value={pageSize}
                onChange={(e) => {
                  setPageSize(Number(e.target.value));
                  setPage(1);
                }}
              >
                {PAGE_SIZE_OPTIONS.map((size) => (
                  <option key={size} value={size}>
                    {t("perPage", { size })}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" disabled={page === 1} onClick={() => setPage(page - 1)}>
                <ChevronLeft className="size-4" />
                {t("prev")}
              </Button>
              <span className="text-sm text-muted-foreground">
                {page} / {totalPages || 1}
              </span>
              <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>
                {t("next")}
                <ChevronRight className="size-4" />
              </Button>
            </div>
          </div>
        </>
      )}
      </>
      )}
    </div>
  );
}
