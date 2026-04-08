"use client";

import { useState } from "react";
import { Search, Users, Box, Shield } from "lucide-react";
import { toast } from "sonner";

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
  CardDescription,
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
          toast.success("가입 요청이 전송되었습니다");
          setDialogOpen(false);
          setSelectedTeam(null);
        },
        onError: (error) => {
          const msg =
            error instanceof Error ? error.message : "요청 처리 중 오류가 발생했습니다";
          toast.error(msg);
        },
      },
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold">팀 탐색</h1>
        <p className="text-muted-foreground mt-1">가입 가능한 팀을 찾아보세요</p>
      </div>

      {/* Search */}
      <div className="flex items-center gap-3 max-w-lg">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="팀 이름으로 검색..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="pl-9"
          />
        </div>
        <div className="flex items-center gap-1 shrink-0">
          {([
            ["all", "전체"],
            ["joined", "가입됨"],
            ["not_joined", "미가입"],
            ["pending", "요청중"],
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
          팀 목록을 불러오는 중 오류가 발생했습니다.
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
                  </CardHeader>
                  <CardContent className="space-y-2">
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      <Box className="size-4" />
                      <span>{team.models.includes("all-proxy-models") ? "모든 모델 사용 가능" : `${team.models.length}개 모델 사용 가능`}</span>
                    </div>
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      <Shield className="size-4" />
                      <span>관리자: {team.admins.length > 0 ? team.admins.join(", ") : "-"}</span>
                    </div>
                  </CardContent>
                  <CardFooter>
                    {team.is_member ? (
                      <Badge className="bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400">
                        가입됨
                      </Badge>
                    ) : team.has_pending_request ? (
                      <Badge className="bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400">
                        요청중
                      </Badge>
                    ) : (
                      <Button
                        size="sm"
                        onClick={() => handleRequestClick(team)}
                      >
                        가입 요청
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
                {searchQuery
                  ? "검색 결과가 없습니다."
                  : "가입 가능한 팀이 없습니다."}
              </p>
            </div>
          )}
        </>
      )}

      {/* Join Request Dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>팀 가입 요청</DialogTitle>
            <DialogDescription>
              <span className="font-semibold text-foreground">
                {selectedTeam?.team_alias}
              </span>{" "}
              팀에 가입을 요청합니다.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-2">
            <Label htmlFor="join-message">가입 사유 (선택사항)</Label>
            <textarea
              id="join-message"
              rows={3}
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              placeholder="가입 사유를 입력하세요..."
              className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
            />
          </div>

          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDialogOpen(false)}
              disabled={createJoinRequest.isPending}
            >
              취소
            </Button>
            <Button
              onClick={handleSubmit}
              disabled={createJoinRequest.isPending}
            >
              {createJoinRequest.isPending ? "요청 중..." : "요청 보내기"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
