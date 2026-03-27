"use client";

import { Fragment, useState, useEffect } from "react";
import {
  Search,
  Loader2,
  DollarSign,
  ChevronLeft,
  ChevronRight,
  ChevronRight as ChevronExpand,
  Users,
  Key,
  Building,
  X,
  Trash2,
  AlertTriangle,
} from "lucide-react";
import { toast } from "sonner";

import { useBudgets, useBudgetDetails, useOrphanBudgets, useDeleteOrphanBudgets, useDeleteBudget, useDeleteBudgetsBatch } from "@/hooks/use-api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { Budget } from "@/types";

const PAGE_SIZE_OPTIONS = [10, 30, 50, 100, 300, 500, 1000] as const;
const DEFAULT_PAGE_SIZE = 50;

function formatBudget(value: number | null): string {
  if (value == null) return "무제한";
  return `$${value.toFixed(2)}`;
}

function formatDate(dateStr: string | null): string {
  if (!dateStr) return "-";
  return new Date(dateStr).toLocaleDateString("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
}

function BudgetDetailPanel({ budgetId }: { budgetId: string }) {
  const { data, isLoading } = useBudgetDetails(budgetId);

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 py-4 px-8">
        <Loader2 className="size-4 animate-spin" />
        <span className="text-sm text-muted-foreground">로딩 중...</span>
      </div>
    );
  }

  if (!data) return null;

  return (
    <div className="bg-muted/30 px-8 py-4 space-y-4">
      {/* Team Memberships */}
      {data.team_memberships.length > 0 && (
        <div>
          <div className="flex items-center gap-2 mb-2">
            <Users className="size-4 text-muted-foreground" />
            <span className="text-sm font-medium">팀 멤버십 ({data.team_memberships.length})</span>
          </div>
          <div className="rounded-md border bg-background">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>사번</TableHead>
                  <TableHead>팀</TableHead>
                  <TableHead>사용량</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.team_memberships.map((tm) => (
                  <TableRow key={`${tm.user_id}-${tm.team_id}`}>
                    <TableCell className="font-mono text-sm">{tm.user_id}</TableCell>
                    <TableCell>{tm.team_alias || tm.team_id}</TableCell>
                    <TableCell>${tm.spend.toFixed(2)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </div>
      )}

      {/* Keys */}
      {data.keys.length > 0 && (
        <div>
          <div className="flex items-center gap-2 mb-2">
            <Key className="size-4 text-muted-foreground" />
            <span className="text-sm font-medium">API 키 ({data.keys.length})</span>
          </div>
          <div className="rounded-md border bg-background">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>키</TableHead>
                  <TableHead>별칭</TableHead>
                  <TableHead>사번</TableHead>
                  <TableHead>팀</TableHead>
                  <TableHead>사용량</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.keys.map((k, i) => (
                  <TableRow key={i}>
                    <TableCell className="font-mono text-xs">{k.token}</TableCell>
                    <TableCell>{k.key_alias || k.key_name || "-"}</TableCell>
                    <TableCell className="font-mono text-sm">{k.user_id || "-"}</TableCell>
                    <TableCell>{k.team_id || "-"}</TableCell>
                    <TableCell>${k.spend.toFixed(2)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </div>
      )}

      {/* Organizations */}
      {data.organizations.length > 0 && (
        <div>
          <div className="flex items-center gap-2 mb-2">
            <Building className="size-4 text-muted-foreground" />
            <span className="text-sm font-medium">조직 ({data.organizations.length})</span>
          </div>
          <div className="flex flex-wrap gap-2">
            {data.organizations.map((o) => (
              <Badge key={o.organization_id} variant="outline">
                {o.organization_alias || o.organization_id}
              </Badge>
            ))}
          </div>
        </div>
      )}

      {/* Projects */}
      {data.projects && data.projects.length > 0 && (
        <div>
          <div className="flex items-center gap-2 mb-2">
            <Building className="size-4 text-muted-foreground" />
            <span className="text-sm font-medium">프로젝트 ({data.projects.length})</span>
          </div>
          <div className="flex flex-wrap gap-2">
            {data.projects.map((p) => (
              <Badge key={p.project_id} variant="outline">{p.project_name || p.project_id}</Badge>
            ))}
          </div>
        </div>
      )}

      {/* End Users */}
      {data.end_users && data.end_users.length > 0 && (
        <div>
          <div className="flex items-center gap-2 mb-2">
            <Users className="size-4 text-muted-foreground" />
            <span className="text-sm font-medium">엔드유저 ({data.end_users.length})</span>
          </div>
          <div className="flex flex-wrap gap-2">
            {data.end_users.map((eu) => (
              <Badge key={eu.user_id} variant="outline">{eu.alias || eu.user_id} (${eu.spend.toFixed(2)})</Badge>
            ))}
          </div>
        </div>
      )}

      {/* Tags */}
      {data.tags && data.tags.length > 0 && (
        <div>
          <div className="flex items-center gap-2 mb-2">
            <Key className="size-4 text-muted-foreground" />
            <span className="text-sm font-medium">태그 ({data.tags.length})</span>
          </div>
          <div className="flex flex-wrap gap-2">
            {data.tags.map((tag) => (
              <Badge key={tag} variant="secondary">{tag}</Badge>
            ))}
          </div>
        </div>
      )}

      {/* Org Memberships */}
      {data.org_memberships && data.org_memberships.length > 0 && (
        <div>
          <div className="flex items-center gap-2 mb-2">
            <Building className="size-4 text-muted-foreground" />
            <span className="text-sm font-medium">조직 멤버십 ({data.org_memberships.length})</span>
          </div>
          <div className="flex flex-wrap gap-2">
            {data.org_memberships.map((om) => (
              <Badge key={`${om.user_id}-${om.organization_id}`} variant="outline">
                {om.user_id} @ {om.organization_id}
              </Badge>
            ))}
          </div>
        </div>
      )}

      {data.team_memberships.length === 0 && data.keys.length === 0 && data.organizations.length === 0
        && (!data.projects || data.projects.length === 0) && (!data.end_users || data.end_users.length === 0)
        && (!data.tags || data.tags.length === 0) && (!data.org_memberships || data.org_memberships.length === 0) && (
        <p className="text-sm text-muted-foreground">연결된 항목이 없습니다.</p>
      )}
    </div>
  );
}

export default function BudgetManagementPage() {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [idInput, setIdInput] = useState("");
  const [amountInput, setAmountInput] = useState("");
  const [searchId, setSearchId] = useState("");
  const [searchAmount, setSearchAmount] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [orphansOnly, setOrphansOnly] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  useEffect(() => {
    const timer = setTimeout(() => {
      setSearchId(idInput);
      setSearchAmount(amountInput);
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [idInput, amountInput]);

  const { data, isLoading } = useBudgets(page, pageSize, searchId, searchAmount, orphansOnly);
  const { data: orphanData } = useOrphanBudgets();
  const deleteOrphansMutation = useDeleteOrphanBudgets();
  const deleteBudgetMutation = useDeleteBudget();
  const deleteBatchMutation = useDeleteBudgetsBatch();

  const toggleExpand = (budgetId: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(budgetId)) next.delete(budgetId);
      else next.add(budgetId);
      return next;
    });
  };

  const toggleSelect = (budgetId: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(budgetId)) next.delete(budgetId);
      else next.add(budgetId);
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (!data) return;
    const allIds = data.budgets.map((b) => b.budget_id);
    const allSelected = allIds.every((id) => selected.has(id));
    if (allSelected) {
      setSelected((prev) => {
        const next = new Set(prev);
        allIds.forEach((id) => next.delete(id));
        return next;
      });
    } else {
      setSelected((prev) => {
        const next = new Set(prev);
        allIds.forEach((id) => next.add(id));
        return next;
      });
    }
  };

  const totalPages = data ? Math.ceil(data.total / pageSize) : 0;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">예산 관리</h1>
        <p className="text-muted-foreground mt-1">
          LiteLLM 예산(Budget)을 조회하고 연결된 멤버십/키/조직을 확인합니다
        </p>
      </div>

      {/* Stats + Orphan cleanup */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {data && (
          <Card>
            <CardHeader className="flex flex-row items-center justify-between pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">전체 예산</CardTitle>
              <DollarSign className="size-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{data.total}개</div>
            </CardContent>
          </Card>
        )}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">미연결 예산</CardTitle>
            <AlertTriangle className="size-4 text-muted-foreground" />
          </CardHeader>
          <CardContent className="space-y-2">
            <div className="text-2xl font-bold text-amber-600">{orphanData?.count ?? "-"}개</div>
            {orphanData && orphanData.count > 0 && (
              <Button
                variant="destructive"
                size="sm"
                disabled={deleteOrphansMutation.isPending}
                onClick={() => {
                  if (!confirm(`미연결 예산 ${orphanData.count.toLocaleString()}개를 모두 삭제하시겠습니까?\n대량 데이터의 경우 시간이 걸릴 수 있습니다.`)) return;
                  toast.info("미연결 예산 삭제를 시작합니다. 잠시 기다려주세요...");
                  deleteOrphansMutation.mutate(undefined, {
                    onSuccess: (res) => toast.success(`${res.deleted.toLocaleString()}개의 미연결 예산이 삭제되었습니다.`),
                    onError: (err) => toast.error(err instanceof Error ? err.message : "삭제 실패"),
                  });
                }}
              >
                <Trash2 className="size-3.5 mr-1" />
                {deleteOrphansMutation.isPending ? "삭제 중... (시간이 걸릴 수 있습니다)" : `일괄 삭제 (${orphanData.count.toLocaleString()}개)`}
              </Button>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Search */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1 max-w-xs">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" />
          <Input
            placeholder="Budget ID 검색..."
            value={idInput}
            onChange={(e) => setIdInput(e.target.value)}
            className="pl-8 h-9"
          />
        </div>
        <div className="w-[140px]">
          <Input
            type="number"
            step="0.01"
            placeholder="금액 검색..."
            value={amountInput}
            onChange={(e) => setAmountInput(e.target.value)}
            className="h-9"
          />
        </div>
        <Button
          variant={orphansOnly ? "default" : "outline"}
          size="sm"
          className="h-9"
          onClick={() => { setOrphansOnly(!orphansOnly); setPage(1); }}
        >
          <AlertTriangle className="size-3.5 mr-1" />
          미연결만
        </Button>
        {(searchId || searchAmount || orphansOnly) && (
          <Button variant="ghost" size="sm" onClick={() => { setIdInput(""); setAmountInput(""); setSearchId(""); setSearchAmount(""); setOrphansOnly(false); }}>
            <X className="size-3.5 mr-1" />
            초기화
          </Button>
        )}
      </div>

      {/* Batch delete bar */}
      {selected.size > 0 && (
        <div className="flex items-center gap-3 rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-2">
          <span className="text-sm font-medium">{selected.size}개 선택됨</span>
          <Button
            variant="destructive"
            size="sm"
            disabled={deleteBatchMutation.isPending}
            onClick={() => {
              if (!confirm(`선택한 ${selected.size}개의 예산을 삭제하시겠습니까?\n연결된 항목이 있는 예산은 건너뜁니다.`)) return;
              deleteBatchMutation.mutate([...selected], {
                onSuccess: (res) => {
                  toast.success(`${res.deleted}개 삭제 완료${res.skipped > 0 ? ` (연결된 ${res.skipped}개 건너뜀)` : ""}`);
                  setSelected(new Set());
                },
                onError: (err) => toast.error(err instanceof Error ? err.message : "삭제 실패"),
              });
            }}
          >
            <Trash2 className="size-3.5 mr-1" />
            {deleteBatchMutation.isPending ? "삭제 중..." : "선택 삭제"}
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}>
            선택 해제
          </Button>
        </div>
      )}

      {/* Table */}
      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="size-6 animate-spin text-muted-foreground" />
        </div>
      ) : !data || data.budgets.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-lg border border-dashed py-16">
          <DollarSign className="size-10 text-muted-foreground mb-3" />
          <p className="text-muted-foreground">예산이 없습니다.</p>
        </div>
      ) : (
        <>
          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-10">
                    <input
                      type="checkbox"
                      className="size-4 rounded border-gray-300"
                      checked={data.budgets.length > 0 && data.budgets.every((b) => selected.has(b.budget_id))}
                      onChange={toggleSelectAll}
                    />
                  </TableHead>
                  <TableHead className="w-10" />
                  <TableHead>Budget ID</TableHead>
                  <TableHead>한도</TableHead>
                  <TableHead>주기</TableHead>
                  <TableHead>초기화</TableHead>
                  <TableHead>멤버십</TableHead>
                  <TableHead>키</TableHead>
                  <TableHead>조직</TableHead>
                  <TableHead>생성일</TableHead>
                  <TableHead className="w-10" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.budgets.map((b) => {
                  const isExpanded = expanded.has(b.budget_id);
                  const linkedTotal = b.team_membership_count + b.key_count + b.org_count;
                  return (
                    <Fragment key={b.budget_id}>
                      <TableRow
                        className={linkedTotal > 0 ? "cursor-pointer" : ""}
                        onClick={() => linkedTotal > 0 && toggleExpand(b.budget_id)}
                      >
                        <TableCell onClick={(e) => e.stopPropagation()}>
                          <input
                            type="checkbox"
                            className="size-4 rounded border-gray-300"
                            checked={selected.has(b.budget_id)}
                            onChange={() => toggleSelect(b.budget_id)}
                          />
                        </TableCell>
                        <TableCell>
                          {linkedTotal > 0 && (
                            <ChevronExpand
                              className={`size-4 transition-transform ${isExpanded ? "rotate-90" : ""}`}
                            />
                          )}
                        </TableCell>
                        <TableCell className="font-mono text-xs max-w-[200px] truncate" title={b.budget_id}>
                          {b.budget_id}
                        </TableCell>
                        <TableCell className="font-medium">{formatBudget(b.max_budget)}</TableCell>
                        <TableCell className="text-sm">{b.budget_duration || "-"}</TableCell>
                        <TableCell className="text-sm">{formatDate(b.budget_reset_at)}</TableCell>
                        <TableCell>
                          {b.team_membership_count > 0 ? (
                            <Badge variant="secondary" className="gap-1">
                              <Users className="size-3" />
                              {b.team_membership_count}
                            </Badge>
                          ) : "-"}
                        </TableCell>
                        <TableCell>
                          {b.key_count > 0 ? (
                            <Badge variant="secondary" className="gap-1">
                              <Key className="size-3" />
                              {b.key_count}
                            </Badge>
                          ) : "-"}
                        </TableCell>
                        <TableCell>
                          {b.org_count > 0 ? (
                            <Badge variant="secondary" className="gap-1">
                              <Building className="size-3" />
                              {b.org_count}
                            </Badge>
                          ) : "-"}
                        </TableCell>
                        <TableCell className="text-sm">{formatDate(b.created_at)}</TableCell>
                        <TableCell>
                          <Button
                            variant="ghost"
                            size="icon-xs"
                            className="text-destructive hover:text-destructive"
                            disabled={deleteBudgetMutation.isPending}
                            onClick={(e) => {
                              e.stopPropagation();
                              const linkedTotal = b.team_membership_count + b.key_count + b.org_count
                                + b.project_count + b.end_user_count + b.tag_count + b.org_membership_count;
                              if (linkedTotal > 0) {
                                toast.error(`이 예산에 ${linkedTotal}개의 연결된 항목이 있어 삭제할 수 없습니다.`);
                                return;
                              }
                              if (confirm(`예산 ${b.budget_id.slice(0, 8)}...을 삭제하시겠습니까?`)) {
                                deleteBudgetMutation.mutate(b.budget_id, {
                                  onSuccess: () => toast.success("예산이 삭제되었습니다."),
                                  onError: (err) => toast.error(err instanceof Error ? err.message : "삭제 실패"),
                                });
                              }
                            }}
                          >
                            <Trash2 className="size-3.5" />
                          </Button>
                        </TableCell>
                      </TableRow>
                      {isExpanded && (
                        <TableRow>
                          <TableCell colSpan={11} className="p-0">
                            <BudgetDetailPanel budgetId={b.budget_id} />
                          </TableCell>
                        </TableRow>
                      )}
                    </Fragment>
                  );
                })}
              </TableBody>
            </Table>
          </div>

          {/* Pagination */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <p className="text-sm text-muted-foreground">
                총 {data.total}개 중 {(page - 1) * pageSize + 1}–{Math.min(page * pageSize, data.total)}
              </p>
              <select
                className="h-8 rounded-md border border-input bg-background px-2 text-sm"
                value={pageSize}
                onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1); }}
              >
                {PAGE_SIZE_OPTIONS.map((size) => (
                  <option key={size} value={size}>{size}개씩</option>
                ))}
              </select>
            </div>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" disabled={page === 1} onClick={() => setPage(page - 1)}>
                <ChevronLeft className="size-4" />
                이전
              </Button>
              <span className="text-sm text-muted-foreground">
                {page} / {totalPages || 1}
              </span>
              <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>
                다음
                <ChevronRight className="size-4" />
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
