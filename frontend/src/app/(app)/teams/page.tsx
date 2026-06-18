"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import { useMyTeams, useMe } from "@/hooks/use-api";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
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
  const t = useTranslations("teams");
  const tc = useTranslations("common");

  if (isLoading) {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-bold">{t("title")}</h1>
          <p className="text-muted-foreground mt-1">{t("subtitle")}</p>
        </div>
        <SkeletonCards />
      </div>
    );
  }

  if (isError) {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-bold">{t("title")}</h1>
          <p className="text-muted-foreground mt-1">{t("subtitle")}</p>
        </div>
        <div className="flex flex-col items-center gap-4 rounded-xl border border-destructive/20 bg-destructive/5 p-8">
          <AlertCircle className="size-10 text-destructive" />
          <p className="text-sm text-destructive">
            {t("loadError")}: {error?.message ?? tc("unknownError")}
          </p>
          <Button variant="outline" size="sm" onClick={() => refetch()}>
            <RefreshCw className="size-4" />
            {tc("retry")}
          </Button>
        </div>
      </div>
    );
  }

  const isEmpty = !teams || teams.length === 0;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">{t("title")}</h1>
        <p className="text-muted-foreground mt-1">{t("subtitle")}</p>
      </div>

      {isEmpty ? (
        <div className="flex flex-col items-center gap-4 rounded-xl border border-dashed p-12">
          <Users className="size-10 text-muted-foreground" />
          <div className="text-center">
            <p className="font-medium">{t("empty")}</p>
            <p className="text-sm text-muted-foreground mt-1">
              {t("emptyHint")}
            </p>
          </div>
          <Button asChild variant="outline">
            <Link href="/teams/discover">
              {t("discover")}
              <ArrowRight className="size-4" />
            </Link>
          </Button>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          {teams.map((team) => {
            return (
              <Link key={team.team_id} href={`/teams/${team.team_id}`}>
                <Card className="h-full cursor-pointer transition-shadow hover:shadow-md">
                  <CardHeader>
                    <CardTitle className="text-base">
                      {team.team_alias}
                    </CardTitle>
                    {team.description && (
                      <p className="line-clamp-2 text-sm text-muted-foreground">{team.description}</p>
                    )}
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <div className="flex items-center gap-2">
                      <Badge variant="secondary" className="gap-1">
                        <Boxes className="size-3" />
                        {team.models.includes("all-proxy-models")
                          ? t("allModels")
                          : t("modelCount", { count: team.models.length })}
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
