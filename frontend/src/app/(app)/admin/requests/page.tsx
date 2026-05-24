"use client";

import { useState, useMemo } from "react";
import { Inbox, Search, X, ArrowLeft, ArrowRight } from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import {
  useJoinRequests,
  useApproveRequest,
  useRejectRequest,
  useMe,
} from "@/hooks/use-api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
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
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import type { TeamJoinRequest, JoinRequestStatus, RequestType } from "@/types";

function StatusBadge({ status }: { status: JoinRequestStatus }) {
  const t = useTranslations("requests");
  const styles: Record<JoinRequestStatus, string> = {
    pending:
      "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400",
    approved:
      "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400",
    rejected: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",
  };
  const labels: Record<JoinRequestStatus, string> = {
    pending: t("statusPending"),
    approved: t("statusApproved"),
    rejected: t("statusRejected"),
  };

  return <Badge className={styles[status]}>{labels[status]}</Badge>;
}

function TypeBadge({ type }: { type: RequestType }) {
  const t = useTranslations("requests");
  const styles: Record<RequestType, string> = {
    join: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
    budget: "bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400",
  };
  const labels: Record<RequestType, string> = {
    join: t("typeJoin"),
    budget: t("typeBudget"),
  };
  return <Badge className={styles[type]}>{labels[type]}</Badge>;
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
  const t = useTranslations("adminRequests");
  const tr = useTranslations("requests");
  useMe();
  const { data: requests, isLoading, isError } = useJoinRequests();
  const approveRequest = useApproveRequest();
  const rejectRequest = useRejectRequest();

  const [statusTab, setStatusTab] = useState("all");
  const [typeTab, setTypeTab] = useState("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [dialogAction, setDialogAction] = useState<"approve" | "reject">(
    "approve",
  );
  const [selectedRequest, setSelectedRequest] =
    useState<TeamJoinRequest | null>(null);
  const [detailRequest, setDetailRequest] = useState<TeamJoinRequest | null>(null);
  const [page, setPage] = useState(1);
  const pageSize = 20;
  const [comment, setComment] = useState("");

  const filteredRequests = useMemo(() => {
    if (!requests) return [];
    return requests.filter((req) => {
      const statusMatch = statusTab === "all" || req.status === statusTab;
      const typeMatch = typeTab === "all" || (req.request_type ?? "join") === typeTab;
      if (!statusMatch || !typeMatch) return false;
      if (searchQuery) {
        const q = searchQuery.toLowerCase();
        const requester = req.requester_id.toLowerCase();
        const team = (req.team_alias || req.team_id).toLowerCase();
        if (!requester.includes(q) && !team.includes(q)) return false;
      }
      return true;
    });
  }, [requests, statusTab, typeTab, searchQuery]);

  const totalPages = Math.max(1, Math.ceil(filteredRequests.length / pageSize));
  const safePageValue = Math.min(page, totalPages);
  const pageRequests = filteredRequests.slice((safePageValue - 1) * pageSize, safePageValue * pageSize);

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
          toast.success(t("toastProcessed"));
          setDialogOpen(false);
          setSelectedRequest(null);
        },
        onError: (error) => {
          const msg =
            error instanceof Error ? error.message : t("errorProcess");
          toast.error(msg);
        },
      },
    );
  }

  const isPending = approveRequest.isPending || rejectRequest.isPending;
  const reqType = (selectedRequest?.request_type ?? "join") as RequestType;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold">{t("title")}</h1>
        <p className="text-muted-foreground mt-1">
          {t("subtitle")}
        </p>
      </div>

      {/* Error state */}
      {isError && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          {tr("loadError")}
        </div>
      )}

      {/* Filters */}
      <div className="flex items-center gap-4 flex-wrap">
        <div className="relative flex-1 max-w-xs">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" />
          <Input
            placeholder={t("searchPlaceholder")}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="pl-8 h-9"
          />
        </div>
        <Tabs value={typeTab} onValueChange={setTypeTab}>
          <TabsList>
            <TabsTrigger value="all">{t("typeFilterAll")}</TabsTrigger>
            <TabsTrigger value="join">{tr("typeJoin")}</TabsTrigger>
            <TabsTrigger value="budget">{tr("typeBudget")}</TabsTrigger>
          </TabsList>
        </Tabs>
        {(searchQuery || typeTab !== "all") && (
          <Button variant="ghost" size="sm" onClick={() => { setSearchQuery(""); setTypeTab("all"); }}>
            <X className="size-3.5 mr-1" />
            {t("clearFilters")}
          </Button>
        )}
        {requests && (
          <span className="text-sm text-muted-foreground ml-auto">
            {tr("filteredCount", { filtered: filteredRequests.length, total: requests.length })}
          </span>
        )}
      </div>

      {/* Status tabs + Table */}
      <Tabs value={statusTab} onValueChange={(v) => { setStatusTab(v); setPage(1); }}>
        <TabsList>
          <TabsTrigger value="all">{tr("tabAll")}</TabsTrigger>
          <TabsTrigger value="pending">{tr("statusPending")}</TabsTrigger>
          <TabsTrigger value="approved">{tr("statusApproved")}</TabsTrigger>
          <TabsTrigger value="rejected">{tr("statusRejected")}</TabsTrigger>
        </TabsList>

        <TabsContent value={statusTab} className="mt-4">
          {isLoading ? (
            <TableSkeleton />
          ) : filteredRequests.length > 0 ? (
            <>
            <div className="rounded-lg border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{tr("colType")}</TableHead>
                    <TableHead>{t("colRequester")}</TableHead>
                    <TableHead>{tr("colTeam")}</TableHead>
                    <TableHead>{tr("colMessage")}</TableHead>
                    <TableHead>{tr("colStatus")}</TableHead>
                    <TableHead>{tr("colCreated")}</TableHead>
                    <TableHead>{t("colAction")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {pageRequests.map((req) => (
                    <TableRow key={req.id}>
                      <TableCell>
                        <TypeBadge type={(req.request_type ?? "join") as RequestType} />
                      </TableCell>
                      <TableCell className="font-medium">
                        {req.requester_id}
                      </TableCell>
                      <TableCell>
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
                              {t("approveBtn")}
                            </Button>
                            <Button
                              size="xs"
                              variant="destructive"
                              onClick={() =>
                                openActionDialog(req, "reject")
                              }
                            >
                              {t("rejectBtn")}
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
            {totalPages > 1 && (
              <div className="flex items-center justify-between mt-4">
                <p className="text-sm text-muted-foreground">
                  {tr("pageInfo", {
                    total: filteredRequests.length,
                    start: (safePageValue - 1) * pageSize + 1,
                    end: Math.min(safePageValue * pageSize, filteredRequests.length),
                  })}
                </p>
                <div className="flex items-center gap-2">
                  <Button variant="outline" size="sm" disabled={safePageValue <= 1} onClick={() => setPage((p) => p - 1)}>
                    <ArrowLeft className="size-4" />
                    {tr("prev")}
                  </Button>
                  <span className="text-sm text-muted-foreground">{safePageValue} / {totalPages}</span>
                  <Button variant="outline" size="sm" disabled={safePageValue >= totalPages} onClick={() => setPage((p) => p + 1)}>
                    {tr("next")}
                    <ArrowRight className="size-4" />
                  </Button>
                </div>
              </div>
            )}
          </>) : (
            <div className="flex flex-col items-center justify-center rounded-lg border border-dashed py-12 text-center">
              <Inbox className="size-10 text-muted-foreground mb-3" />
              <p className="text-muted-foreground">
                {t("empty")}
              </p>
            </div>
          )}
        </TabsContent>
      </Tabs>

      {/* Detail Modal */}
      <Dialog open={!!detailRequest} onOpenChange={(open) => !open && setDetailRequest(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{tr("detailTitle")}</DialogTitle>
          </DialogHeader>
          {detailRequest && (
            <div className="space-y-3 text-sm overflow-hidden">
              <div className="grid grid-cols-[80px_1fr] gap-2 min-w-0">
                <span className="text-muted-foreground">{tr("colType")}</span>
                <span><TypeBadge type={(detailRequest.request_type ?? "join") as RequestType} /></span>
                <span className="text-muted-foreground">{t("colRequester")}</span>
                <span className="font-medium">{detailRequest.requester_id}</span>
                <span className="text-muted-foreground">{tr("colTeam")}</span>
                <span className="font-medium">{detailRequest.team_alias || detailRequest.team_id}</span>
                <span className="text-muted-foreground">{tr("colStatus")}</span>
                <span><StatusBadge status={detailRequest.status} /></span>
                <span className="text-muted-foreground">{tr("colCreated")}</span>
                <span>{formatDate(detailRequest.created_at)}</span>
                {(detailRequest.request_type ?? "join") === "budget" && (
                  <>
                    <span className="text-muted-foreground">{tr("detailRequestedAmount")}</span>
                    <span className="font-medium text-purple-700 dark:text-purple-400">
                      ${detailRequest.requested_budget?.toFixed(2)}
                    </span>
                  </>
                )}
              </div>
              <div>
                <p className="text-muted-foreground mb-1">{tr("detailMessage")}</p>
                <p className="whitespace-pre-wrap break-words rounded-md bg-muted p-3">
                  {detailRequest.message || "-"}
                </p>
              </div>
              {detailRequest.review_comment && (
                <div>
                  <p className="text-muted-foreground mb-1">{tr("colReviewComment")}</p>
                  <p className="whitespace-pre-wrap break-words rounded-md bg-muted p-3">
                    {detailRequest.review_comment}
                  </p>
                </div>
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* Approve / Reject Confirmation Dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {dialogAction === "approve"
                ? t("dialogApproveTitle", { type: reqType === "budget" ? tr("typeBudget") : tr("typeJoin") })
                : t("dialogRejectTitle", { type: reqType === "budget" ? tr("typeBudget") : tr("typeJoin") })}
            </DialogTitle>
            <DialogDescription>
              {dialogAction === "approve"
                ? t("dialogApproveDescription", {
                    requester: selectedRequest?.requester_id ?? "",
                    team: selectedRequest?.team_alias || selectedRequest?.team_id || "",
                    type: reqType === "budget" ? tr("typeBudget") : tr("typeJoin"),
                  })
                : t("dialogRejectDescription", {
                    requester: selectedRequest?.requester_id ?? "",
                    team: selectedRequest?.team_alias || selectedRequest?.team_id || "",
                    type: reqType === "budget" ? tr("typeBudget") : tr("typeJoin"),
                  })}
              {reqType === "budget" && selectedRequest?.requested_budget != null && (
                <span className="block mt-1 font-semibold text-purple-700 dark:text-purple-400">
                  {t("dialogAmount", { amount: selectedRequest.requested_budget.toFixed(2) })}
                </span>
              )}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-2">
            <Label htmlFor="review-comment">{t("commentLabel")}</Label>
            <textarea
              id="review-comment"
              rows={3}
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder={t("commentPlaceholder")}
              className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
            />
          </div>

          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDialogOpen(false)}
              disabled={isPending}
            >
              {t("cancel")}
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
                ? t("processing")
                : dialogAction === "approve"
                  ? t("approveBtn")
                  : t("rejectBtn")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
