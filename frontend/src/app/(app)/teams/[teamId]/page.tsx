"use client";

import { Fragment, use, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useTeamDetail, useTeamMembers, useDeleteKey, useRevealKey, useModels, useChangeMemberRole, useChangeMemberBudget, useRemoveTeamMember, useCreateBudgetRequest, useUpdateTeamSettings } from "@/hooks/use-api";
import { toast } from "sonner";
import { ModelDetailSheet } from "@/components/model-detail-sheet";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
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
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Separator } from "@/components/ui/separator";
import {
  ArrowLeft,
  ArrowRight,
  Shield,
  Trash2,
  Plus,
  AlertCircle,
  RefreshCw,
  Loader2,
  Boxes,
  Key,
  Users,
  DollarSign,
  Eye,
  Zap,
  Globe,
  ChevronRight,
  Save,
  Copy,
  Check,
} from "lucide-react";
import type { ApiKey, TeamMember, ModelWithCatalog, ModelStatus } from "@/types";

const STATUS_STYLES: Record<ModelStatus, string> = {
  testing: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
  prerelease: "bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400",
  lts: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400",
  deprecating: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400",
  deprecated: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",
};

function StatusBadge({ status }: { status: ModelStatus }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ${STATUS_STYLES[status]}`}
    >
      {status}
    </span>
  );
}

function formatTokenCost(cost: number | null): string {
  return cost != null ? `$ ${(cost * 1_000_000).toFixed(2)}` : "-";
}

function formatBudget(spend: number, maxBudget: number | null): string {
  const spendStr = `$${spend.toFixed(2)}`;
  if (maxBudget === null) return `${spendStr} / 무제한`;
  return `${spendStr} / $${maxBudget.toFixed(2)}`;
}

function budgetPercent(spend: number, maxBudget: number | null): number {
  if (maxBudget === null || maxBudget === 0) return 0;
  return Math.min((spend / maxBudget) * 100, 100);
}

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString("ko-KR", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function formatResetDate(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString("ko-KR", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatBudgetDuration(duration: string | null): string | null {
  if (!duration) return null;
  const match = duration.match(/^(\d+)([dhms])$/);
  if (!match) return duration;
  const [, num, unit] = match;
  const unitMap: Record<string, string> = { d: "일", h: "시간", m: "분", s: "초" };
  return `${num}${unitMap[unit] || unit}`;
}

function maskKey(token: string): string {
  if (token.length <= 8) return token;
  return token.slice(0, 8) + "...";
}

function DeleteKeyDialog({
  keyItem,
  onDelete,
  isDeleting,
}: {
  keyItem: ApiKey;
  onDelete: (keyHash: string) => void;
  isDeleting: boolean;
}) {
  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="ghost" size="icon-xs" className="text-destructive hover:text-destructive">
          <Trash2 className="size-3.5" />
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>키 삭제</DialogTitle>
          <DialogDescription>
            &quot;{keyItem.key_alias || keyItem.token.slice(0, 8)}&quot; 키를 삭제하시겠습니까?
            이 작업은 되돌릴 수 없습니다.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">취소</Button>
          </DialogClose>
          <Button
            variant="destructive"
            disabled={isDeleting}
            onClick={() => onDelete(keyItem.token)}
          >
            {isDeleting && <Loader2 className="size-4 animate-spin" />}
            삭제
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function BudgetRequestDialog({ teamId, currentBudget }: { teamId: string; currentBudget: number | null }) {
  const [open, setOpen] = useState(false);
  const [amount, setAmount] = useState("");
  const [message, setMessage] = useState("");
  const mutation = useCreateBudgetRequest();

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" className="w-full mt-2">
          <DollarSign className="size-3.5 mr-1" />
          예산 변경 요청
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>예산 변경 요청</DialogTitle>
          <DialogDescription>
            팀 관리자에게 예산 변경을 요청합니다.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="rounded-md bg-muted p-3 text-sm">
            <div className="flex justify-between">
              <span className="text-muted-foreground">현재 예산</span>
              <span className="font-medium">{currentBudget === null ? "무제한" : `$${currentBudget.toFixed(2)}`}</span>
            </div>
            {amount && Number(amount) > 0 && (
              <div className="flex justify-between mt-1 pt-1 border-t border-border">
                <span className="text-muted-foreground">변경 후 예산</span>
                <span className="font-medium text-primary">${Number(amount).toFixed(2)}</span>
              </div>
            )}
          </div>
          <div>
            <label className="text-sm font-medium">변경 금액 ($)</label>
            <Input
              type="number"
              step="0.01"
              min="0"
              placeholder="예: 100"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
            />
          </div>
          <div>
            <label className="text-sm font-medium">사유 (선택)</label>
            <Input
              placeholder="변경이 필요한 이유를 입력하세요"
              value={message}
              onChange={(e) => setMessage(e.target.value)}
            />
          </div>
        </div>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="ghost">취소</Button>
          </DialogClose>
          <Button
            disabled={!amount || Number(amount) <= 0 || mutation.isPending}
            onClick={() => {
              mutation.mutate(
                { team_id: teamId, requested_budget: Number(amount), message: message || undefined },
                {
                  onSuccess: () => {
                    toast.success("예산 변경 요청이 제출되었습니다.");
                    setOpen(false);
                    setAmount("");
                    setMessage("");
                  },
                  onError: (err) => {
                    toast.error(err instanceof Error ? err.message : "요청 실패");
                  },
                },
              );
            }}
          >
            {mutation.isPending ? <Loader2 className="size-4 animate-spin" /> : "요청 제출"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function OverviewTab({
  team,
  isAdmin,
  myKeys,
  myMembership,
  modelsByName,
  onMoveToKeys,
  onMoveToModels,
  onSelectModel,
}: {
  team: {
    team_id: string;
    team_alias: string;
    spend: number;
    max_budget: number | null;
    budget_duration: string | null;
    budget_reset_at: string | null;
    models: string[];
    members: string[];
    admins: string[];
    member_count?: number;
    admin_count?: number;
  };
  isAdmin: boolean;
  myKeys: ApiKey[];
  myMembership: { spend: number; max_budget: number | null; budget_duration: string | null; budget_reset_at: string | null };
  modelsByName: Map<string, ModelWithCatalog>;
  onMoveToKeys: () => void;
  onMoveToModels: () => void;
  onSelectModel: (model: ModelWithCatalog) => void;
}) {
  const totalMembers = team.member_count ?? team.members.length;
  const totalAdmins = team.admin_count ?? team.admins.length;
  const pct = budgetPercent(team.spend, team.max_budget);
  const mySpend = myMembership.spend;
  const myMaxBudget = myMembership.max_budget;
  const myPct = budgetPercent(mySpend, myMaxBudget);
  const topKeys = [...myKeys].sort((a, b) => b.spend - a.spend).slice(0, 3);
  const scopedModels = team.models.slice(0, 5).map((modelName) => ({
    modelName,
    model: modelsByName.get(modelName) ?? null,
  }));
  const memberOnly = team.members.filter((member) => !team.admins.includes(member));
  const remainingAdmins = totalAdmins - team.admins.length;
  const remainingMembers = (totalMembers - totalAdmins) - memberOnly.length;

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">팀 예산</CardTitle>
            <DollarSign className="size-4 text-muted-foreground" />
          </CardHeader>
          <CardContent className="space-y-2">
            <div className="text-2xl font-bold">{formatBudget(team.spend, team.max_budget)}</div>
            <p className="text-xs text-muted-foreground">팀 전체 사용량</p>
            <div className="h-2 w-full rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-primary transition-all"
                style={{ width: `${team.max_budget === null ? 0 : pct}%` }}
              />
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">내 사용량</CardTitle>
            <DollarSign className="size-4 text-muted-foreground" />
          </CardHeader>
          <CardContent className="space-y-2">
            <div className="text-2xl font-bold">{formatBudget(mySpend, myMaxBudget)}</div>
            <p className="text-xs text-muted-foreground">팀 내 내 예산</p>
            <div className="h-2 w-full rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-blue-500 transition-all"
                style={{ width: `${myMaxBudget === null ? 0 : myPct}%` }}
              />
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">내 키</CardTitle>
            <Key className="size-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{myKeys.length}개</div>
            <p className="text-xs text-muted-foreground">생성된 API 키</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">모델</CardTitle>
            <Boxes className="size-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{team.models.includes("all-proxy-models") ? "전체" : `${team.models.length}개`}</div>
            <p className="text-xs text-muted-foreground">사용 가능한 모델</p>
          </CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">팀 예산 상세</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex items-end justify-between gap-3">
                <div>
                  <p className="text-sm text-muted-foreground">팀 전체 사용량</p>
                  <p className="text-2xl font-bold">{formatBudget(team.spend, team.max_budget)}</p>
                </div>
                <p className="text-sm font-medium text-muted-foreground">
                  {team.max_budget === null ? "무제한" : `${pct.toFixed(1)}%`}
                </p>
              </div>
              <div className="h-2 w-full rounded-full bg-muted">
                <div
                  className="h-full rounded-full bg-primary transition-all"
                  style={{ width: `${team.max_budget === null ? 0 : pct}%` }}
                />
              </div>
              <div className="space-y-1 text-xs text-muted-foreground">
                <p>예산 주기: {team.budget_duration ? `${formatBudgetDuration(team.budget_duration)} 주기` : "-"}</p>
                <p>예산 초기화: {team.budget_reset_at ? formatResetDate(team.budget_reset_at) : "-"}</p>
              </div>
              <Separator />
              <div className="flex items-end justify-between gap-3">
                <div>
                  <p className="text-sm text-muted-foreground">내 사용량</p>
                  <p className="text-2xl font-bold">{formatBudget(mySpend, myMaxBudget)}</p>
                </div>
                <p className="text-sm font-medium text-muted-foreground">
                  {myMaxBudget === null ? "무제한" : `${myPct.toFixed(1)}%`}
                </p>
              </div>
              <div className="h-2 w-full rounded-full bg-muted">
                <div
                  className="h-full rounded-full bg-blue-500 transition-all"
                  style={{ width: `${myMaxBudget === null ? 0 : myPct}%` }}
                />
              </div>
              <div className="space-y-1 text-xs text-muted-foreground">
                <p>예산 주기: {myMembership.budget_duration ? `${formatBudgetDuration(myMembership.budget_duration)} 주기` : "-"}</p>
                <p>예산 초기화: {myMembership.budget_reset_at ? formatResetDate(myMembership.budget_reset_at) : "-"}</p>
              </div>
              <BudgetRequestDialog teamId={team.team_id} currentBudget={myMaxBudget} />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">팀원 정보</CardTitle>
            </CardHeader>
            <CardContent className="space-y-5">
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 text-sm font-medium">
                    <Shield className="size-4" />
                    관리자
                  </div>
                  <span className="text-xs text-muted-foreground">{totalAdmins.toLocaleString()}명</span>
                </div>
                <div className="flex flex-wrap gap-2">
                  {team.admins.length === 0 ? (
                    <p className="text-sm text-muted-foreground">관리자가 없습니다.</p>
                  ) : (
                    <>
                      {team.admins.map((admin) => (
                        <Badge key={admin} variant="secondary" className="gap-1">
                          <Shield className="size-3" />
                          {admin}
                        </Badge>
                      ))}
                      {remainingAdmins > 0 && (
                        <Badge variant="outline" className="text-muted-foreground">
                          외 {remainingAdmins.toLocaleString()}명
                        </Badge>
                      )}
                    </>
                  )}
              </div>
            </div>
              {isAdmin && <p className="text-xs text-muted-foreground">팀 관리 권한이 활성화되어 있습니다.</p>}
            </CardContent>
          </Card>
        </div>

        <div className="space-y-6">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle className="text-base">내 키 요약</CardTitle>
              <Button variant="ghost" size="sm" className="h-8 px-2" onClick={onMoveToKeys}>
                전체 보기
                <ArrowRight className="size-4" />
              </Button>
            </CardHeader>
            <CardContent className="space-y-3">
              {topKeys.length === 0 ? (
                <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
                  생성된 키가 없습니다.{" "}
                  <Link className="underline underline-offset-4" href={`/keys/new?team_id=${team.team_id}`}>
                    키 생성
                  </Link>
                </div>
              ) : (
                topKeys.map((key) => (
                    <div key={key.token} className="rounded-lg border p-3">
                      <p className="text-sm font-medium">{key.key_alias || "-"}</p>
                    </div>
                  ))
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle className="text-base">사용 가능한 모델</CardTitle>
              <Button variant="ghost" size="sm" className="h-8 px-2" onClick={onMoveToModels}>
                전체 보기
                <ChevronRight className="size-4" />
              </Button>
            </CardHeader>
            <CardContent className="space-y-2">
              {scopedModels.length === 0 ? (
                <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
                  이 팀에 배정된 모델이 없습니다.
                </div>
              ) : (
                scopedModels.map(({ modelName, model }) => (
                  <div key={modelName} className="flex items-start justify-between gap-3 rounded-lg border p-3">
                    <div className="min-w-0 space-y-1">
                      {model ? (
                        <button
                          type="button"
                          onClick={() => onSelectModel(model)}
                          className="cursor-pointer text-left text-sm font-medium hover:underline"
                        >
                          {model.catalog?.display_name || model.model_name}
                        </button>
                      ) : (
                        <p className="text-sm font-medium">{modelName}</p>
                      )}
                      <div className="flex items-center gap-2 text-xs text-muted-foreground">
                        <Globe className="size-3.5" />
                        {model?.litellm_info?.model_info?.litellm_provider || "-"}
                      </div>
                    </div>
                    {model?.catalog ? <StatusBadge status={model.catalog.status} /> : null}
                  </div>
                ))
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

function TeamSettingsTab({ teamId, defaultMemberBudget }: { teamId: string; defaultMemberBudget: number | null }) {
  const updateSettings = useUpdateTeamSettings();
  const [defaultBudget, setDefaultBudget] = useState(
    defaultMemberBudget != null ? String(defaultMemberBudget) : ""
  );

  const handleSave = () => {
    updateSettings.mutate(
      {
        teamId,
        body: {
          default_member_budget: defaultBudget ? Number(defaultBudget) : null,
        },
      },
      {
        onSuccess: () => toast.success("팀 설정이 저장되었습니다."),
        onError: (err) => toast.error(err instanceof Error ? err.message : "저장 실패"),
      },
    );
  };

  return (
    <div className="space-y-6 max-w-2xl">
      <h2 className="text-lg font-semibold">팀 설정</h2>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">멤버 기본 예산</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <label className="text-sm font-medium">기본 예산 ($)</label>
            <input
              type="number"
              step="0.01"
              value={defaultBudget}
              onChange={(e) => setDefaultBudget(e.target.value)}
              placeholder="미설정 시 예산 제한 없음"
              className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
            />
            <p className="text-xs text-muted-foreground">
              신규 멤버가 팀에 추가될 때 자동으로 할당되는 예산입니다
            </p>
          </div>
        </CardContent>
      </Card>

      <Button onClick={handleSave} disabled={updateSettings.isPending}>
        {updateSettings.isPending ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
        저장
      </Button>
    </div>
  );
}

function MembersTab({ teamId }: { teamId: string }) {
  const [page, setPage] = useState(1);
  const pageSize = 50;
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const changeRoleMutation = useChangeMemberRole();
  const changeBudgetMutation = useChangeMemberBudget();
  const removeMemberMutation = useRemoveTeamMember();
  const [roleChangeTarget, setRoleChangeTarget] = useState<{ userId: string; currentIsAdmin: boolean } | null>(null);
  const [budgetChangeTarget, setBudgetChangeTarget] = useState<{ userId: string; currentBudget: number | null } | null>(null);
  const [budgetAmount, setBudgetAmount] = useState("");

  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch(searchInput);
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [searchInput]);

  const { data, isLoading } = useTeamMembers(teamId, page, pageSize, search);

  const toggleExpand = (userId: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(userId)) next.delete(userId);
      else next.add(userId);
      return next;
    });
  };

  const totalPages = data ? Math.ceil(data.total / pageSize) : 0;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">팀 멤버</h2>
        <Input
          placeholder="사번 검색..."
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          className="w-48"
        />
      </div>

      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="h-12 animate-pulse rounded bg-muted" />
          ))}
        </div>
      ) : !data || data.members.length === 0 ? (
        <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed p-8">
          <Users className="size-8 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">
            {search ? "검색 결과가 없습니다." : "멤버가 없습니다."}
          </p>
        </div>
      ) : (
        <>
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-10" />
                  <TableHead>사번</TableHead>
                  <TableHead>역할</TableHead>
                  <TableHead className="hidden sm:table-cell">키 수</TableHead>
                  <TableHead>예산 사용</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.members.map((member: TeamMember) => {
                  const isExpanded = expanded.has(member.user_id);
                  const pct = budgetPercent(member.total_spend, member.total_max_budget);
                  return (
                    <Fragment key={member.user_id}>
                      <TableRow
                        className={member.key_count > 0 ? "cursor-pointer" : ""}
                        onClick={() => member.key_count > 0 && toggleExpand(member.user_id)}
                      >
                        <TableCell>
                          {member.key_count > 0 && (
                            <ChevronRight
                              className={`size-4 transition-transform ${isExpanded ? "rotate-90" : ""}`}
                            />
                          )}
                        </TableCell>
                        <TableCell className="font-medium">{member.user_id}</TableCell>
                        <TableCell>
                          <div className="flex items-center gap-2">
                            {member.is_admin ? (
                              <Badge variant="default" className="gap-1">
                                <Shield className="size-3" />
                                관리자
                              </Badge>
                            ) : (
                              <Badge variant="outline">멤버</Badge>
                            )}
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-6 px-2 text-xs text-muted-foreground"
                              disabled={changeRoleMutation.isPending}
                              onClick={(e) => {
                                e.stopPropagation();
                                setRoleChangeTarget({ userId: member.user_id, currentIsAdmin: member.is_admin });
                              }}
                            >
                              {member.is_admin ? "멤버로 변경" : "관리자로 변경"}
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-6 px-2 text-xs text-destructive hover:text-destructive"
                              disabled={removeMemberMutation.isPending}
                              onClick={(e) => {
                                e.stopPropagation();
                                if (!confirm(`${member.user_id} 멤버를 팀에서 삭제하시겠습니까?`)) return;
                                removeMemberMutation.mutate(
                                  { teamId, userId: member.user_id },
                                  {
                                    onSuccess: () => toast.success(`${member.user_id} 멤버가 삭제되었습니다.`),
                                    onError: (err) => toast.error(err instanceof Error ? err.message : "삭제 실패"),
                                  },
                                );
                              }}
                            >
                              <Trash2 className="size-3 mr-0.5" />
                              삭제
                            </Button>
                          </div>
                        </TableCell>
                        <TableCell className="hidden sm:table-cell">{member.key_count}개</TableCell>
                        <TableCell>
                          <div className="flex items-center gap-2">
                            <div className="space-y-1">
                              <span className="text-sm">{formatBudget(member.total_spend, member.total_max_budget)}</span>
                              <div className="h-1.5 w-24 rounded-full bg-muted">
                                <div
                                  className="h-full rounded-full bg-primary transition-all"
                                  style={{ width: `${member.total_max_budget === null ? 0 : pct}%` }}
                                />
                              </div>
                            </div>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-6 px-2 text-xs text-muted-foreground"
                              onClick={(e) => {
                                e.stopPropagation();
                                setBudgetChangeTarget({ userId: member.user_id, currentBudget: member.total_max_budget });
                                setBudgetAmount(member.total_max_budget != null ? String(member.total_max_budget) : "");
                              }}
                            >
                              변경
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                      {isExpanded && member.keys.length > 0 && (
                        <TableRow>
                          <TableCell colSpan={5} className="bg-muted/30 p-0">
                            <div className="space-y-2 px-8 py-3">
                              {member.keys.map((key) => {
                                const keyPct = budgetPercent(key.spend, key.max_budget);
                                return (
                                  <div
                                    key={key.token}
                                    className="flex items-center justify-between gap-4 rounded-lg border bg-background p-3"
                                  >
                                    <div className="min-w-0 space-y-0.5">
                                      <p className="text-sm font-medium">
                                        {key.key_alias || "-"}
                                      </p>
                                      <p className="font-mono text-xs text-muted-foreground">
                                        {maskKey(key.token)}
                                      </p>
                                    </div>
                                    <div className="w-48 space-y-1 text-right">
                                      <span className="text-xs">{formatBudget(key.spend, key.max_budget)}</span>
                                      <div className="h-1.5 w-full rounded-full bg-muted">
                                        <div
                                          className="h-full rounded-full bg-primary transition-all"
                                          style={{ width: `${key.max_budget === null ? 0 : keyPct}%` }}
                                        />
                                      </div>
                                      {(key.budget_duration || key.budget_reset_at) && (
                                        <p className="flex items-center justify-end gap-1 text-[11px] text-muted-foreground">
                                          <RefreshCw className="size-2.5" />
                                          {[
                                            key.budget_duration
                                              ? `${formatBudgetDuration(key.budget_duration)} 주기`
                                              : null,
                                            key.budget_reset_at
                                              ? `다음: ${formatResetDate(key.budget_reset_at)}`
                                              : null,
                                          ]
                                            .filter(Boolean)
                                            .join(" · ")}
                                        </p>
                                      )}
                                    </div>
                                  </div>
                                );
                              })}
                            </div>
                          </TableCell>
                        </TableRow>
                      )}
                    </Fragment>
                  );
                })}
              </TableBody>
            </Table>
          </div>

          <div className="flex items-center justify-between">
            <p className="text-sm text-muted-foreground">
              총 {data.total.toLocaleString()}명 중{" "}
              {((page - 1) * pageSize + 1).toLocaleString()}–
              {Math.min(page * pageSize, data.total).toLocaleString()}
            </p>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                <ArrowLeft className="size-4" />
                이전
              </Button>
              <span className="text-sm text-muted-foreground">
                {page} / {totalPages}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={page >= totalPages}
                onClick={() => setPage((p) => p + 1)}
              >
                다음
                <ArrowRight className="size-4" />
              </Button>
            </div>
          </div>
        </>
      )}

      {/* Role Change Confirmation Dialog */}
      <Dialog open={!!roleChangeTarget} onOpenChange={(open) => !open && setRoleChangeTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>역할 변경</DialogTitle>
            <DialogDescription>
              <span className="font-semibold text-foreground">{roleChangeTarget?.userId}</span>
              님의 역할을{" "}
              <span className="font-semibold text-foreground">
                {roleChangeTarget?.currentIsAdmin ? "멤버" : "관리자"}
              </span>
              로 변경하시겠습니까?
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRoleChangeTarget(null)}>
              취소
            </Button>
            <Button
              disabled={changeRoleMutation.isPending}
              onClick={() => {
                if (!roleChangeTarget) return;
                changeRoleMutation.mutate(
                  {
                    teamId,
                    userId: roleChangeTarget.userId,
                    role: roleChangeTarget.currentIsAdmin ? "member" : "admin",
                  },
                  {
                    onSuccess: () => {
                      toast.success("역할이 변경되었습니다.");
                      setRoleChangeTarget(null);
                    },
                    onError: (err) => {
                      toast.error(err instanceof Error ? err.message : "역할 변경 실패");
                    },
                  },
                );
              }}
            >
              {changeRoleMutation.isPending ? "변경 중..." : "확인"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Budget Change Dialog */}
      <Dialog open={!!budgetChangeTarget} onOpenChange={(open) => { if (!open) { setBudgetChangeTarget(null); setBudgetAmount(""); } }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>예산 변경</DialogTitle>
            <DialogDescription>
              <span className="font-semibold text-foreground">{budgetChangeTarget?.userId}</span>
              님의 예산을 변경합니다.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="rounded-md bg-muted p-3 text-sm">
              <div className="flex justify-between">
                <span className="text-muted-foreground">현재 예산</span>
                <span className="font-medium">
                  {budgetChangeTarget?.currentBudget === null ? "무제한" : `$${budgetChangeTarget?.currentBudget?.toFixed(2)}`}
                </span>
              </div>
              {budgetAmount && Number(budgetAmount) > 0 && (
                <div className="flex justify-between mt-1 pt-1 border-t border-border">
                  <span className="text-muted-foreground">변경 후 예산</span>
                  <span className="font-medium text-primary">${Number(budgetAmount).toFixed(2)}</span>
                </div>
              )}
            </div>
            <div>
              <label className="text-sm font-medium">변경 금액 ($)</label>
              <Input
                type="number"
                step="0.01"
                min="0"
                placeholder="예: 100"
                value={budgetAmount}
                onChange={(e) => setBudgetAmount(e.target.value)}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => { setBudgetChangeTarget(null); setBudgetAmount(""); }}>
              취소
            </Button>
            <Button
              disabled={!budgetAmount || Number(budgetAmount) <= 0 || changeBudgetMutation.isPending}
              onClick={() => {
                if (!budgetChangeTarget) return;
                changeBudgetMutation.mutate(
                  { teamId, userId: budgetChangeTarget.userId, maxBudget: Number(budgetAmount) },
                  {
                    onSuccess: () => {
                      toast.success("예산이 변경되었습니다.");
                      setBudgetChangeTarget(null);
                      setBudgetAmount("");
                    },
                    onError: (err) => toast.error(err instanceof Error ? err.message : "예산 변경 실패"),
                  },
                );
              }}
            >
              {changeBudgetMutation.isPending ? "변경 중..." : "확인"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

export default function TeamDetailPage({
  params,
}: {
  params: Promise<{ teamId: string }>;
}) {
  const { teamId } = use(params);
  const [activeTab, setActiveTab] = useState("overview");
  const [deletingKeyId, setDeletingKeyId] = useState<string | null>(null);
  const [copiedKeyId, setCopiedKeyId] = useState<string | null>(null);
  const [detailModel, setDetailModel] = useState<ModelWithCatalog | null>(null);
  const { data, isLoading, isError, error, refetch } = useTeamDetail(teamId);
  const { data: allModels } = useModels();
  const deleteKeyMutation = useDeleteKey();
  const revealKeyMutation = useRevealKey();
  const modelsByName = useMemo(
    () => new Map(allModels?.map((m) => [m.model_name, m]) ?? []),
    [allModels],
  );

  const handleDeleteKey = (keyHash: string) => {
    setDeletingKeyId(keyHash);
    deleteKeyMutation.mutate(keyHash, {
      onSettled: () => setDeletingKeyId(null),
    });
  };

  if (isLoading) {
    return (
      <div className="space-y-6">
        <div className="h-8 w-48 animate-pulse rounded bg-muted" />
        <div className="h-4 w-32 animate-pulse rounded bg-muted" />
        <div className="h-64 w-full animate-pulse rounded-xl bg-muted" />
      </div>
    );
  }

  if (isError) {
    return (
      <div className="space-y-6">
        <Button variant="ghost" size="sm" asChild>
          <Link href="/teams">
            <ArrowLeft className="size-4" />
            내 팀으로 돌아가기
          </Link>
        </Button>
        <div className="flex flex-col items-center gap-4 rounded-xl border border-destructive/20 bg-destructive/5 p-8">
          <AlertCircle className="size-10 text-destructive" />
          <p className="text-sm text-destructive">
            팀 정보를 불러오는 중 오류가 발생했습니다:{" "}
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

  if (!data) return null;

  const { team, my_keys, is_admin, my_membership } = data;
  const enrichedTeamModels = team.models
    .map((modelName) => ({
      modelName,
      model: modelsByName.get(modelName) ?? null,
    }))
    .filter(({ model }) => model?.catalog);

  return (
    <div className="space-y-6">
      <Button variant="ghost" size="sm" asChild>
        <Link href="/teams">
          <ArrowLeft className="size-4" />
          내 팀
        </Link>
      </Button>

      <div className="flex items-center gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-2xl font-bold">{team.team_alias}</h1>
            {is_admin && (
              <Badge variant="default" className="gap-1">
                <Shield className="size-3" />
                관리자
              </Badge>
            )}
          </div>
        </div>
      </div>

      <Separator />

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="overview">개요</TabsTrigger>
          <TabsTrigger value="keys">내 키</TabsTrigger>
          <TabsTrigger value="models">모델</TabsTrigger>
          {is_admin && <TabsTrigger value="members">멤버</TabsTrigger>}
          {is_admin && <TabsTrigger value="settings">설정</TabsTrigger>}
        </TabsList>

        <TabsContent value="overview" className="mt-6">
          <OverviewTab
            team={team}
            isAdmin={is_admin}
            myKeys={my_keys}
            myMembership={my_membership}
            modelsByName={modelsByName}
            onMoveToKeys={() => setActiveTab("keys")}
            onMoveToModels={() => setActiveTab("models")}
            onSelectModel={setDetailModel}
          />
        </TabsContent>

        <TabsContent value="keys" className="mt-6 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold">내 API 키</h2>
            <Button asChild size="sm">
              <Link href={`/keys/new?team_id=${teamId}`}>
                <Plus className="size-4" />
                키 생성
              </Link>
            </Button>
          </div>

          {my_keys.length === 0 ? (
            <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed p-8">
              <p className="text-sm text-muted-foreground">생성된 키가 없습니다.</p>
              <Button asChild variant="outline" size="sm">
                <Link href={`/keys/new?team_id=${teamId}`}>
                  <Plus className="size-4" />
                  첫 키 생성하기
                </Link>
              </Button>
            </div>
          ) : (
            <div className="rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>별칭</TableHead>
                    <TableHead>키</TableHead>
                    <TableHead>생성일</TableHead>
                    <TableHead className="w-12" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {my_keys.map((key) => {
                    return (
                      <TableRow key={key.token}>
                        <TableCell className="font-medium">
                          {key.key_alias || "-"}
                        </TableCell>
                        <TableCell className="hidden md:table-cell">
                          <div className="flex items-center gap-1">
                            <span className="font-mono text-xs text-muted-foreground">{maskKey(key.token)}</span>
                            <Button
                              variant="ghost"
                              size="icon-xs"
                              title="키 복사"
                              disabled={revealKeyMutation.isPending}
                              onClick={() => {
                                revealKeyMutation.mutate(key.token, {
                                  onSuccess: async (res) => {
                                    try {
                                      await navigator.clipboard.writeText(res.key);
                                    } catch {
                                      const ta = document.createElement("textarea");
                                      ta.value = res.key;
                                      ta.style.position = "fixed";
                                      ta.style.opacity = "0";
                                      document.body.appendChild(ta);
                                      ta.select();
                                      document.execCommand("copy");
                                      document.body.removeChild(ta);
                                    }
                                    setCopiedKeyId(key.token);
                                    toast.success("키가 클립보드에 복사되었습니다.");
                                    setTimeout(() => setCopiedKeyId(null), 2000);
                                  },
                                  onError: (err) => toast.error(err instanceof Error ? err.message : "키 복사 실패"),
                                });
                              }}
                            >
                              {copiedKeyId === key.token ? <Check className="size-3.5 text-green-600" /> : <Copy className="size-3.5" />}
                            </Button>
                          </div>
                        </TableCell>
                        <TableCell className="hidden text-sm text-muted-foreground md:table-cell">
                          {key.expires ? formatDate(key.expires) : "-"}
                        </TableCell>
                        <TableCell>
                          {key.models.length > 0 ? (
                            <Badge variant="secondary" className="gap-1">
                              <Boxes className="size-3" />
                              {key.models.includes("all-proxy-models") ? "모든 모델" : `${key.models.length}개`}
                            </Badge>
                          ) : (
                            <span className="text-xs text-muted-foreground">전체</span>
                          )}
                        </TableCell>
                        <TableCell className="hidden text-sm text-muted-foreground md:table-cell">
                          {formatDate(key.created_at)}
                        </TableCell>
                        <TableCell>
                          <DeleteKeyDialog
                            keyItem={key}
                            onDelete={handleDeleteKey}
                            isDeleting={deletingKeyId === key.token}
                          />
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </TabsContent>

        <TabsContent value="models" className="mt-6 space-y-4">
          <h2 className="text-lg font-semibold">사용 가능한 모델</h2>
          {enrichedTeamModels.length === 0 ? (
            <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed p-8">
              <Boxes className="size-8 text-muted-foreground" />
              <p className="text-sm text-muted-foreground">이 팀에 배정된 모델이 없습니다.</p>
            </div>
          ) : (
            <div className="rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>모델명</TableHead>
                    <TableHead>상태</TableHead>
                    <TableHead className="hidden lg:table-cell">비용</TableHead>
                    <TableHead>기능</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {enrichedTeamModels.map(({ modelName, model }) => {
                    if (!model) return null;

                    const displayName = model.catalog?.display_name || model.model_name || modelName;
                    const provider = model.litellm_info?.model_info?.litellm_provider || "-";
                    const inputCost = model.litellm_info?.model_info?.input_cost_per_token ?? null;
                    const outputCost = model.litellm_info?.model_info?.output_cost_per_token ?? null;
                    const supportsVision = model.litellm_info?.model_info?.supports_vision;
                    const supportsFunctionCalling = model.litellm_info?.model_info?.supports_function_calling;

                    return (
                      <TableRow key={modelName}>
                        <TableCell>
                          <div className="space-y-1">
                            <button
                              type="button"
                              onClick={() => setDetailModel(model)}
                              className="cursor-pointer text-left font-medium hover:underline"
                            >
                              {displayName}
                            </button>
                            {displayName !== modelName && (
                              <p className="font-mono text-xs text-muted-foreground">{modelName}</p>
                            )}
                          </div>
                        </TableCell>
                        <TableCell>{model.catalog ? <StatusBadge status={model.catalog.status} /> : "-"}</TableCell>
                        <TableCell className="hidden text-xs text-muted-foreground lg:table-cell">
                          I: {formatTokenCost(inputCost)} / O: {formatTokenCost(outputCost)} per 1M tokens
                        </TableCell>
                        <TableCell>
                          {supportsVision || supportsFunctionCalling ? (
                            <div className="flex items-center gap-2">
                              {supportsVision ? <Eye className="size-4 text-muted-foreground" /> : null}
                              {supportsFunctionCalling ? <Zap className="size-4 text-muted-foreground" /> : null}
                            </div>
                          ) : (
                            "-"
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </TabsContent>

        {is_admin && (
          <TabsContent value="members" className="mt-6">
            <MembersTab teamId={teamId} />
          </TabsContent>
        )}

        {is_admin && (
          <TabsContent value="settings" className="mt-6">
            <TeamSettingsTab teamId={teamId} defaultMemberBudget={data.default_member_budget ?? null} />
          </TabsContent>
        )}
      </Tabs>

      <ModelDetailSheet
        model={detailModel}
        open={!!detailModel}
        onOpenChange={(o) => {
          if (!o) setDetailModel(null);
        }}
      />
    </div>
  );
}
