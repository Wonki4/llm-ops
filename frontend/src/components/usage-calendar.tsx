"use client";

import { useMemo, useState } from "react";
import { ChevronLeft, ChevronRight, Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useLocaleTag } from "@/lib/locale";
import { useAdminUsageDaily } from "@/hooks/use-api";
import { toDateInput } from "@/lib/usage";
import { Button } from "@/components/ui/button";

/** Monthly spend heatmap for the admin usage page. Scoped by `teamId` (empty =
 *  all teams). Clicking a day with usage calls `onPickDay` for a drilldown. */
export function UsageCalendar({
  teamId,
  onPickDay,
}: {
  teamId: string;
  onPickDay?: (date: string) => void;
}) {
  const t = useTranslations("adminUsage");
  const localeTag = useLocaleTag();
  const now = new Date();
  const [year, setYear] = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth()); // 0-11

  const firstOfMonth = new Date(year, month, 1);
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const startDate = toDateInput(firstOfMonth);
  const endDate = toDateInput(new Date(year, month, daysInMonth));

  const { data, isLoading } = useAdminUsageDaily(startDate, endDate, teamId);

  const byDate = useMemo(() => {
    const m = new Map<string, { spend: number; api_requests: number; total_tokens: number }>();
    for (const d of data?.days ?? []) m.set(d.date, d);
    return m;
  }, [data]);

  const maxSpend = useMemo(
    () => Math.max(0, ...(data?.days ?? []).map((d) => d.spend)),
    [data],
  );

  const goPrev = () => {
    if (month === 0) { setYear((y) => y - 1); setMonth(11); }
    else setMonth((mo) => mo - 1);
  };
  const goNext = () => {
    if (month === 11) { setYear((y) => y + 1); setMonth(0); }
    else setMonth((mo) => mo + 1);
  };
  const goToday = () => { setYear(now.getFullYear()); setMonth(now.getMonth()); };

  const monthLabel = firstOfMonth.toLocaleDateString(localeTag, { year: "numeric", month: "long" });
  const weekdays = useMemo(() => {
    // Sun-first weekday short labels in the active locale.
    const base = new Date(2024, 0, 7); // a Sunday
    return Array.from({ length: 7 }, (_, i) =>
      new Date(base.getFullYear(), base.getMonth(), base.getDate() + i).toLocaleDateString(localeTag, { weekday: "short" }),
    );
  }, [localeTag]);

  const leadingBlanks = firstOfMonth.getDay(); // 0=Sun
  const todayStr = toDateInput(now);

  return (
    <div className="space-y-4">
      {/* Month nav + month totals */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={goPrev}><ChevronLeft className="size-4" /></Button>
          <div className="min-w-[140px] text-center text-sm font-semibold">{monthLabel}</div>
          <Button variant="outline" size="sm" onClick={goNext}><ChevronRight className="size-4" /></Button>
          <Button variant="ghost" size="sm" onClick={goToday}>{t("calendarToday")}</Button>
        </div>
        {data && (
          <div className="text-sm text-muted-foreground tabular-nums">
            {t("calendarMonthTotal")}: <span className="font-semibold text-foreground">${data.totals.spend.toFixed(2)}</span>
            {" · "}{data.totals.api_requests.toLocaleString(localeTag)} {t("colRequests")}
          </div>
        )}
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="size-6 animate-spin text-muted-foreground" />
        </div>
      ) : (
        <div className="rounded-lg border p-2">
          <div className="grid grid-cols-7 gap-1">
            {weekdays.map((w) => (
              <div key={w} className="py-1 text-center text-xs font-medium text-muted-foreground">{w}</div>
            ))}
            {Array.from({ length: leadingBlanks }, (_, i) => (
              <div key={`blank-${i}`} />
            ))}
            {Array.from({ length: daysInMonth }, (_, i) => {
              const day = i + 1;
              const dateStr = toDateInput(new Date(year, month, day));
              const entry = byDate.get(dateStr);
              const spend = entry?.spend ?? 0;
              const intensity = maxSpend > 0 && spend > 0 ? Math.max(0.12, spend / maxSpend) : 0;
              const isToday = dateStr === todayStr;
              const clickable = !!entry && !!onPickDay;
              return (
                <button
                  key={dateStr}
                  type="button"
                  disabled={!clickable}
                  onClick={() => clickable && onPickDay!(dateStr)}
                  title={entry ? `$${spend.toFixed(4)} · ${entry.api_requests.toLocaleString(localeTag)} ${t("colRequests")} · ${entry.total_tokens.toLocaleString(localeTag)} ${t("colTokens")}` : undefined}
                  className={`flex h-20 flex-col rounded-md border p-1.5 text-left transition-colors ${
                    clickable ? "cursor-pointer hover:border-primary" : "cursor-default"
                  } ${isToday ? "border-primary" : "border-border"}`}
                  style={intensity > 0 ? { backgroundColor: `color-mix(in srgb, var(--primary) ${Math.round(intensity * 100)}%, transparent)` } : undefined}
                >
                  <span className={`text-xs ${isToday ? "font-bold text-primary" : "text-muted-foreground"}`}>{day}</span>
                  {spend > 0 && (
                    <span className="mt-auto text-right text-xs font-semibold tabular-nums">${spend.toFixed(2)}</span>
                  )}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
