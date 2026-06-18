"use client";

import { useState } from "react";
import { Search, Users, Box, Shield } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import { useDiscoverTeams, useCreateJoinRequest } from "@/hooks/use-api";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import type { DiscoverTeam } from "@/types";

function SkeletonCard() {
  return (
    <Card>
      <CardHeader>
        <div className="h-5 w-2/3 animate-pulse rounded bg-muted" />
        <div className="h-3 w-1/2 animate-pulse rounded bg-muted" />
      </CardHeader>
      <CardContent className="space-y-2">
        <div className="h-4 w-3/4 animate-pulse rounded bg-muted" />
        <div className="h-4 w-1/2 animate-pulse rounded bg-muted" />
      </CardContent>
      <CardFooter>
        <div className="h-9 w-24 animate-pulse rounded bg-muted" />
      </CardFooter>
    </Card>
  );
}

export default function TeamDiscoveryPage() {
  const t = useTranslations("teamsDiscover");
  const tc = useTranslations("common");
  const { data: teams, isLoading, isError } = useDiscoverTeams();
  const createJoinRequest = useCreateJoinRequest();

  const [searchQuery, setSearchQuery] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [selectedTeam, setSelectedTeam] = useState<DiscoverTeam | null>(null);
  const [message, setMessage] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | "joined" | "not_joined" | "pending">("all");

  const filteredTeams = teams?.filter((team) => {
    if (statusFilter === "joined" && !team.is_member) return false;
    if (statusFilter === "not_joined" && (team.is_member || team.has_pending_request)) return false;
    if (statusFilter === "pending" && !team.has_pending_request) return false;
    return team.team_alias.toLowerCase().includes(searchQuery.toLowerCase());
  });

  function handleRequestClick(team: DiscoverTeam) {
    setSelectedTeam(team);
    setMessage("");
    setDialogOpen(true);
  }

  function handleSubmit() {
    if (!selectedTeam) return;

    createJoinRequest.mutate(
      {
        team_id: selectedTeam.team_id,
        message: message.trim() || undefined,
      },
      {
        onSuccess: () => {
          toast.success(t("joinRequestSent"));
          setDialogOpen(false);
          setSelectedTeam(null);
        },
        onError: (error) => {
          const msg =
            error instanceof Error ? error.message : t("joinRequestError");
          toast.error(msg);
        },
      },
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold">{t("title")}</h1>
        <p className="text-muted-foreground mt-1">{t("subtitle")}</p>
      </div>

      {/* Search */}
      <div className="flex items-center gap-3 max-w-lg">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder={t("searchPlaceholder")}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="pl-9"
          />
        </div>
        <div className="flex items-center gap-1 shrink-0">
          {([
            ["all", t("filterAll")],
            ["joined", t("filterJoined")],
            ["not_joined", t("filterNotJoined")],
            ["pending", t("filterPending")],
          ] as const).map(([value, label]) => (
            <Button
              key={value}
              variant={statusFilter === value ? "default" : "outline"}
              size="sm"
              onClick={() => setStatusFilter(value)}
            >
              {label}
            </Button>
          ))}
        </div>
      </div>

      {/* Error state */}
      {isError && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          {t("loadError")}
        </div>
      )}

      {/* Loading state */}
      {isLoading && (
        <div className="grid gap-4 sm:grid-cols-1 md:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      )}

      {/* Team cards */}
      {!isLoading && !isError && (
        <>
          {filteredTeams && filteredTeams.length > 0 ? (
            <div className="grid gap-4 sm:grid-cols-1 md:grid-cols-2 lg:grid-cols-3">
              {filteredTeams.map((team) => (
                <Card key={team.team_id}>
                  <CardHeader>
                    <CardTitle>{team.team_alias}</CardTitle>
                    {team.description && (
                      <p className="line-clamp-2 text-sm text-muted-foreground">{team.description}</p>
                    )}
                  </CardHeader>
                  <CardContent className="space-y-2">
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      <Box className="size-4" />
                      <span>
                        {team.models.includes("all-proxy-models")
                          ? t("allModels")
                          : t("modelCount", { count: team.models.length })}
                      </span>
                    </div>
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      <Shield className="size-4" />
                      <span>{t("admins", { names: team.admins.length > 0 ? team.admins.join(", ") : "-" })}</span>
                    </div>
                  </CardContent>
                  <CardFooter>
                    {team.is_member ? (
                      <Badge className="bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400">
                        {t("filterJoined")}
                      </Badge>
                    ) : team.has_pending_request ? (
                      <Badge className="bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400">
                        {t("filterPending")}
                      </Badge>
                    ) : (
                      <Button
                        size="sm"
                        onClick={() => handleRequestClick(team)}
                      >
                        {t("joinRequest")}
                      </Button>
                    )}
                  </CardFooter>
                </Card>
              ))}
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center rounded-lg border border-dashed py-12 text-center">
              <Users className="size-10 text-muted-foreground mb-3" />
              <p className="text-muted-foreground">
                {searchQuery ? t("emptySearch") : t("emptyAll")}
              </p>
            </div>
          )}
        </>
      )}

      {/* Join Request Dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("dialogTitle")}</DialogTitle>
            <DialogDescription>
              <span className="font-semibold text-foreground">
                {selectedTeam?.team_alias}
              </span>{" "}
              {t("dialogDesc")}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-2">
            <Label htmlFor="join-message">{t("dialogMessageLabel")}</Label>
            <textarea
              id="join-message"
              rows={3}
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              placeholder={t("dialogMessagePlaceholder")}
              className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
            />
          </div>

          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDialogOpen(false)}
              disabled={createJoinRequest.isPending}
            >
              {tc("cancel")}
            </Button>
            <Button
              onClick={handleSubmit}
              disabled={createJoinRequest.isPending}
            >
              {createJoinRequest.isPending ? t("dialogSubmitting") : t("dialogSubmit")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
