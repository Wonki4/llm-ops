"use client";

import Link from "next/link";
import { useMyTeams, useMe } from "@/hooks/use-api";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Users,
  Boxes,
  AlertCircle,
  RefreshCw,
  ArrowRight,
} from "lucide-react";

function formatBudget(spend: number, maxBudget: number | null): string {
  const spendStr = `$${spend.toFixed(2)}`;
  if (maxBudget === null) return `${spendStr} / 무제한`;
  return `${spendStr} / $${maxBudget.toFixed(2)}`;
}

function budgetPercent(spend: number, maxBudget: number | null): number {
  if (maxBudget === null || maxBudget === 0) return 0;
  return Math.min((spend / maxBudget) * 100, 100);
}

function formatResetDate(dateStr: string): string {
  const date = new Date(dateStr);
  return date.toLocaleDateString("ko-KR", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function SkeletonCards() {
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
      {Array.from({ length: 6 }).map((_, i) => (
        <div
          key={i}
          className="flex flex-col gap-4 rounded-xl border bg-card p-6 shadow-sm"
        >
          <div className="h-5 w-2/3 animate-pulse rounded bg-muted" />
          <div className="h-3 w-1/2 animate-pulse rounded bg-muted" />
          <div className="h-2 w-full animate-pulse rounded-full bg-muted" />
          <div className="flex gap-2">
            <div className="h-5 w-16 animate-pulse rounded-full bg-muted" />
            <div className="h-5 w-12 animate-pulse rounded-full bg-muted" />
          </div>
        </div>
      ))}
    </div>
  );
}

export default function MyTeamsPage() {
  const { data: teams, isLoading, isError, error, refetch } = useMyTeams();
  useMe();

  if (isLoading) {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-bold">내 팀</h1>
          <p className="text-muted-foreground mt-1">
            소속된 팀 목록을 확인하세요
          </p>
        </div>
        <SkeletonCards />
      </div>
    );
  }

  if (isError) {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-bold">내 팀</h1>
          <p className="text-muted-foreground mt-1">
            소속된 팀 목록을 확인하세요
          </p>
        </div>
        <div className="flex flex-col items-center gap-4 rounded-xl border border-destructive/20 bg-destructive/5 p-8">
          <AlertCircle className="size-10 text-destructive" />
          <p className="text-sm text-destructive">
            팀 목록을 불러오는 중 오류가 발생했습니다:{" "}
            {error?.message ?? "알 수 없는 오류"}
          </p>
          <Button variant="outline" size="sm" onClick={() => refetch()}>
            <RefreshCw className="size-4" />
            다시 시도
          </Button>
        </div>
      </div>
    );
  }

  const isEmpty = !teams || teams.length === 0;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">내 팀</h1>
        <p className="text-muted-foreground mt-1">
          소속된 팀 목록을 확인하세요
        </p>
      </div>

      {isEmpty ? (
        <div className="flex flex-col items-center gap-4 rounded-xl border border-dashed p-12">
          <Users className="size-10 text-muted-foreground" />
          <div className="text-center">
            <p className="font-medium">소속된 팀이 없습니다.</p>
            <p className="text-sm text-muted-foreground mt-1">
              팀 탐색에서 팀에 가입해보세요.
            </p>
          </div>
          <Button asChild variant="outline">
            <Link href="/teams/discover">
              팀 탐색하기
              <ArrowRight className="size-4" />
            </Link>
          </Button>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          {teams.map((team) => {
            const pct = budgetPercent(team.spend, team.max_budget);
            return (
              <Link key={team.team_id} href={`/teams/${team.team_id}`}>
                <Card className="h-full cursor-pointer transition-shadow hover:shadow-md">
                  <CardHeader>
                    <CardTitle className="text-base">
                      {team.team_alias}
                    </CardTitle>
                    <CardDescription className="truncate text-xs font-mono">
                      {team.team_id}
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    {/* Budget bar */}
                    <div className="space-y-1">
                      <div className="flex items-center justify-between text-xs text-muted-foreground">
                        <span>예산</span>
                        <span>{formatBudget(team.spend, team.max_budget)}</span>
                      </div>
                      <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                        <div
                          className="h-full rounded-full bg-primary transition-all"
                          style={{
                            width:
                              team.max_budget === null
                                ? "0%"
                                : `${pct}%`,
                          }}
                        />
                      </div>
                    </div>

                    {/* Budget reset */}
                    {team.budget_reset_at && (
                      <p className="text-xs text-muted-foreground">
                        예산 초기화: {formatResetDate(team.budget_reset_at)}
                      </p>
                    )}

                    {/* Badges */}
                    <div className="flex items-center gap-2">
                      <Badge variant="secondary" className="gap-1">
                        <Boxes className="size-3" />
                        {team.models.length}개 모델
                      </Badge>
                      <Badge variant="outline" className="gap-1">
                        <Users className="size-3" />
                        {(team.member_count ?? team.members.length).toLocaleString()}명
                      </Badge>
                    </div>
                  </CardContent>
                </Card>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
