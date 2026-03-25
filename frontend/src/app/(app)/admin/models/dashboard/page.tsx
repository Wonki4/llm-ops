"use client";

import { useState, useMemo } from "react";
import Link from "next/link";
import {
  Search,
  Loader2,
  Boxes,
  BookOpen,
  Server,
  AlertTriangle,
  Activity,
  ArrowRight,
  ChevronLeft,
  ChevronRight,
  X,
  ExternalLink,
} from "lucide-react";

import { useModels, useAllModelStatusHistory } from "@/hooks/use-api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Separator } from "@/components/ui/separator";
import { ModelDetailSheet } from "@/components/model-detail-sheet";
import type { ModelStatus, ModelWithCatalog, ModelStatusHistory, ModelCatalog } from "@/types";

// ─── Constants ────────────────────────────────────────────────

const PAGE_SIZE = 50;

const STATUS_OPTIONS: { value: ModelStatus; label: string }[] = [
  { value: "testing", label: "Testing" },
  { value: "prerelease", label: "Prerelease" },
  { value: "lts", label: "LTS" },
  { value: "deprecating", label: "Deprecating" },
  { value: "deprecated", label: "Deprecated" },
];

const STATUS_STYLES: Record<ModelStatus, string> = {
  testing:
    "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
  prerelease:
    "bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400",
  lts: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400",
  deprecating:
    "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400",
  deprecated:
    "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",
};

const STATUS_BAR_COLORS: Record<ModelStatus | "unregistered", string> = {
  testing: "bg-blue-400",
  prerelease: "bg-purple-400",
  lts: "bg-green-500",
  deprecating: "bg-yellow-400",
  deprecated: "bg-red-400",
  unregistered: "bg-gray-300 dark:bg-gray-600",
};

const STATUS_DOT_COLORS: Record<ModelStatus | "unregistered", string> = {
  testing: "bg-blue-400",
  prerelease: "bg-purple-400",
  lts: "bg-green-500",
  deprecating: "bg-yellow-400",
  deprecated: "bg-red-400",
  unregistered: "bg-gray-300 dark:bg-gray-600",
};

const STATUS_ORDER: ModelStatus[] = [
  "testing",
  "prerelease",
  "lts",
  "deprecating",
  "deprecated",
];

const STATUS_INDEX: Record<ModelStatus, number> = {
  testing: 0,
  prerelease: 1,
  lts: 2,
  deprecating: 3,
  deprecated: 4,
};

// ─── Helpers ──────────────────────────────────────────────────

function StatusBadge({ status }: { status: ModelStatus }) {
  return <Badge className={STATUS_STYLES[status]}>{status}</Badge>;
}

function formatDate(dateStr: string | null | undefined): string {
  if (!dateStr) return "-";
  return new Date(dateStr).toLocaleDateString("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
}

function formatCost(cost: number | null | undefined): string {
  if (cost == null) return "-";
  if (cost === 0) return "$ 0";
  return `$ ${(cost * 1_000_000).toFixed(2)} / 1M`;
}

function formatRelativeTime(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return "방금 전";
  if (minutes < 60) return `${minutes}분 전`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}시간 전`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}일 전`;
  return formatDate(dateStr);
}

function getProvider(model: ModelWithCatalog): string {
  return model.litellm_info?.model_info?.litellm_provider ?? "-";
}

function getNextTransitionDate(catalog: ModelCatalog | null): string | null {
  if (!catalog?.status_schedule) return null;

  const today = new Date();
  today.setHours(0, 0, 0, 0);

  const currentStatusIndex = STATUS_INDEX[catalog.status];
  let nextDate: string | null = null;
  let nextTimestamp = Number.POSITIVE_INFINITY;

  for (const { value } of STATUS_OPTIONS) {
    if (STATUS_INDEX[value] <= currentStatusIndex) continue;

    const dateStr = catalog.status_schedule[value];
    if (!dateStr) continue;

    const parsed = new Date(`${dateStr}T00:00:00`);
    const timestamp = parsed.getTime();

    if (Number.isNaN(timestamp) || parsed <= today) continue;
    if (timestamp < nextTimestamp) {
      nextTimestamp = timestamp;
      nextDate = dateStr;
    }
  }

  return nextDate;
}

// ─── Stat Card ────────────────────────────────────────────────

function StatCard({
  title,
  value,
  icon: Icon,
  description,
}: {
  title: string;
  value: number | string;
  icon: typeof Activity;
  description?: string;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">
          {title}
        </CardTitle>
        <Icon className="size-4 text-muted-foreground" />
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold">{value}</div>
        {description && (
          <p className="text-xs text-muted-foreground mt-1">{description}</p>
        )}
      </CardContent>
    </Card>
  );
}

// ─── Source Badge ──────────────────────────────────────────────

function SourceBadge({ model }: { model: ModelWithCatalog }) {
  const hasLiteLLM = !!model.litellm_info;
  const hasCatalog = !!model.catalog;

  if (hasLiteLLM && hasCatalog) {
    return (
      <div className="flex gap-1">
        <Badge
          variant="outline"
          className="text-[10px] px-1.5 py-0 gap-1 border-green-300 text-green-700 dark:border-green-700 dark:text-green-400"
        >
          <Server className="size-2.5" />
          배포
        </Badge>
        <Badge
          variant="outline"
          className="text-[10px] px-1.5 py-0 gap-1 border-blue-300 text-blue-700 dark:border-blue-700 dark:text-blue-400"
        >
          <BookOpen className="size-2.5" />
          카탈로그
        </Badge>
      </div>
    );
  }
  if (hasLiteLLM) {
    return (
      <Badge
        variant="outline"
        className="text-[10px] px-1.5 py-0 gap-1 border-green-300 text-green-700 dark:border-green-700 dark:text-green-400"
      >
        <Server className="size-2.5" />
        배포만
      </Badge>
    );
  }
  return (
    <Badge
      variant="outline"
      className="text-[10px] px-1.5 py-0 gap-1 border-orange-300 text-orange-700 dark:border-orange-700 dark:text-orange-400"
    >
      <BookOpen className="size-2.5" />
      카탈로그만
    </Badge>
  );
}

// ─── Status Distribution Bar ──────────────────────────────────

const DISTRIBUTION_ORDER: (ModelStatus | "unregistered")[] = [
  ...STATUS_ORDER,
  "unregistered",
];

const DISTRIBUTION_LABELS: Record<ModelStatus | "unregistered", string> = {
  testing: "testing",
  prerelease: "prerelease",
  lts: "lts",
  deprecating: "deprecating",
  deprecated: "deprecated",
  unregistered: "미등록",
};

function StatusDistributionBar({
  counts,
  total,
}: {
  counts: Record<ModelStatus | "unregistered", number>;
  total: number;
}) {
  if (total === 0) {
    return (
      <div className="space-y-3">
        <div className="h-4 w-full rounded-full bg-muted" />
        <p className="text-sm text-muted-foreground text-center">
          등록된 모델이 없습니다
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {/* Stacked bar */}
      <div className="flex h-4 w-full overflow-hidden rounded-full border">
        {DISTRIBUTION_ORDER.map((status) => {
          const count = counts[status];
          if (count === 0) return null;
          const pct = (count / total) * 100;
          return (
            <TooltipProvider key={status}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <div
                    className={`${STATUS_BAR_COLORS[status]} transition-all`}
                    style={{ width: `${pct}%`, minWidth: count > 0 ? "8px" : "0" }}
                    aria-label={`${DISTRIBUTION_LABELS[status]}: ${count}개 (${pct.toFixed(0)}%)`}
                  />
                </TooltipTrigger>
                <TooltipContent>
                  <p className="text-xs">
                    {DISTRIBUTION_LABELS[status]}: {count}개 ({pct.toFixed(1)}%)
                  </p>
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          );
        })}
      </div>

      {/* Legend */}
      <div className="flex flex-wrap gap-x-5 gap-y-1.5">
        {DISTRIBUTION_ORDER.map((status) => (
          <div key={status} className="flex items-center gap-1.5">
            <div
              className={`size-2.5 rounded-full ${STATUS_DOT_COLORS[status]}`}
            />
            <span className="text-xs text-muted-foreground">
              {DISTRIBUTION_LABELS[status]}
            </span>
            <span className="text-xs font-medium">{counts[status]}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Recent Changes ───────────────────────────────────────────

function RecentChanges({
  history,
  isLoading,
  onModelClick,
}: {
  history: ModelStatusHistory[];
  isLoading: boolean;
  onModelClick: (modelName: string) => void;
}) {
  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-8">
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (history.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-8 text-center">
        <Activity className="size-8 text-muted-foreground/40 mb-2" />
        <p className="text-sm text-muted-foreground">최근 변경이 없습니다</p>
      </div>
    );
  }

  return (
    <div className="space-y-1">
      {history.map((h) => (
        <div
          key={h.id}
          className="flex items-center gap-3 rounded-md px-3 py-2.5 hover:bg-muted/50 transition-colors"
        >
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <button
                type="button"
                onClick={() => onModelClick(h.model_name)}
                className="font-mono text-sm truncate max-w-[180px] hover:underline cursor-pointer text-left"
              >
                {h.model_name}
              </button>
              <div className="flex items-center gap-1">
                {h.previous_status ? (
                  <>
                    <StatusBadge status={h.previous_status} />
                    <ArrowRight className="size-3 text-muted-foreground shrink-0" />
                    <StatusBadge status={h.new_status} />
                  </>
                ) : (
                  <>
                    <span className="text-xs text-muted-foreground">생성</span>
                    <ArrowRight className="size-3 text-muted-foreground shrink-0" />
                    <StatusBadge status={h.new_status} />
                  </>
                )}
              </div>
            </div>
            <div className="mt-0.5 flex items-center gap-2 text-xs text-muted-foreground">
              <span>{h.changed_by}</span>
              <span>·</span>
              <span>{formatRelativeTime(h.changed_at)}</span>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

// ─── Main Component ───────────────────────────────────────────

export default function ModelDashboardPage() {
  // Data fetching
  const { data: models, isLoading, isError } = useModels();
  const { data: recentData, isLoading: recentLoading } =
    useAllModelStatusHistory({ limit: 5, offset: 0 });
  const [detailModel, setDetailModel] = useState<ModelWithCatalog | null>(null);

  // Filter state for model table
  const [nameInput, setNameInput] = useState("");
  const [nameFilter, setNameFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [page, setPage] = useState(0);

  // ── Computed stats ──
  const stats = useMemo(() => {
    const all = (models ?? []).filter((m) => !m.catalog || m.catalog.visible !== false);
    const total = all.length;
    const withCatalog = all.filter((m) => m.catalog).length;
    const withLiteLLM = all.filter((m) => m.litellm_info).length;
    const now = Date.now();
    const retiring = all.filter(
      (m) =>
        m.catalog &&
        (m.catalog.status === "deprecating" ||
          m.catalog.status === "deprecated" ||
          (m.catalog.status_schedule?.deprecated &&
            new Date(m.catalog.status_schedule.deprecated).getTime() > now)),
    ).length;

    return { total, withCatalog, withLiteLLM, retiring };
  }, [models]);

  // ── Status distribution (including unregistered) ──
  const statusCounts = useMemo(() => {
    const counts: Record<ModelStatus | "unregistered", number> = {
      testing: 0,
      prerelease: 0,
      lts: 0,
      deprecating: 0,
      deprecated: 0,
      unregistered: 0,
    };
    let total = 0;
    for (const m of models ?? []) {
      if (m.catalog) {
        counts[m.catalog.status]++;
      } else {
        counts.unregistered++;
      }
      total++;
    }
    return { counts, total };
  }, [models]);

  // ── Filtered models for table ──
  const filteredModels = useMemo(() => {
    let result = (models ?? []).filter((m) => !m.catalog || m.catalog.visible !== false);

    if (nameFilter) {
      const q = nameFilter.toLowerCase();
      result = result.filter(
        (m) =>
          m.model_name.toLowerCase().includes(q) ||
          m.catalog?.display_name?.toLowerCase().includes(q),
      );
    }

    if (statusFilter) {
      result = result.filter(
        (m) => m.catalog && m.catalog.status === statusFilter,
      );
    }

    return result;
  }, [models, nameFilter, statusFilter]);

  // ── Pagination ──
  const totalFiltered = filteredModels.length;
  const totalPages = Math.ceil(totalFiltered / PAGE_SIZE);
  const pageModels = filteredModels.slice(
    page * PAGE_SIZE,
    (page + 1) * PAGE_SIZE,
  );
  const startItem = totalFiltered > 0 ? page * PAGE_SIZE + 1 : 0;
  const endItem = Math.min((page + 1) * PAGE_SIZE, totalFiltered);

  // ── Filter handlers ──
  function applyFilters() {
    setNameFilter(nameInput.trim());
    setPage(0);
  }

  function resetFilters() {
    setNameInput("");
    setNameFilter("");
    setStatusFilter("");
    setPage(0);
  }

  const hasActiveFilters = !!(nameFilter || statusFilter);

  // ── Recent history ──
  const recentHistory = recentData?.history ?? [];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold">모델 대시보드</h1>
        <p className="text-muted-foreground mt-1">
          전체 모델 현황을 한눈에 확인합니다
        </p>
      </div>

      {/* ── Stat Cards ── */}
      {isLoading ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Card key={i}>
              <CardContent className="pt-6">
                <div className="h-8 w-16 animate-pulse rounded bg-muted" />
              </CardContent>
            </Card>
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            title="전체 모델"
            value={stats.total}
            icon={Boxes}
            description="LiteLLM + 카탈로그 합산"
          />
          <StatCard
            title="카탈로그 등록"
            value={stats.withCatalog}
            icon={BookOpen}
            description="상태 관리 중인 모델"
          />
          <StatCard
            title="LiteLLM 활성"
            value={stats.withLiteLLM}
            icon={Server}
            description="배포된 모델"
          />
          <StatCard
            title="폐기 예정"
            value={stats.retiring}
            icon={AlertTriangle}
            description="deprecating / deprecated"
          />
        </div>
      )}

      {/* ── Status Distribution + Recent Changes (side by side) ── */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* Status Distribution */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">상태 분포</CardTitle>
            <CardDescription>
              전체 모델의 상태별 비율 (미등록 포함)
            </CardDescription>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <div className="h-4 w-full animate-pulse rounded-full bg-muted" />
            ) : (
              <StatusDistributionBar
                counts={statusCounts.counts}
                total={statusCounts.total}
              />
            )}
          </CardContent>
        </Card>

        {/* Recent Changes */}
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <div>
                <CardTitle className="text-base">최근 변경</CardTitle>
                <CardDescription>
                  최근 모델 상태 변경 이력
                </CardDescription>
              </div>
              <Link href="/admin/models/history">
                <Button variant="ghost" size="sm" className="text-xs gap-1">
                  더 보기
                  <ExternalLink className="size-3" />
                </Button>
              </Link>
            </div>
          </CardHeader>
          <CardContent>
            <RecentChanges
              history={recentHistory}
              isLoading={recentLoading}
              onModelClick={(modelName) => {
                const fullModel = models?.find((m) => m.model_name === modelName);
                if (fullModel) setDetailModel(fullModel);
              }}
            />
          </CardContent>
        </Card>
      </div>

      <Separator />

      {/* ── All Models Table Section ── */}
      <div>
        <h2 className="text-lg font-semibold mb-4">전체 모델 목록</h2>

        {/* Filters */}
        <div className="flex flex-wrap items-end gap-3 mb-4">
          <div className="flex-1 min-w-[200px] max-w-xs">
            <label className="text-xs font-medium text-muted-foreground mb-1.5 block">
              모델명
            </label>
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" />
              <Input
                placeholder="모델명 검색..."
                value={nameInput}
                onChange={(e) => setNameInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && applyFilters()}
                className="pl-8 h-9"
              />
            </div>
          </div>

          <div className="w-[160px]">
            <label className="text-xs font-medium text-muted-foreground mb-1.5 block">
              상태
            </label>
            <Select
              value={statusFilter}
              onValueChange={(v) => {
                setStatusFilter(v === "__all__" ? "" : v);
                setPage(0);
              }}
            >
              <SelectTrigger className="h-9">
                <SelectValue placeholder="전체" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__all__">전체</SelectItem>
                {STATUS_OPTIONS.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <Button size="sm" onClick={applyFilters} className="h-9">
            <Search className="size-3.5 mr-1" />
            검색
          </Button>

          {hasActiveFilters && (
            <Button
              size="sm"
              variant="ghost"
              onClick={resetFilters}
              className="h-9 text-muted-foreground"
            >
              <X className="size-3.5 mr-1" />
              초기화
            </Button>
          )}
        </div>

        {/* Table */}
        {isLoading ? (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="size-6 animate-spin text-muted-foreground" />
          </div>
        ) : isError ? (
          <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
            모델 데이터를 불러오는 중 오류가 발생했습니다.
          </div>
        ) : totalFiltered === 0 ? (
          <div className="flex flex-col items-center justify-center rounded-lg border border-dashed py-16 text-center">
            <Boxes className="size-10 text-muted-foreground mb-3" />
            <p className="text-muted-foreground">
              {hasActiveFilters
                ? "필터 조건에 맞는 모델이 없습니다."
                : "등록된 모델이 없습니다."}
            </p>
          </div>
        ) : (
          <>
            <div className="rounded-lg border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="min-w-[200px]">모델명</TableHead>
                    <TableHead className="w-[100px]">상태</TableHead>
                    <TableHead>Input 비용</TableHead>
                    <TableHead>Output 비용</TableHead>
                    <TableHead className="w-[110px]">다음 전환</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {pageModels.map((m) => (
                    <TableRow key={m.model_name}>
                      <TableCell>
                        <button
                          type="button"
                          onClick={() => setDetailModel(m)}
                          className="text-left hover:underline cursor-pointer"
                        >
                          <div className="space-y-0.5">
                            <div className="font-medium text-sm truncate max-w-[280px]">
                              {m.catalog?.display_name ?? m.model_name}
                            </div>
                            {m.catalog?.display_name &&
                              m.catalog.display_name !== m.model_name && (
                                <div className="text-[11px] font-mono text-muted-foreground truncate max-w-[280px]">
                                  {m.model_name}
                                </div>
                              )}
                          </div>
                        </button>
                      </TableCell>
                      <TableCell>
                        {m.catalog ? (
                          <StatusBadge status={m.catalog.status} />
                        ) : (
                          <span className="text-xs text-muted-foreground">-</span>
                        )}
                      </TableCell>
                      <TableCell className="text-xs font-mono text-muted-foreground">
                        {formatCost(m.litellm_info?.model_info?.input_cost_per_token)}
                      </TableCell>
                      <TableCell className="text-xs font-mono text-muted-foreground">
                        {formatCost(m.litellm_info?.model_info?.output_cost_per_token)}
                      </TableCell>
                      <TableCell>
                        <span className="text-sm">
                          {formatDate(getNextTransitionDate(m.catalog))}
                        </span>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>

            {/* Pagination */}
            <div className="flex items-center justify-between mt-4">
              <p className="text-sm text-muted-foreground">
                총 {totalFiltered}개 중 {startItem}–{endItem}
              </p>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page === 0}
                  onClick={() => setPage((p) => p - 1)}
                >
                  <ChevronLeft className="size-4" />
                  이전
                </Button>
                <span className="text-sm text-muted-foreground">
                  {page + 1} / {totalPages || 1}
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page >= totalPages - 1}
                  onClick={() => setPage((p) => p + 1)}
                >
                  다음
                  <ChevronRight className="size-4" />
                </Button>
              </div>
            </div>
          </>
        )}
      </div>

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
