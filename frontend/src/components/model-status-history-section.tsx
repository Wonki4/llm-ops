"use client";

import { useId, useState } from "react";
import { useTranslations } from "next-intl";

import { ModelStatusBadge } from "@/components/model-status-badge";
import { Button } from "@/components/ui/button";
import { useModelStatusHistory } from "@/hooks/use-api";
import { parseServerDate, useLocaleTag } from "@/lib/locale";

const RECENT_CHANGES = 5;

export function ModelStatusHistorySection({
  catalogId,
  expandable = false,
}: {
  catalogId?: string;
  expandable?: boolean;
}) {
  const t = useTranslations("modelDetail");
  const common = useTranslations("common");
  const locale = useLocaleTag();
  const id = useId();
  const [expanded, setExpanded] = useState(false);
  const { data: history, isLoading, isError, refetch } = useModelStatusHistory(catalogId);
  const total = history?.length ?? 0;
  const canExpand = expandable && total > RECENT_CHANGES;
  const entries = canExpand && expanded ? history : history?.slice(0, RECENT_CHANGES);

  return (
    <section aria-labelledby={id} className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id={id} className="text-sm font-semibold">{t("sections.statusHistory")}</h2>
        {canExpand && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            aria-expanded={expanded}
            aria-controls={`${id}-entries`}
            onClick={() => setExpanded(value => !value)}
          >
            {expanded ? t("history.collapse") : t("history.expand", { count: total })}
          </Button>
        )}
      </div>
      {!catalogId && <p className="text-sm text-muted-foreground">{t("history.unregistered")}</p>}
      {isLoading && <p role="status" className="text-sm text-muted-foreground">{common("loading")}</p>}
      {isError && (
        <div role="alert" className="flex items-center gap-2 text-sm">
          {t("history.loadError")}
          <Button variant="outline" size="sm" onClick={() => refetch()}>{common("retry")}</Button>
        </div>
      )}
      {!isError && history?.length === 0 && <p className="text-sm text-muted-foreground">{t("history.empty")}</p>}
      {!!entries?.length && (
        <ol id={`${id}-entries`} className="divide-y rounded-lg border bg-background">
          {entries.map(entry => (
            <li key={entry.id} className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 px-3 py-2">
              <div className="flex flex-wrap items-center gap-1.5 text-xs">
                {entry.previous_status
                  ? <ModelStatusBadge status={entry.previous_status} />
                  : <span className="text-muted-foreground">{t("history.created")}</span>}
                <span>→</span>
                <ModelStatusBadge status={entry.new_status} />
              </div>
              <div className="flex flex-wrap gap-x-3 text-xs text-muted-foreground">
                <span>{entry.changed_by}</span>
                {entry.changed_at && (
                  <time dateTime={entry.changed_at}>
                    {parseServerDate(entry.changed_at).toLocaleString(locale, { dateStyle: "medium", timeStyle: "short" })}
                  </time>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
