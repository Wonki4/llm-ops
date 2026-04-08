"use client";

import { useMemo, useState } from "react";
import { Inbox, Search, X } from "lucide-react";

import { useJoinRequests } from "@/hooks/use-api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { JoinRequestStatus, RequestType, TeamJoinRequest } from "@/types";

const STATUS_LABELS: Record<JoinRequestStatus, string> = {
  pending: "대기중",
  approved: "승인",
  rejected: "거절",
};

const TYPE_LABELS: Record<RequestType, string> = {
  join: "팀 가입",
  budget: "예산 증액",
};

function StatusBadge({ status }: { status: JoinRequestStatus }) {
  const styles: Record<JoinRequestStatus, string> = {
    pending: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400",
    approved: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400",
    rejected: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",
  };
  return <Badge className={styles[status]}>{STATUS_LABELS[status]}</Badge>;
}

function TypeBadge({ type }: { type: RequestType }) {
  const styles: Record<RequestType, string> = {
    join: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
    budget: "bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400",
  };
  return <Badge className={styles[type]}>{TYPE_LABELS[type]}</Badge>;
}

function formatDate(dateStr: string) {
  return new Date(dateStr).toLocaleDateString("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
}

export default function MyRequestsPage() {
  const { data: requests, isLoading, isError } = useJoinRequests(undefined, undefined, true);
  const [statusTab, setStatusTab] = useState("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [detailRequest, setDetailRequest] = useState<TeamJoinRequest | null>(null);

  const filteredRequests = useMemo(() => {
    if (!requests) return [];
    return requests.filter((req) => {
      if (statusTab !== "all" && req.status !== statusTab) return false;
      if (searchQuery) {
        const q = searchQuery.toLowerCase();
        const team = (req.team_alias || req.team_id).toLowerCase();
        if (!team.includes(q)) return false;
      }
      return true;
    });
  }, [requests, statusTab, searchQuery]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">내 요청</h1>
        <p className="text-muted-foreground mt-1">
          내가 보낸 팀 가입 및 예산 증액 요청 현황입니다
        </p>
      </div>

      {isError && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          요청 목록을 불러오는 중 오류가 발생했습니다.
        </div>
      )}

      <div className="flex items-center gap-3 flex-wrap">
        <div className="relative flex-1 max-w-xs">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" />
          <Input
            placeholder="팀명 검색..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="pl-8 h-9"
          />
        </div>
        {searchQuery && (
          <Button variant="ghost" size="sm" onClick={() => setSearchQuery("")}>
            <X className="size-3.5 mr-1" />
            초기화
          </Button>
        )}
        {requests && (
          <span className="text-sm text-muted-foreground ml-auto">
            {filteredRequests.length} / {requests.length}개
          </span>
        )}
      </div>

      <Tabs value={statusTab} onValueChange={setStatusTab}>
        <TabsList>
          <TabsTrigger value="all">전체</TabsTrigger>
          <TabsTrigger value="pending">대기중</TabsTrigger>
          <TabsTrigger value="approved">승인</TabsTrigger>
          <TabsTrigger value="rejected">거절</TabsTrigger>
        </TabsList>

        <TabsContent value={statusTab} className="mt-4">
          {isLoading ? (
            <div className="space-y-3">
              {Array.from({ length: 3 }).map((_, i) => (
                <div key={i} className="flex gap-4">
                  <div className="h-8 flex-1 animate-pulse rounded bg-muted" />
                  <div className="h-8 w-24 animate-pulse rounded bg-muted" />
                  <div className="h-8 w-20 animate-pulse rounded bg-muted" />
                </div>
              ))}
            </div>
          ) : filteredRequests.length > 0 ? (
            <div className="rounded-lg border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>유형</TableHead>
                    <TableHead>팀</TableHead>
                    <TableHead>내용</TableHead>
                    <TableHead>상태</TableHead>
                    <TableHead>요청일</TableHead>
                    <TableHead>처리 코멘트</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filteredRequests.map((req) => (
                    <TableRow key={req.id}>
                      <TableCell>
                        <TypeBadge type={(req.request_type ?? "join") as RequestType} />
                      </TableCell>
                      <TableCell className="font-medium">
                        {req.team_alias || req.team_id}
                      </TableCell>
                      <TableCell className="max-w-[200px]">
                        <button
                          type="button"
                          className="block w-full text-left truncate text-sm text-muted-foreground hover:text-foreground cursor-pointer"
                          onClick={() => setDetailRequest(req)}
                        >
                          {(req.request_type ?? "join") === "budget" ? (
                            <span className="font-medium text-purple-700 dark:text-purple-400">
                              ${req.requested_budget?.toFixed(2)}
                              {req.message && ` - ${req.message}`}
                            </span>
                          ) : (
                            req.message || "-"
                          )}
                        </button>
                      </TableCell>
                      <TableCell>
                        <StatusBadge status={req.status} />
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        {formatDate(req.created_at)}
                      </TableCell>
                      <TableCell className="max-w-[200px]">
                        <button
                          type="button"
                          className="block w-full text-left truncate text-sm text-muted-foreground hover:text-foreground cursor-pointer"
                          onClick={() => setDetailRequest(req)}
                        >
                          {req.review_comment || "-"}
                        </button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center rounded-lg border border-dashed py-12 text-center">
              <Inbox className="size-10 text-muted-foreground mb-3" />
              <p className="text-muted-foreground">
                {searchQuery ? "검색 결과가 없습니다." : "요청 내역이 없습니다."}
              </p>
            </div>
          )}
        </TabsContent>
      </Tabs>

      {/* Detail Modal */}
      <Dialog open={!!detailRequest} onOpenChange={(open) => !open && setDetailRequest(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>요청 상세</DialogTitle>
          </DialogHeader>
          {detailRequest && (
            <div className="space-y-3 text-sm">
              <div className="grid grid-cols-[80px_1fr] gap-2">
                <span className="text-muted-foreground">유형</span>
                <span><TypeBadge type={(detailRequest.request_type ?? "join") as RequestType} /></span>
                <span className="text-muted-foreground">팀</span>
                <span className="font-medium">{detailRequest.team_alias || detailRequest.team_id}</span>
                <span className="text-muted-foreground">상태</span>
                <span><StatusBadge status={detailRequest.status} /></span>
                <span className="text-muted-foreground">요청일</span>
                <span>{formatDate(detailRequest.created_at)}</span>
                {(detailRequest.request_type ?? "join") === "budget" && (
                  <>
                    <span className="text-muted-foreground">요청 금액</span>
                    <span className="font-medium text-purple-700 dark:text-purple-400">
                      ${detailRequest.requested_budget?.toFixed(2)}
                    </span>
                  </>
                )}
              </div>
              <div>
                <p className="text-muted-foreground mb-1">요청 내용</p>
                <p className="whitespace-pre-wrap break-words rounded-md bg-muted p-3">
                  {detailRequest.message || "-"}
                </p>
              </div>
              {detailRequest.review_comment && (
                <div>
                  <p className="text-muted-foreground mb-1">처리 코멘트</p>
                  <p className="whitespace-pre-wrap break-words rounded-md bg-muted p-3">
                    {detailRequest.review_comment}
                  </p>
                </div>
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
