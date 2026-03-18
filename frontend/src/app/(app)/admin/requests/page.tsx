"use client";

import { useState } from "react";
import { Inbox } from "lucide-react";
import { toast } from "sonner";

import {
  useJoinRequests,
  useApproveRequest,
  useRejectRequest,
  useMe,
} from "@/hooks/use-api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
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
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import type { TeamJoinRequest, JoinRequestStatus } from "@/types";

const STATUS_LABELS: Record<JoinRequestStatus, string> = {
  pending: "대기중",
  approved: "승인",
  rejected: "거절",
};

function StatusBadge({ status }: { status: JoinRequestStatus }) {
  const styles: Record<JoinRequestStatus, string> = {
    pending:
      "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400",
    approved:
      "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400",
    rejected: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",
  };

  return <Badge className={styles[status]}>{STATUS_LABELS[status]}</Badge>;
}

function formatDate(dateStr: string) {
  return new Date(dateStr).toLocaleDateString("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
}

function TableSkeleton() {
  return (
    <div className="space-y-3">
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} className="flex gap-4">
          <div className="h-8 flex-1 animate-pulse rounded bg-muted" />
          <div className="h-8 w-24 animate-pulse rounded bg-muted" />
          <div className="h-8 w-32 animate-pulse rounded bg-muted" />
          <div className="h-8 w-16 animate-pulse rounded bg-muted" />
          <div className="h-8 w-24 animate-pulse rounded bg-muted" />
          <div className="h-8 w-28 animate-pulse rounded bg-muted" />
        </div>
      ))}
    </div>
  );
}

export default function AdminRequestsPage() {
  const { data: me } = useMe();
  const { data: requests, isLoading, isError } = useJoinRequests();
  const approveRequest = useApproveRequest();
  const rejectRequest = useRejectRequest();

  const [activeTab, setActiveTab] = useState("all");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [dialogAction, setDialogAction] = useState<"approve" | "reject">(
    "approve",
  );
  const [selectedRequest, setSelectedRequest] =
    useState<TeamJoinRequest | null>(null);
  const [comment, setComment] = useState("");

  const filteredRequests = requests?.filter((req) => {
    if (activeTab === "all") return true;
    return req.status === activeTab;
  });

  function openActionDialog(
    request: TeamJoinRequest,
    action: "approve" | "reject",
  ) {
    setSelectedRequest(request);
    setDialogAction(action);
    setComment("");
    setDialogOpen(true);
  }

  function handleConfirm() {
    if (!selectedRequest) return;

    const mutation =
      dialogAction === "approve" ? approveRequest : rejectRequest;

    mutation.mutate(
      {
        requestId: selectedRequest.id,
        body: comment.trim() ? { comment: comment.trim() } : undefined,
      },
      {
        onSuccess: () => {
          toast.success("요청이 처리되었습니다");
          setDialogOpen(false);
          setSelectedRequest(null);
        },
        onError: (error) => {
          const msg =
            error instanceof Error
              ? error.message
              : "요청 처리 중 오류가 발생했습니다";
          toast.error(msg);
        },
      },
    );
  }

  const isPending = approveRequest.isPending || rejectRequest.isPending;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold">가입 요청 관리</h1>
        <p className="text-muted-foreground mt-1">
          팀 가입 요청을 검토하세요
        </p>
      </div>

      {/* Error state */}
      {isError && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          가입 요청 목록을 불러오는 중 오류가 발생했습니다.
        </div>
      )}

      {/* Tabs + Table */}
      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="all">전체</TabsTrigger>
          <TabsTrigger value="pending">대기중</TabsTrigger>
          <TabsTrigger value="approved">승인</TabsTrigger>
          <TabsTrigger value="rejected">거절</TabsTrigger>
        </TabsList>

        <TabsContent value={activeTab} className="mt-4">
          {isLoading ? (
            <TableSkeleton />
          ) : filteredRequests && filteredRequests.length > 0 ? (
            <div className="rounded-lg border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>요청자</TableHead>
                    <TableHead>팀</TableHead>
                    <TableHead>메시지</TableHead>
                    <TableHead>상태</TableHead>
                    <TableHead>요청일</TableHead>
                    <TableHead>처리</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filteredRequests.map((req) => (
                    <TableRow key={req.id}>
                      <TableCell className="font-medium">
                        {req.requester_id}
                      </TableCell>
                      <TableCell>
                        {req.team_alias || req.team_id}
                      </TableCell>
                      <TableCell
                        className="max-w-[200px] truncate"
                        title={req.message ?? undefined}
                      >
                        {req.message || (
                          <span className="text-muted-foreground">-</span>
                        )}
                      </TableCell>
                      <TableCell>
                        <StatusBadge status={req.status} />
                      </TableCell>
                      <TableCell>{formatDate(req.created_at)}</TableCell>
                      <TableCell>
                        {req.status === "pending" ? (
                          <div className="flex gap-2">
                            <Button
                              size="xs"
                              className="bg-green-600 text-white hover:bg-green-700"
                              onClick={() =>
                                openActionDialog(req, "approve")
                              }
                            >
                              승인
                            </Button>
                            <Button
                              size="xs"
                              variant="destructive"
                              onClick={() =>
                                openActionDialog(req, "reject")
                              }
                            >
                              거절
                            </Button>
                          </div>
                        ) : (
                          <span className="text-muted-foreground text-sm">
                            -
                          </span>
                        )}
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
                처리할 가입 요청이 없습니다.
              </p>
            </div>
          )}
        </TabsContent>
      </Tabs>

      {/* Approve / Reject Confirmation Dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {dialogAction === "approve"
                ? "가입 요청 승인"
                : "가입 요청 거절"}
            </DialogTitle>
            <DialogDescription>
              <span className="font-semibold text-foreground">
                {selectedRequest?.requester_id}
              </span>
              님의{" "}
              <span className="font-semibold text-foreground">
                {selectedRequest?.team_alias || selectedRequest?.team_id}
              </span>{" "}
              팀 가입 요청을{" "}
              {dialogAction === "approve" ? "승인" : "거절"}합니다.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-2">
            <Label htmlFor="review-comment">코멘트 (선택사항)</Label>
            <textarea
              id="review-comment"
              rows={3}
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder="코멘트를 입력하세요..."
              className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
            />
          </div>

          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDialogOpen(false)}
              disabled={isPending}
            >
              취소
            </Button>
            <Button
              variant={
                dialogAction === "approve" ? "default" : "destructive"
              }
              onClick={handleConfirm}
              disabled={isPending}
              className={
                dialogAction === "approve"
                  ? "bg-green-600 text-white hover:bg-green-700"
                  : undefined
              }
            >
              {isPending
                ? "처리 중..."
                : dialogAction === "approve"
                  ? "승인"
                  : "거절"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
