"use client";

import { useState, useMemo } from "react";
import {
  ArrowRight,
  History,
  Search,
  Loader2,
  Clock,
  Activity,
  TrendingUp,
  Hash,
  ChevronLeft,
  ChevronRight,
  X,
  CalendarClock,
  Calendar,
  ArrowUpRight,
  ArrowDownRight,
} from "lucide-react";

import {
  useAllModelStatusHistory,
  useModelCatalog,
  useModels,
  useModelStatusHistory,
} from "@/hooks/use-api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
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
import { Separator } from "@/components/ui/separator";
import { ModelDetailSheet } from "@/components/model-detail-sheet";
import type { ModelStatus, ModelStatusHistory, ModelCatalog, ModelWithCatalog } from "@/types";

// ─── Constants ────────────────────────────────────────────────

const PAGE_SIZE = 50;

const STATUS_OPTIONS: { value: ModelStatus; label: string }[] = [
  { value: "testing", label: "Testing" },
  { value: "prerelease", label: "Prerelease" },
  { value: "lts", label: "LTS" },
  { value: "deprecating", label: "Deprecating" },
  { value: "deprecated", label: "Deprecated" },
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

/** Solid Tailwind bg classes for the timeline bar segments */
const STATUS_BAR_COLORS: Record<ModelStatus, string> = {
  testing: "bg-blue-400",
  prerelease: "bg-purple-400",
  lts: "bg-green-400",
  deprecating: "bg-yellow-400",
  deprecated: "bg-red-400",
};

/** Dot colors for calendar events */
const STATUS_DOT_COLORS: Record<ModelStatus, string> = {
  testing: "bg-blue-500",
  prerelease: "bg-purple-500",
  lts: "bg-green-500",
  deprecating: "bg-yellow-500",
  deprecated: "bg-red-500",
};

const STATUS_BORDER_COLORS: Record<ModelStatus, string> = {
  testing: "border-blue-500",
  prerelease: "border-purple-500",
  lts: "border-green-500",
  deprecating: "border-yellow-500",
  deprecated: "border-red-500",
};

const STATUS_LABELS: Record<ModelStatus, string> = {
  testing: "Testing",
  prerelease: "Prerelease",
  lts: "LTS",
  deprecating: "Deprecating",
  deprecated: "Deprecated",
};

const DAY_NAMES = ["월", "화", "수", "목", "금", "토", "일"];

const ALL_STATUSES: ModelStatus[] = ["testing", "prerelease", "lts", "deprecating", "deprecated"];

// ─── Helpers ──────────────────────────────────────────────────

function StatusBadge({ status }: { status: ModelStatus }) {
  return <Badge className={STATUS_STYLES[status]}>{status}</Badge>;
}

function formatDateTime(dateStr: string | null | undefined): string {
  if (!dateStr) return "-";
  return new Date(dateStr).toLocaleDateString("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatDate(dateStr: string | null | undefined): string {
  if (!dateStr) return "-";
  return new Date(dateStr).toLocaleDateString("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
}

function formatRelativeTime(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return "방금 전";
  if (minutes < 60) return `${minutes}분 전`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}시간 전`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}일 전`;
  return formatDateTime(dateStr);
}

function daysUntil(dateStr: string): number {
  return Math.ceil(
    (new Date(dateStr).getTime() - Date.now()) / (24 * 60 * 60 * 1000),
  );
}

// ─── Calendar Helpers ─────────────────────────────────────────

interface CalendarEvent {
  model_name: string;
  display_name: string;
  type: "status_change" | "scheduled_status";
  status: ModelStatus;
  from_status?: ModelStatus | null;
  description: string;
}

/** Build a grid of weeks for a given month. Week starts Monday. */
function buildCalendarGrid(year: number, month: number): (number | null)[][] {
  const firstDay = new Date(year, month, 1);
  const lastDay = new Date(year, month + 1, 0);
  const totalDays = lastDay.getDate();
  // Monday=0 ... Sunday=6
  const startOffset = (firstDay.getDay() + 6) % 7;
  const weeks: (number | null)[][] = [];
  let week: (number | null)[] = [];

  for (let i = 0; i < startOffset; i++) week.push(null);
  for (let d = 1; d <= totalDays; d++) {
    week.push(d);
    if (week.length === 7) {
      weeks.push(week);
      week = [];
    }
  }
  if (week.length > 0) {
    while (week.length < 7) week.push(null);
    weeks.push(week);
  }

  return weeks;
}

// ─── Timeline Segment Types ──────────────────────────────────

interface TimelineSegment {
  status: ModelStatus;
  startTime: number;
  endTime: number;
  isCurrent: boolean;
  isFuture: boolean;
}

function buildTimelineSegments(
  historyAsc: ModelStatusHistory[],
  deprecatedAt: string | null,
): TimelineSegment[] {
  if (historyAsc.length === 0) return [];

  const now = Date.now();
  const segments: TimelineSegment[] = [];

  for (let i = 0; i < historyAsc.length; i++) {
    const entry = historyAsc[i];
    const start = new Date(entry.changed_at).getTime();
    const nextEntry = historyAsc[i + 1];
    const end = nextEntry ? new Date(nextEntry.changed_at).getTime() : now;
    const isCurrent = !nextEntry;

    segments.push({
      status: entry.new_status,
      startTime: start,
      endTime: end,
      isCurrent,
      isFuture: false,
    });
  }

  // Add future segment for scheduled deprecated date
  if (deprecatedAt) {
    const deprecateTime = new Date(deprecatedAt).getTime();
    if (deprecateTime > now) {
      segments.push({
        status: "deprecated",
        startTime: now,
        endTime: deprecateTime,
        isCurrent: false,
        isFuture: true,
      });
    }
  }

  return segments;
}

// ─── Stat Card ────────────────────────────────────────────────

function StatCard({
  title,
  value,
  icon: Icon,
  description,
}: {
  title: string;
  value: number | string;
  icon: typeof Activity;
  description?: string;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">
          {title}
        </CardTitle>
        <Icon className="size-4 text-muted-foreground" />
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold">{value}</div>
        {description && (
          <p className="text-xs text-muted-foreground mt-1">{description}</p>
        )}
      </CardContent>
    </Card>
  );
}

// ─── Model Lifecycle Timeline ─────────────────────────────────

function ModelLifecycleTimeline({
  catalog,
  historyEntries,
  isLoading,
}: {
  catalog: ModelCatalog;
  historyEntries: ModelStatusHistory[];
  isLoading: boolean;
}) {
  // Sort ascending (oldest first) for timeline building
  const historyAsc = useMemo(
    () => [...historyEntries].sort(
      (a, b) => new Date(a.changed_at).getTime() - new Date(b.changed_at).getTime(),
    ),
    [historyEntries],
  );

  const deprecatedAt = catalog.status_schedule?.deprecated ?? null;

  const upcomingScheduledStatuses = useMemo(() => {
    const scheduleEntries = Object.entries(catalog.status_schedule ?? {}) as [
      ModelStatus,
      string,
    ][];

    return scheduleEntries
      .filter(([, dateStr]) => !!dateStr)
      .map(([status, dateStr]) => {
        const timestamp = new Date(dateStr).getTime();
        return {
          status,
          dateStr,
          timestamp,
        };
      })
      .filter((entry) => !Number.isNaN(entry.timestamp) && entry.timestamp > Date.now())
      .sort((a, b) => a.timestamp - b.timestamp);
  }, [catalog.status_schedule]);

  const segments = useMemo(
    () => buildTimelineSegments(historyAsc, deprecatedAt),
    [historyAsc, deprecatedAt],
  );

  // Calculate total timespan for proportional widths
  const totalSpan = useMemo(() => {
    if (segments.length === 0) return 1;
    const first = segments[0].startTime;
    const last = segments[segments.length - 1].endTime;
    return Math.max(last - first, 1);
  }, [segments]);

  const firstTime = segments.length > 0 ? segments[0].startTime : Date.now();

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-8">
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {/* Current status + scheduled deprecated info */}
      <div className="flex flex-wrap items-center gap-4">
        <div className="flex items-center gap-2">
          <span className="text-sm text-muted-foreground">현재 상태:</span>
          <StatusBadge status={catalog.status} />
        </div>
        {deprecatedAt && (
          <div className="flex items-center gap-2">
            <CalendarClock className="size-3.5 text-muted-foreground" />
            <span className="text-sm text-muted-foreground">폐기 예정:</span>
            <span className="text-sm font-medium">
              {formatDate(deprecatedAt)}
            </span>
            {daysUntil(deprecatedAt) > 0 ? (
              <Badge variant="outline" className="text-xs">
                {daysUntil(deprecatedAt)}일 후
              </Badge>
            ) : (
              <Badge variant="destructive" className="text-xs">
                기한 경과
              </Badge>
            )}
          </div>
        )}
      </div>

      {/* Horizontal timeline bar */}
      {segments.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-medium text-muted-foreground">상태 타임라인</p>
          <div className="relative">
            {/* Bar */}
            <div className="flex h-10 w-full overflow-hidden rounded-lg border">
              {segments.map((seg, i) => {
                const width = ((seg.endTime - seg.startTime) / totalSpan) * 100;
                const minWidth = Math.max(width, 3); // Minimum 3% for visibility
                return (
                  <div
                    key={i}
                    className={`relative flex items-center justify-center text-[11px] font-medium text-white transition-all ${STATUS_BAR_COLORS[seg.status]} ${seg.isFuture ? "opacity-40" : ""}`}
                    style={{ width: `${minWidth}%`, minWidth: "24px" }}
                    title={`${seg.status} (${formatDate(new Date(seg.startTime).toISOString())} ~ ${seg.isCurrent ? "현재" : formatDate(new Date(seg.endTime).toISOString())})`}
                  >
                    {minWidth > 8 && (
                      <span className="truncate px-1">
                        {seg.status}
                        {seg.isFuture ? " (예정)" : seg.isCurrent ? " ●" : ""}
                      </span>
                    )}
                    {seg.isFuture && (
                      <div
                        className="absolute inset-0 opacity-30"
                        style={{
                          backgroundImage:
                            "repeating-linear-gradient(135deg, transparent, transparent 4px, rgba(255,255,255,0.3) 4px, rgba(255,255,255,0.3) 8px)",
                        }}
                      />
                    )}
                  </div>
                );
              })}
            </div>
            {/* Date labels under the bar */}
            <div className="flex justify-between mt-1">
              <span className="text-[10px] text-muted-foreground font-mono">
                {formatDate(new Date(firstTime).toISOString())}
              </span>
              <span className="text-[10px] text-muted-foreground font-mono">
                {segments[segments.length - 1]?.isFuture
                  ? formatDate(new Date(segments[segments.length - 1].endTime).toISOString())
                  : "현재"}
              </span>
            </div>
          </div>
        </div>
      )}

      {/* Vertical event timeline */}
      <div className="space-y-2">
        <p className="text-xs font-medium text-muted-foreground">변경 이벤트</p>
        <div className="relative pl-6">
          {/* Vertical line */}
          <div className="absolute left-[9px] top-2 bottom-2 w-px bg-border" />

          {/* Future scheduled status events */}
          {upcomingScheduledStatuses.map((scheduled) => (
            <div
              key={`${scheduled.status}-${scheduled.dateStr}`}
              className="relative flex items-start gap-3 pb-4"
            >
              <div
                className={`absolute left-[-15px] top-1.5 size-[7px] rounded-full border-2 bg-white ${STATUS_BORDER_COLORS[scheduled.status]}`}
              />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <Badge
                    variant="outline"
                    className="text-[10px] border-dashed"
                  >
                    예정
                  </Badge>
                  <ArrowRight className="size-3 text-muted-foreground" />
                  <StatusBadge status={scheduled.status} />
                </div>
                <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
                  <CalendarClock className="size-3" />
                  <span>{formatDate(scheduled.dateStr)}</span>
                  <span className="text-orange-500 font-medium">
                    ({daysUntil(scheduled.dateStr)}일 후)
                  </span>
                </div>
              </div>
            </div>
          ))}

          {/* Past events (newest first) */}
          {[...historyEntries]
            .sort(
              (a, b) =>
                new Date(b.changed_at).getTime() -
                new Date(a.changed_at).getTime(),
            )
            .map((h, i, arr) => (
              <div key={h.id} className="relative flex items-start gap-3 pb-4">
                <div
                  className={`absolute left-[-15px] top-1.5 size-[7px] rounded-full ${i === 0 ? "bg-foreground ring-2 ring-foreground/20" : "bg-muted-foreground/50"}`}
                />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5 flex-wrap">
                    {h.previous_status ? (
                      <>
                        <StatusBadge status={h.previous_status} />
                        <ArrowRight className="size-3 text-muted-foreground shrink-0" />
                        <StatusBadge status={h.new_status} />
                      </>
                    ) : (
                      <>
                        <span className="text-xs text-muted-foreground">
                          생성
                        </span>
                        <ArrowRight className="size-3 text-muted-foreground shrink-0" />
                        <StatusBadge status={h.new_status} />
                      </>
                    )}
                    {i === 0 && (
                      <Badge
                        variant="outline"
                        className="text-[10px] ml-1"
                      >
                        최신
                      </Badge>
                    )}
                  </div>
                  <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
                    <span>{h.changed_by}</span>
                    <span>·</span>
                    <span>{formatDateTime(h.changed_at)}</span>
                    <span className="text-muted-foreground/50">
                      ({formatRelativeTime(h.changed_at)})
                    </span>
                  </div>
                </div>
              </div>
            ))}

          {historyEntries.length === 0 && (
            <div className="py-4 text-sm text-muted-foreground">
              변경 이력이 없습니다.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── Main Component ───────────────────────────────────────────

export default function StatusHistoryDashboardPage() {
  // Model selector state
  const [selectedCatalogId, setSelectedCatalogId] = useState<string>("");
  const { data: catalogList } = useModelCatalog();
  const { data: models } = useModels();
  const [detailModel, setDetailModel] = useState<ModelWithCatalog | null>(null);
  const selectedCatalog = catalogList?.find((c) => c.id === selectedCatalogId);
  const { data: selectedHistory, isLoading: historyLoading } =
    useModelStatusHistory(selectedCatalogId || undefined);

  // Calendar state
  const [calendarMonth, setCalendarMonth] = useState<Date>(() => {
    const now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), 1);
  });

  const calendarRange = useMemo(() => {
    const y = calendarMonth.getFullYear();
    const m = calendarMonth.getMonth();
    return {
      date_from: new Date(y, m, 1).toISOString(),
      date_to: new Date(y, m + 1, 1).toISOString(),
    };
  }, [calendarMonth]);

  const { data: calendarData, isLoading: calendarLoading } = useAllModelStatusHistory({
    ...calendarRange,
    limit: 500,
  });

  const calendarGrid = useMemo(
    () => buildCalendarGrid(calendarMonth.getFullYear(), calendarMonth.getMonth()),
    [calendarMonth],
  );

  const dayEventsMap = useMemo(() => {
    const map = new Map<number, CalendarEvent[]>();
    const year = calendarMonth.getFullYear();
    const month = calendarMonth.getMonth();

    function addEvent(day: number, event: CalendarEvent) {
      const existing = map.get(day) ?? [];
      existing.push(event);
      map.set(day, existing);
    }

    // From history entries (status changes in this month)
    if (calendarData?.history) {
      for (const h of calendarData.history) {
        const date = new Date(h.changed_at);
        if (date.getFullYear() === year && date.getMonth() === month) {
          const cat = catalogList?.find((c) => c.model_name === h.model_name);
          addEvent(date.getDate(), {
            model_name: h.model_name,
            display_name: cat?.display_name ?? h.model_name,
            type: "status_change",
            status: h.new_status,
            from_status: h.previous_status,
            description: h.previous_status
              ? `${cat?.display_name ?? h.model_name}: ${h.previous_status} → ${h.new_status}`
              : `${cat?.display_name ?? h.model_name}: 생성 (${h.new_status})`,
          });
        }
      }
    }

    // From catalog: status_schedule dates in this month
    if (catalogList) {
      for (const c of catalogList) {
        const scheduleEntries = Object.entries(c.status_schedule ?? {}) as [
          ModelStatus,
          string,
        ][];
        for (const [status, dateStr] of scheduleEntries) {
          if (!dateStr) continue;

          const date = new Date(dateStr);
          if (date.getFullYear() === year && date.getMonth() === month) {
            addEvent(date.getDate(), {
              model_name: c.model_name,
              display_name: c.display_name,
              type: "scheduled_status",
              status,
              description: `${c.display_name}: ${STATUS_LABELS[status]} 예정`,
            });
          }
        }
      }
    }

    return map;
  }, [calendarData, catalogList, calendarMonth]);

  const statusCounts = useMemo(() => {
    if (!catalogList) return {} as Partial<Record<ModelStatus, number>>;
    const counts: Partial<Record<ModelStatus, number>> = {};
    for (const c of catalogList) {
      counts[c.status] = (counts[c.status] ?? 0) + 1;
    }
    return counts;
  }, [catalogList]);

  const incomingModels = useMemo(
    () => (catalogList ?? []).filter((c) => c.status === "testing" || c.status === "prerelease"),
    [catalogList],
  );

  const outgoingModels = useMemo(
    () =>
      (catalogList ?? []).filter(
        (c) =>
          c.status === "deprecating" ||
          c.status === "deprecated" ||
          (c.status_schedule?.deprecated &&
            new Date(c.status_schedule.deprecated).getTime() > Date.now()),
      ),
    [catalogList],
  );

  const today = new Date();
  const isCurrentMonth =
    calendarMonth.getFullYear() === today.getFullYear() &&
    calendarMonth.getMonth() === today.getMonth();

  // Filter state for all-history table
  const [modelNameInput, setModelNameInput] = useState("");
  const [modelNameFilter, setModelNameFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [changedByInput, setChangedByInput] = useState("");
  const [changedByFilter, setChangedByFilter] = useState("");
  const [page, setPage] = useState(0);

  // Fetch all history data
  const { data, isLoading, isError } = useAllModelStatusHistory({
    model_name: modelNameFilter || undefined,
    status_filter: statusFilter || undefined,
    changed_by: changedByFilter || undefined,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  });

  const history = data?.history ?? [];
  const total = data?.total ?? 0;

  // Compute stats
  const stats = useMemo(() => {
    const now = Date.now();
    const day = 24 * 60 * 60 * 1000;

    const last24h = history.filter(
      (h) => now - new Date(h.changed_at).getTime() < day,
    ).length;
    const last7d = history.filter(
      (h) => now - new Date(h.changed_at).getTime() < 7 * day,
    ).length;
    const uniqueModels = new Set(history.map((h) => h.model_name)).size;

    return { last24h, last7d, uniqueModels };
  }, [history]);

  // Pagination
  const totalPages = Math.ceil(total / PAGE_SIZE);
  const startItem = page * PAGE_SIZE + 1;
  const endItem = Math.min((page + 1) * PAGE_SIZE, total);

  // Filter handlers
  function applyFilters() {
    setModelNameFilter(modelNameInput.trim());
    setChangedByFilter(changedByInput.trim());
    setPage(0);
  }

  function resetFilters() {
    setModelNameInput("");
    setModelNameFilter("");
    setStatusFilter("");
    setChangedByInput("");
    setChangedByFilter("");
    setPage(0);
  }

  const hasActiveFilters = !!(
    modelNameFilter ||
    statusFilter ||
    changedByFilter
  );

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold">변경 이력 대시보드</h1>
        <p className="text-muted-foreground mt-1">
          모든 모델의 상태 변경 기록을 한눈에 확인합니다
        </p>
      </div>

      {/* ── 모델 캘린더 ── */}
      <Card>
        <CardHeader>
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
            <div>
              <CardTitle className="flex items-center gap-2 text-base">
                <Calendar className="size-4" />
                모델 캘린더
              </CardTitle>
              <CardDescription>
                전체 모델의 현재 상태와 예정된 일정을 확인합니다
              </CardDescription>
            </div>
            <div className="flex items-center gap-1.5">
              <Button
                variant="outline"
                size="sm"
                className="h-7 px-2"
                onClick={() =>
                  setCalendarMonth(
                    (prev) => new Date(prev.getFullYear(), prev.getMonth() - 1, 1),
                  )
                }
              >
                <ChevronLeft className="size-3.5" />
              </Button>
              <Button
                variant={isCurrentMonth ? "default" : "outline"}
                size="sm"
                className="h-7 px-3 text-xs font-medium min-w-[120px]"
                onClick={() => {
                  const now = new Date();
                  setCalendarMonth(new Date(now.getFullYear(), now.getMonth(), 1));
                }}
              >
                {calendarMonth.getFullYear()}년 {calendarMonth.getMonth() + 1}월
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="h-7 px-2"
                onClick={() =>
                  setCalendarMonth(
                    (prev) => new Date(prev.getFullYear(), prev.getMonth() + 1, 1),
                  )
                }
              >
                <ChevronRight className="size-3.5" />
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-5">
          {/* Status overview pills */}
          <div className="flex flex-wrap gap-2">
            {ALL_STATUSES.map((s) => {
              const count = statusCounts[s] ?? 0;
              return (
                <div
                  key={s}
                  className="flex items-center gap-1.5 rounded-full border px-3 py-1"
                >
                  <div className={`size-2 rounded-full ${STATUS_DOT_COLORS[s]}`} />
                  <span className="text-xs font-medium">{STATUS_LABELS[s]}</span>
                  <span className="text-xs text-muted-foreground">{count}</span>
                </div>
              );
            })}
          </div>

          {/* Calendar grid */}
          <div className="rounded-lg border overflow-hidden">
            {/* Day name headers */}
            <div className="grid grid-cols-7 border-b bg-muted/50">
              {DAY_NAMES.map((d) => (
                <div
                  key={d}
                  className="py-2 text-center text-xs font-medium text-muted-foreground"
                >
                  {d}
                </div>
              ))}
            </div>
            {/* Week rows */}
            {calendarLoading ? (
              <div className="flex items-center justify-center py-16">
                <Loader2 className="size-5 animate-spin text-muted-foreground" />
              </div>
            ) : (
              calendarGrid.map((week, wi) => (
                <div key={wi} className="grid grid-cols-7 border-b last:border-b-0">
                  {week.map((day, di) => {
                    const events = day ? dayEventsMap.get(day) ?? [] : [];
                    const isToday = isCurrentMonth && day === today.getDate();

                    return (
                      <div
                        key={di}
                        className={`relative min-h-[72px] p-1.5 border-r last:border-r-0 transition-colors ${
                          day ? "hover:bg-muted/30" : "bg-muted/10"
                        } ${isToday ? "bg-blue-50 dark:bg-blue-950/20" : ""}`}
                      >
                        {day && (
                          <>
                            <span
                              className={`text-xs font-medium ${
                                isToday
                                  ? "inline-flex size-5 items-center justify-center rounded-full bg-blue-600 text-white"
                                  : "text-muted-foreground"
                              }`}
                            >
                              {day}
                            </span>
                            {/* Event indicators */}
                            {events.length > 0 && (
                              <div className="mt-1 space-y-0.5">
                                {events.slice(0, 3).map((ev, ei) => (
                                  <div
                                    key={ei}
                                    className={`flex items-center gap-1 rounded px-1 py-0.5 min-w-0 overflow-hidden ${
                                      ev.type === "scheduled_status"
                                        ? "border border-dashed border-border/70 bg-muted/30"
                                        : "bg-muted/60"
                                    }`}
                                    title={ev.description}
                                  >
                                    <div
                                      className={`size-1.5 rounded-full shrink-0 ${STATUS_DOT_COLORS[ev.status]}`}
                                    />
                                    <button
                                      type="button"
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        const m = models?.find((x) => x.model_name === ev.model_name);
                                        if (m) setDetailModel(m);
                                      }}
                                      className="text-[10px] truncate leading-tight hover:underline cursor-pointer text-left"
                                    >
                                      {ev.display_name}
                                    </button>
                                  </div>
                                ))}
                                {events.length > 3 && (
                                  <span className="text-[10px] text-muted-foreground px-1">
                                    +{events.length - 3}
                                  </span>
                                )}
                              </div>
                            )}
                          </>
                        )}
                      </div>
                    );
                  })}
                </div>
              ))
            )}
          </div>

          {/* Incoming + Outgoing models */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {/* Incoming models */}
            <div className="rounded-lg border p-4">
              <div className="flex items-center gap-2 mb-3">
                <ArrowUpRight className="size-4 text-blue-500" />
                <h4 className="text-sm font-medium">도입 중인 모델</h4>
                <Badge variant="secondary" className="text-[10px]">
                  {incomingModels.length}
                </Badge>
              </div>
              {incomingModels.length === 0 ? (
                <p className="text-xs text-muted-foreground">도입 중인 모델이 없습니다</p>
              ) : (
                <div className="space-y-2">
                  {incomingModels.map((m) => (
                    <div key={m.id} className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2 min-w-0">
                        <div className={`size-2 rounded-full shrink-0 ${STATUS_DOT_COLORS[m.status]}`} />
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            const fullModel = models?.find((x) => x.model_name === m.model_name);
                            if (fullModel) setDetailModel(fullModel);
                          }}
                          className="text-sm font-mono truncate hover:underline cursor-pointer text-left"
                        >
                          {m.display_name}
                        </button>
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        <StatusBadge status={m.status} />
                        <span className="text-[10px] text-muted-foreground">{formatDate(m.created_at)}</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Outgoing models */}
            <div className="rounded-lg border p-4">
              <div className="flex items-center gap-2 mb-3">
                <ArrowDownRight className="size-4 text-red-500" />
                <h4 className="text-sm font-medium">폐기 예정 모델</h4>
                <Badge variant="secondary" className="text-[10px]">
                  {outgoingModels.length}
                </Badge>
              </div>
              {outgoingModels.length === 0 ? (
                <p className="text-xs text-muted-foreground">폐기 예정인 모델이 없습니다</p>
              ) : (
                <div className="space-y-2">
                  {outgoingModels.map((m) => (
                    <div key={m.id} className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2 min-w-0">
                        <div className={`size-2 rounded-full shrink-0 ${STATUS_DOT_COLORS[m.status]}`} />
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            const fullModel = models?.find((x) => x.model_name === m.model_name);
                            if (fullModel) setDetailModel(fullModel);
                          }}
                          className="text-sm font-mono truncate hover:underline cursor-pointer text-left"
                        >
                          {m.display_name}
                        </button>
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        <StatusBadge status={m.status} />
                        {m.status_schedule?.deprecated && (
                          <span className="text-[10px] text-muted-foreground">
                            {daysUntil(m.status_schedule.deprecated) > 0
                              ? `${daysUntil(m.status_schedule.deprecated)}일 후`
                              : "기한 경과"}
                          </span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </CardContent>
      </Card>

      {/* ── Model Lifecycle Timeline Section ── */}
      <Card>
        <CardHeader>
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
            <div>
              <CardTitle className="text-base">모델 라이프사이클</CardTitle>
              <CardDescription>
                모델을 선택하면 상태 변경 타임라인과 예정된 일정을 확인할 수
                있습니다
              </CardDescription>
            </div>
            <Select
              value={selectedCatalogId}
              onValueChange={setSelectedCatalogId}
            >
              <SelectTrigger className="w-full sm:w-[280px] h-9">
                <SelectValue placeholder="모델 선택..." />
              </SelectTrigger>
              <SelectContent>
                {catalogList?.map((c) => (
                  <SelectItem key={c.id} value={c.id}>
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-xs">
                        {c.display_name}
                      </span>
                      <Badge className={`${STATUS_STYLES[c.status]} text-[10px] px-1 py-0`}>
                        {c.status}
                      </Badge>
                    </div>
                  </SelectItem>
                ))}
                {(!catalogList || catalogList.length === 0) && (
                  <SelectItem value="__none__" disabled>
                    등록된 카탈로그가 없습니다
                  </SelectItem>
                )}
              </SelectContent>
            </Select>
          </div>
        </CardHeader>
        <CardContent>
          {!selectedCatalogId ? (
            <div className="flex flex-col items-center justify-center py-10 text-center">
              <CalendarClock className="size-10 text-muted-foreground/40 mb-3" />
              <p className="text-sm text-muted-foreground">
                위에서 모델을 선택하면 날짜별 상태 변경 타임라인을 확인할 수
                있습니다
              </p>
            </div>
          ) : selectedCatalog ? (
            <ModelLifecycleTimeline
              catalog={selectedCatalog}
              historyEntries={selectedHistory ?? []}
              isLoading={historyLoading}
            />
          ) : (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="size-5 animate-spin text-muted-foreground" />
            </div>
          )}
        </CardContent>
      </Card>

      <Separator />

      {/* ── All History Section ── */}

      {/* Stat Cards */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          title="전체 변경"
          value={total}
          icon={Activity}
          description="총 상태 변경 횟수"
        />
        <StatCard
          title="24시간"
          value={stats.last24h}
          icon={Clock}
          description="최근 24시간 변경"
        />
        <StatCard
          title="7일"
          value={stats.last7d}
          icon={TrendingUp}
          description="최근 7일 변경"
        />
        <StatCard
          title="모델 수"
          value={stats.uniqueModels}
          icon={Hash}
          description="변경된 고유 모델 수"
        />
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex-1 min-w-[200px] max-w-xs">
          <label className="text-xs font-medium text-muted-foreground mb-1.5 block">
            모델명
          </label>
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" />
            <Input
              placeholder="모델명 검색..."
              value={modelNameInput}
              onChange={(e) => setModelNameInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && applyFilters()}
              className="pl-8 h-9"
            />
          </div>
        </div>

        <div className="w-[160px]">
          <label className="text-xs font-medium text-muted-foreground mb-1.5 block">
            상태
          </label>
          <Select
            value={statusFilter}
            onValueChange={(v) => {
              setStatusFilter(v === "__all__" ? "" : v);
              setPage(0);
            }}
          >
            <SelectTrigger className="h-9">
              <SelectValue placeholder="전체" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">전체</SelectItem>
              {STATUS_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="min-w-[150px] max-w-[200px]">
          <label className="text-xs font-medium text-muted-foreground mb-1.5 block">
            변경자
          </label>
          <Input
            placeholder="사번..."
            value={changedByInput}
            onChange={(e) => setChangedByInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && applyFilters()}
            className="h-9"
          />
        </div>

        <Button size="sm" onClick={applyFilters} className="h-9">
          <Search className="size-3.5 mr-1" />
          검색
        </Button>

        {hasActiveFilters && (
          <Button
            size="sm"
            variant="ghost"
            onClick={resetFilters}
            className="h-9 text-muted-foreground"
          >
            <X className="size-3.5 mr-1" />
            초기화
          </Button>
        )}
      </div>

      {/* Table */}
      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="size-6 animate-spin text-muted-foreground" />
        </div>
      ) : isError ? (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          변경 이력을 불러오는 중 오류가 발생했습니다.
        </div>
      ) : history.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-lg border border-dashed py-16 text-center">
          <History className="size-10 text-muted-foreground mb-3" />
          <p className="text-muted-foreground">
            {hasActiveFilters
              ? "필터 조건에 맞는 변경 이력이 없습니다."
              : "아직 변경 이력이 없습니다."}
          </p>
        </div>
      ) : (
        <>
          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[180px]">시간</TableHead>
                  <TableHead>모델</TableHead>
                  <TableHead>변경 내용</TableHead>
                  <TableHead className="w-[120px]">변경자</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {history.map((h) => (
                  <TableRow key={h.id}>
                    <TableCell className="text-sm">
                      <div className="flex flex-col">
                        <span className="text-xs text-muted-foreground">
                          {formatRelativeTime(h.changed_at)}
                        </span>
                        <span className="text-[11px] text-muted-foreground/60 font-mono">
                          {formatDateTime(h.changed_at)}
                        </span>
                      </div>
                    </TableCell>
                    <TableCell>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          const m = models?.find((x) => x.model_name === h.model_name);
                          if (m) setDetailModel(m);
                        }}
                        className="font-mono text-sm hover:underline cursor-pointer text-left"
                      >
                        {h.model_name}
                      </button>
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1.5">
                        {h.previous_status ? (
                          <>
                            <StatusBadge status={h.previous_status} />
                            <ArrowRight className="size-3 text-muted-foreground shrink-0" />
                            <StatusBadge status={h.new_status} />
                          </>
                        ) : (
                          <>
                            <span className="text-xs text-muted-foreground">
                              생성
                            </span>
                            <ArrowRight className="size-3 text-muted-foreground shrink-0" />
                            <StatusBadge status={h.new_status} />
                          </>
                        )}
                      </div>
                    </TableCell>
                    <TableCell className="text-sm font-mono">
                      {h.changed_by}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>

          {/* Pagination */}
          <div className="flex items-center justify-between">
            <p className="text-sm text-muted-foreground">
              총 {total}개 중 {startItem}–{endItem}
            </p>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={page === 0}
                onClick={() => setPage((p) => p - 1)}
              >
                <ChevronLeft className="size-4" />
                이전
              </Button>
              <span className="text-sm text-muted-foreground">
                {page + 1} / {totalPages || 1}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={page >= totalPages - 1}
                onClick={() => setPage((p) => p + 1)}
              >
                다음
                <ChevronRight className="size-4" />
              </Button>
            </div>
          </div>
        </>
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
