"use client";

import { useMemo, useState } from "react";
import { Calendar, ChevronLeft, ChevronRight, Loader2, Boxes } from "lucide-react";
import { useTranslations, useLocale } from "next-intl";

import { useModelCatalog } from "@/hooks/use-api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { ModelStatus, ModelCatalog } from "@/types";

// ─── Constants ────────────────────────────────────────────────

const STATUS_OPTIONS: { value: ModelStatus; label: string }[] = [
  { value: "testing", label: "Testing" },
  { value: "prerelease", label: "Prerelease" },
  { value: "lts", label: "LTS" },
  { value: "deprecating", label: "Deprecating" },
  { value: "deprecated", label: "Deprecated" },
];

const STATUS_STYLES: Record<ModelStatus, string> = {
  testing: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
  prerelease: "bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400",
  lts: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400",
  deprecating: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400",
  deprecated: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",
};

const STATUS_DOT_COLORS: Record<ModelStatus, string> = {
  testing: "bg-blue-400",
  prerelease: "bg-purple-400",
  lts: "bg-green-500",
  deprecating: "bg-yellow-400",
  deprecated: "bg-red-400",
};

function toLocaleTag(locale: string): string {
  return locale === "ko" ? "ko-KR" : "en-US";
}

function getMonthNames(localeTag: string): string[] {
  return Array.from({ length: 12 }, (_, i) =>
    new Date(2024, i, 1).toLocaleString(localeTag, { month: "long" }),
  );
}

function getWeekdayNames(localeTag: string): string[] {
  // 2024-01-07 is a Sunday
  return Array.from({ length: 7 }, (_, i) =>
    new Date(2024, 0, 7 + i).toLocaleString(localeTag, { weekday: "short" }),
  );
}

// ─── Types ────────────────────────────────────────────────────

interface ScheduleEvent {
  date: string; // YYYY-MM-DD
  modelName: string;
  displayName: string;
  currentStatus: ModelStatus;
  targetStatus: ModelStatus;
  isPast: boolean;
}

// ─── Helpers ──────────────────────────────────────────────────

function StatusBadge({ status }: { status: ModelStatus }) {
  return <Badge className={STATUS_STYLES[status]}>{status}</Badge>;
}

function extractEvents(catalog: ModelCatalog[]): ScheduleEvent[] {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const events: ScheduleEvent[] = [];

  for (const model of catalog) {
    if (!model.status_schedule || model.visible === false) continue;

    for (const { value: status } of STATUS_OPTIONS) {
      const dateStr = model.status_schedule[status];
      if (!dateStr) continue;

      const date = new Date(`${dateStr}T00:00:00`);
      if (Number.isNaN(date.getTime())) continue;

      events.push({
        date: dateStr,
        modelName: model.model_name,
        displayName: model.display_name || model.model_name,
        currentStatus: model.status,
        targetStatus: status,
        isPast: date < today,
      });
    }
  }

  events.sort((a, b) => a.date.localeCompare(b.date));
  return events;
}

function getDaysInMonth(year: number, month: number): number {
  return new Date(year, month + 1, 0).getDate();
}

function formatShortDate(dateStr: string, localeTag: string): string {
  const d = new Date(`${dateStr}T00:00:00`);
  return d.toLocaleDateString(localeTag, { month: "short", day: "numeric" });
}

// ─── Calendar Grid ───────────────────────────────────────────

function CalendarGrid({
  year,
  month,
  events,
  weekdays,
  moreLabel,
}: {
  year: number;
  month: number;
  events: ScheduleEvent[];
  weekdays: string[];
  moreLabel: (extra: number) => string;
}) {
  const daysInMonth = getDaysInMonth(year, month);
  const firstDayOfWeek = new Date(year, month, 1).getDay();
  const today = new Date();
  today.setHours(0, 0, 0, 0);

  const eventsByDay = new Map<number, ScheduleEvent[]>();
  for (const ev of events) {
    const d = new Date(`${ev.date}T00:00:00`);
    if (d.getFullYear() === year && d.getMonth() === month) {
      const day = d.getDate();
      const list = eventsByDay.get(day) || [];
      list.push(ev);
      eventsByDay.set(day, list);
    }
  }

  const cells: (number | null)[] = [];
  for (let i = 0; i < firstDayOfWeek; i++) cells.push(null);
  for (let d = 1; d <= daysInMonth; d++) cells.push(d);
  while (cells.length % 7 !== 0) cells.push(null);

  const isToday = (day: number) => {
    return today.getFullYear() === year && today.getMonth() === month && today.getDate() === day;
  };

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-7 gap-px text-center text-xs font-medium text-muted-foreground">
        {weekdays.map((d, i) => (
          <div key={i} className="py-1">{d}</div>
        ))}
      </div>
      <div className="grid grid-cols-7 gap-px">
        {cells.map((day, i) => {
          if (day === null) return <div key={i} className="min-h-[72px]" />;
          const dayEvents = eventsByDay.get(day) || [];
          return (
            <div
              key={i}
              className={`min-h-[72px] rounded-md border p-1 text-xs ${
                isToday(day) ? "border-primary bg-primary/5" : "border-transparent hover:bg-muted/50"
              }`}
            >
              <div className={`font-medium mb-0.5 ${isToday(day) ? "text-primary" : "text-foreground"}`}>
                {day}
              </div>
              <div className="space-y-0.5">
                {dayEvents.slice(0, 3).map((ev, j) => (
                  <div
                    key={j}
                    className={`truncate rounded px-1 py-0.5 text-[10px] leading-tight ${STATUS_STYLES[ev.targetStatus]}`}
                    title={`${ev.displayName} → ${ev.targetStatus}`}
                  >
                    {ev.displayName}
                  </div>
                ))}
                {dayEvents.length > 3 && (
                  <div className="text-[10px] text-muted-foreground px-1">
                    {moreLabel(dayEvents.length - 3)}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── Main Component ──────────────────────────────────────────

export default function ModelCalendarPage() {
  const t = useTranslations("modelsCalendar");
  const locale = useLocale();
  const localeTag = toLocaleTag(locale);
  const monthNames = useMemo(() => getMonthNames(localeTag), [localeTag]);
  const weekdays = useMemo(() => getWeekdayNames(localeTag), [localeTag]);
  const { data: catalog, isLoading } = useModelCatalog();
  const [statusFilter, setStatusFilter] = useState<string>("");

  const now = new Date();
  const [viewYear, setViewYear] = useState(now.getFullYear());
  const [viewMonth, setViewMonth] = useState(now.getMonth());

  const allEvents = useMemo(() => extractEvents(catalog ?? []), [catalog]);

  const filteredEvents = useMemo(() => {
    if (!statusFilter) return allEvents;
    return allEvents.filter((ev) => ev.targetStatus === statusFilter);
  }, [allEvents, statusFilter]);

  // Stats
  const upcomingCount = allEvents.filter((ev) => !ev.isPast).length;
  const thisMonthEvents = allEvents.filter((ev) => {
    const d = new Date(`${ev.date}T00:00:00`);
    return d.getFullYear() === viewYear && d.getMonth() === viewMonth;
  });
  const deprecatingCount = allEvents.filter(
    (ev) => !ev.isPast && (ev.targetStatus === "deprecating" || ev.targetStatus === "deprecated"),
  ).length;

  // Upcoming events list (next 20)
  const upcomingEvents = filteredEvents.filter((ev) => !ev.isPast).slice(0, 20);

  function prevMonth() {
    if (viewMonth === 0) {
      setViewMonth(11);
      setViewYear(viewYear - 1);
    } else {
      setViewMonth(viewMonth - 1);
    }
  }

  function nextMonth() {
    if (viewMonth === 11) {
      setViewMonth(0);
      setViewYear(viewYear + 1);
    } else {
      setViewMonth(viewMonth + 1);
    }
  }

  function goToday() {
    setViewYear(now.getFullYear());
    setViewMonth(now.getMonth());
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">{t("title")}</h1>
        <p className="text-muted-foreground mt-1">
          {t("subtitle")}
        </p>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">{t("statUpcoming")}</CardTitle>
            <Calendar className="size-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{t("eventCount", { count: upcomingCount })}</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">{t("statThisMonth")}</CardTitle>
            <Calendar className="size-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{t("eventCount", { count: thisMonthEvents.length })}</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">{t("statDeprecating")}</CardTitle>
            <Calendar className="size-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-red-600">{t("eventCount", { count: deprecatingCount })}</div>
          </CardContent>
        </Card>
      </div>

      {/* Calendar */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <Button variant="outline" size="icon" onClick={prevMonth}>
                <ChevronLeft className="size-4" />
              </Button>
              <CardTitle className="text-base min-w-[120px] text-center">
                {t("ymCaption", { year: viewYear, month: monthNames[viewMonth] })}
              </CardTitle>
              <Button variant="outline" size="icon" onClick={nextMonth}>
                <ChevronRight className="size-4" />
              </Button>
              <Button variant="ghost" size="sm" onClick={goToday}>
                {t("today")}
              </Button>
            </div>
            <div className="flex items-center gap-2">
              <Select
                value={statusFilter}
                onValueChange={(v) => setStatusFilter(v === "__all__" ? "" : v)}
              >
                <SelectTrigger className="w-[140px] h-8">
                  <SelectValue placeholder={t("filterAll")} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__all__">{t("filterAll")}</SelectItem>
                  {STATUS_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          {/* Legend */}
          <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2">
            {STATUS_OPTIONS.map((opt) => (
              <div key={opt.value} className="flex items-center gap-1.5">
                <div className={`size-2.5 rounded-full ${STATUS_DOT_COLORS[opt.value]}`} />
                <span className="text-xs text-muted-foreground">{opt.label}</span>
              </div>
            ))}
          </div>
        </CardHeader>
        <CardContent>
          <CalendarGrid
            year={viewYear}
            month={viewMonth}
            events={filteredEvents}
            weekdays={weekdays}
            moreLabel={(extra) => t("more", { count: extra })}
          />
        </CardContent>
      </Card>

      {/* Upcoming transitions list */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("upcomingTitle")}</CardTitle>
          <CardDescription>{t("upcomingDescription")}</CardDescription>
        </CardHeader>
        <CardContent>
          {upcomingEvents.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-8 text-center">
              <Boxes className="size-8 text-muted-foreground/40 mb-2" />
              <p className="text-sm text-muted-foreground">{t("emptyUpcoming")}</p>
            </div>
          ) : (
            <div className="rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("colDate")}</TableHead>
                    <TableHead>{t("colModel")}</TableHead>
                    <TableHead>{t("colCurrentStatus")}</TableHead>
                    <TableHead>{t("colTargetStatus")}</TableHead>
                    <TableHead>{t("colDday")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {upcomingEvents.map((ev, i) => {
                    const daysUntil = Math.ceil(
                      (new Date(`${ev.date}T00:00:00`).getTime() - Date.now()) / 86400000,
                    );
                    return (
                      <TableRow key={`${ev.modelName}-${ev.targetStatus}-${i}`}>
                        <TableCell className="font-mono text-sm">
                          {formatShortDate(ev.date, localeTag)}
                        </TableCell>
                        <TableCell>
                          <div>
                            <span className="font-medium text-sm">{ev.displayName}</span>
                            {ev.displayName !== ev.modelName && (
                              <p className="font-mono text-[11px] text-muted-foreground">{ev.modelName}</p>
                            )}
                          </div>
                        </TableCell>
                        <TableCell>
                          <StatusBadge status={ev.currentStatus} />
                        </TableCell>
                        <TableCell>
                          <StatusBadge status={ev.targetStatus} />
                        </TableCell>
                        <TableCell>
                          <span
                            className={`text-sm font-medium ${
                              daysUntil <= 7
                                ? "text-red-600"
                                : daysUntil <= 30
                                  ? "text-yellow-600"
                                  : "text-muted-foreground"
                            }`}
                          >
                            {daysUntil === 0
                              ? t("today")
                              : daysUntil < 0
                                ? t("dayPast", { days: -daysUntil })
                                : t("dDay", { days: daysUntil })}
                          </span>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
