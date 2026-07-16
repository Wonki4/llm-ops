"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";

import { useTeamBudgetBoosts } from "@/hooks/use-api";
import type { MemberBudgetBoost } from "@/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const PAGE_SIZE = 50;

// An active boost whose end time has passed is awaiting the worker (~5 min).
function effectiveStatus(b: MemberBudgetBoost): MemberBudgetBoost["status"] | "pending" {
  if (b.status === "active" && b.expires_at && new Date(b.expires_at) <= new Date()) {
    return "pending";
  }
  return b.status;
}

export function TeamBoostHistory({ teamId }: { teamId: string }) {
  const t = useTranslations("teamDetail");
  const [status, setStatus] = useState<"active" | "all">("active");
  const [page, setPage] = useState(1);
  const { data } = useTeamBudgetBoosts(teamId, status, page, PAGE_SIZE);

  const boosts = data?.boosts;
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);

  const statusLabel: Record<string, string> = {
    active: t("boostStatusActive"),
    reverted: t("boostStatusReverted"),
    cancelled: t("boostStatusCancelled"),
    pending: t("boostStatusPending"),
  };

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <CardTitle className="text-base">{t("boostHistoryTitle")}</CardTitle>
        <Tabs
          value={status}
          onValueChange={(v) => {
            setStatus(v as "active" | "all");
            setPage(1);
          }}
        >
          <TabsList>
            <TabsTrigger value="active">{t("boostStatusActive")}</TabsTrigger>
            <TabsTrigger value="all">{t("boostFilterAll")}</TabsTrigger>
          </TabsList>
        </Tabs>
      </CardHeader>
      <CardContent>
        {!boosts || boosts.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("boostHistoryEmpty")}</p>
        ) : (
          <>
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
            {totalPages > 1 && (
              <div className="mt-3 flex items-center justify-between">
                <p className="text-xs text-muted-foreground">
                  {t("pagination", {
                    from: ((safePage - 1) * PAGE_SIZE + 1).toLocaleString(),
                    to: Math.min(safePage * PAGE_SIZE, total).toLocaleString(),
                    total: total.toLocaleString(),
                  })}
                </p>
                <div className="flex items-center gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={safePage <= 1}
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                  >
                    {t("pagePrev")}
                  </Button>
                  <span className="text-xs text-muted-foreground">
                    {safePage} / {totalPages}
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={safePage >= totalPages}
                    onClick={() => setPage((p) => p + 1)}
                  >
                    {t("pageNext")}
                  </Button>
                </div>
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
