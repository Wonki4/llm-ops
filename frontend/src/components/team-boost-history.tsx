"use client";

import { useTranslations } from "next-intl";

import { useTeamBudgetBoosts } from "@/hooks/use-api";
import type { MemberBudgetBoost } from "@/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

// An active boost whose end time has passed is awaiting the worker (~5 min).
function effectiveStatus(b: MemberBudgetBoost): MemberBudgetBoost["status"] | "pending" {
  if (b.status === "active" && b.expires_at && new Date(b.expires_at) <= new Date()) {
    return "pending";
  }
  return b.status;
}

export function TeamBoostHistory({ teamId }: { teamId: string }) {
  const t = useTranslations("teamDetail");
  const { data: boosts } = useTeamBudgetBoosts(teamId);

  const statusLabel: Record<string, string> = {
    active: t("boostStatusActive"),
    reverted: t("boostStatusReverted"),
    cancelled: t("boostStatusCancelled"),
    pending: t("boostStatusPending"),
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{t("boostHistoryTitle")}</CardTitle>
      </CardHeader>
      <CardContent>
        {!boosts || boosts.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("boostHistoryEmpty")}</p>
        ) : (
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("boostColMember")}</TableHead>
                  <TableHead>{t("boostColChange")}</TableHead>
                  <TableHead className="hidden sm:table-cell">{t("boostColExpires")}</TableHead>
                  <TableHead>{t("boostColStatus")}</TableHead>
                  <TableHead className="hidden md:table-cell">{t("boostColBy")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {boosts.map((b) => {
                  const st = effectiveStatus(b);
                  return (
                    <TableRow key={b.id}>
                      <TableCell className="font-medium">{b.user_id}</TableCell>
                      <TableCell className="font-mono text-xs">
                        ${b.original_max_budget} → ${b.boost_max_budget}
                      </TableCell>
                      <TableCell className="hidden sm:table-cell text-xs">
                        {b.expires_at ? new Date(b.expires_at).toLocaleString() : "-"}
                      </TableCell>
                      <TableCell>
                        <Badge variant={st === "active" ? "default" : "secondary"}>
                          {statusLabel[st]}
                        </Badge>
                      </TableCell>
                      <TableCell className="hidden md:table-cell text-xs text-muted-foreground">
                        {b.created_by ?? "-"}
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
  );
}
