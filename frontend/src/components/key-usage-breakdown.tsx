"use client";

import { Fragment, useState } from "react";
import { ChevronRight, Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useLocaleTag } from "@/lib/locale";
import { useAdminUserUsageByKey, useTeamMemberUsageByKey } from "@/hooks/use-api";
import { InputTokens } from "@/components/input-tokens";
import { Badge } from "@/components/ui/badge";
import type { UsageKeyRow } from "@/types";

/** A team member's usage within the team, per key then per model group.
 *  Rendered inside an expanded member row on the team usage tab. Admins can
 *  drill into any member; regular members only into their own row (enforced
 *  by the by-key endpoint). */
export function TeamMemberKeyUsage({
  teamId,
  userId,
  startDate,
  endDate,
}: {
  teamId: string;
  userId: string;
  startDate: string;
  endDate: string;
}) {
  const { data, isLoading } = useTeamMemberUsageByKey(teamId, userId, startDate, endDate);
  return <KeyUsageTable keys={data?.keys} isLoading={isLoading} showTeam={false} />;
}

/** A user's usage across teams (or within `teamId`), per key then per model
 *  group. Rendered inside an expanded user row on the admin usage page. */
export function AdminUserKeyUsage({
  userId,
  startDate,
  endDate,
  teamId,
}: {
  userId: string | null;
  startDate: string;
  endDate: string;
  teamId: string;
}) {
  const { data, isLoading } = useAdminUserUsageByKey(userId, startDate, endDate, teamId);
  return <KeyUsageTable keys={data?.keys} isLoading={isLoading} showTeam />;
}

function KeyUsageTable({
  keys,
  isLoading,
  showTeam,
}: {
  keys: UsageKeyRow[] | undefined;
  isLoading: boolean;
  showTeam: boolean;
}) {
  const t = useTranslations("teamDetail");
  const localeTag = useLocaleTag();
  // Users can hold many keys (20+), so several may be open at once.
  const [open, setOpen] = useState<Set<string>>(new Set());

  const toggle = (apiKey: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(apiKey)) next.delete(apiKey);
      else next.add(apiKey);
      return next;
    });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-4">
        <Loader2 className="size-4 animate-spin text-muted-foreground" />
      </div>
    );
  }
  if (!keys || keys.length === 0) {
    return <div className="px-6 py-3 text-xs text-muted-foreground">{t("usageByKeyEmpty")}</div>;
  }

  return (
    <div className="px-6 py-2">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-xs text-muted-foreground">
            <th className="text-left font-normal py-1">{t("colKey")}</th>
            <th className="text-right font-normal py-1">{t("colRequests")}</th>
            <th className="text-right font-normal py-1">{t("colInput")}</th>
            <th className="text-right font-normal py-1">{t("colOutput")}</th>
            <th className="text-right font-normal py-1">{t("colUsage")}</th>
          </tr>
        </thead>
        <tbody>
          {keys.map((k) => {
            const isOpen = open.has(k.api_key);
            return (
              <Fragment key={k.api_key}>
                <tr
                  className="border-t border-border/50 cursor-pointer hover:bg-muted/50"
                  onClick={() => toggle(k.api_key)}
                  data-testid="usage-key-row"
                >
                  <td className="py-1.5">
                    <span className="inline-flex flex-wrap items-center gap-1.5">
                      <ChevronRight className={`size-3.5 shrink-0 transition-transform ${isOpen ? "rotate-90" : ""}`} />
                      {k.state === "unknown" ? (
                        // No alias to show: a hash prefix at least tells several unknown keys apart.
                        <span className="text-muted-foreground">
                          {t("keyUnknown")} <span className="font-mono text-xs">{k.api_key.slice(0, 8)}</span>
                        </span>
                      ) : (
                        <span className={k.state === "deleted" ? "text-muted-foreground" : "font-medium"}>
                          {k.alias}
                        </span>
                      )}
                      {showTeam && k.team_id && (
                        <Badge variant="secondary" className="text-[11px]">{k.team_alias || k.team_id}</Badge>
                      )}
                      {k.state !== "unknown" && k.personal && (
                        <Badge variant="outline" className="text-[11px]">{t("keyBadgePersonal")}</Badge>
                      )}
                      {k.sso && <Badge variant="outline" className="text-[11px]">{t("keyBadgeSso")}</Badge>}
                      {k.state === "deleted" && (
                        <Badge variant="outline" className="text-[11px] text-muted-foreground">
                          {t("keyBadgeDeleted")}
                        </Badge>
                      )}
                    </span>
                  </td>
                  <td className="py-1.5 text-right tabular-nums">{k.api_requests.toLocaleString(localeTag)}</td>
                  <td className="py-1.5 text-right">
                    <InputTokens input={k.input_tokens} cacheRead={k.cache_read_tokens} />
                  </td>
                  <td className="py-1.5 text-right tabular-nums">{k.output_tokens.toLocaleString(localeTag)}</td>
                  <td className="py-1.5 text-right tabular-nums">${k.spend.toFixed(2)}</td>
                </tr>
                {isOpen &&
                  k.models.map((m) => (
                    <tr key={m.model} className="text-muted-foreground" data-testid="usage-model-row">
                      <td className="py-1 pl-8 font-mono text-xs">{m.model}</td>
                      <td className="py-1 text-right tabular-nums">{m.api_requests.toLocaleString(localeTag)}</td>
                      <td className="py-1 text-right">
                        <InputTokens input={m.input_tokens} cacheRead={m.cache_read_tokens} />
                      </td>
                      <td className="py-1 text-right tabular-nums">{m.output_tokens.toLocaleString(localeTag)}</td>
                      <td className="py-1 text-right tabular-nums">${m.spend.toFixed(2)}</td>
                    </tr>
                  ))}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
