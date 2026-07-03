"use client";

import { Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useLocaleTag } from "@/lib/locale";
import { useTeamMemberUsageByModel } from "@/hooks/use-api";
import { InputTokens } from "@/components/input-tokens";

/** Per-model-group usage breakdown for one member within a team. Rendered
 *  inside an expanded usage row on both the team usage tab and the admin
 *  usage page. Admins can drill into any member; regular members only into
 *  their own row (enforced by the by-model endpoint). */
export function MemberModelUsage({
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
  const t = useTranslations("teamDetail");
  const localeTag = useLocaleTag();
  const { data, isLoading } = useTeamMemberUsageByModel(teamId, userId, startDate, endDate, "model_group");

  return (
    <div className="px-6 py-2 space-y-2">
      {isLoading ? (
        <div className="flex items-center justify-center py-4">
          <Loader2 className="size-4 animate-spin text-muted-foreground" />
        </div>
      ) : !data || data.models.length === 0 ? (
        <div className="py-3 text-xs text-muted-foreground">{t("usageByModelEmpty")}</div>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-muted-foreground">
              <th className="text-left font-normal py-1">{t("colModelGroup")}</th>
              <th className="text-right font-normal py-1">{t("colRequests")}</th>
              <th className="text-right font-normal py-1">{t("colInput")}</th>
              <th className="text-right font-normal py-1">{t("colOutput")}</th>
              <th className="text-right font-normal py-1">{t("colUsage")}</th>
            </tr>
          </thead>
          <tbody>
            {data.models.map((mm) => (
              <tr key={mm.model} className="border-t border-border/50">
                <td className="py-1 font-mono text-xs">{mm.model}</td>
                <td className="py-1 text-right tabular-nums">{mm.api_requests.toLocaleString(localeTag)}</td>
                <td className="py-1 text-right">
                  <InputTokens input={mm.input_tokens} cacheRead={mm.cache_read_tokens} />
                </td>
                <td className="py-1 text-right tabular-nums">{mm.output_tokens.toLocaleString(localeTag)}</td>
                <td className="py-1 text-right tabular-nums">${mm.spend.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
