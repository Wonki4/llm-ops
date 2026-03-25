"use client";

import { useState } from "react";
import { Plus, Pencil, Trash2, Package, Server, BookOpen, History, ArrowRight, Loader2 } from "lucide-react";
import { toast } from "sonner";

import {
  useModels,
  useCreateCatalogEntry,
  useUpdateCatalogEntry,
  useDeleteCatalogEntry,
  useModelStatusHistory,
} from "@/hooks/use-api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { ModelDetailSheet } from "@/components/model-detail-sheet";
import type { ModelCatalog, ModelStatus, ModelWithCatalog } from "@/types";

// ─── Constants ────────────────────────────────────────────────

const STATUS_OPTIONS: { value: ModelStatus; label: string }[] = [
  { value: "testing", label: "Testing" },
  { value: "prerelease", label: "Prerelease" },
  { value: "lts", label: "LTS" },
  { value: "deprecating", label: "Deprecating" },
  { value: "deprecated", label: "Deprecated" },
];

const STATUS_INDEX: Record<ModelStatus, number> = {
  testing: 0,
  prerelease: 1,
  lts: 2,
  deprecating: 3,
  deprecated: 4,
};

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

// ─── Helpers ──────────────────────────────────────────────────

function formatCost(cost: number | null | undefined): string {
  if (cost == null) return "-";
  if (cost === 0) return "$ 0";
  return `$ ${(cost * 1_000_000).toFixed(2)} / 1M`;
}

function formatDate(dateStr: string | null | undefined): string {
  if (!dateStr) return "-";
  return new Date(dateStr).toLocaleDateString("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
}

function formatDateTime(dateStr: string | null | undefined): string {
  if (!dateStr) return "-";
  return new Date(dateStr).toLocaleDateString("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function StatusBadge({ status }: { status: ModelStatus }) {
  return <Badge className={STATUS_STYLES[status]}>{status}</Badge>;
}

/** Badge showing whether a model is LiteLLM-deployed, catalog-only, or both */
function SourceBadge({ model }: { model: ModelWithCatalog }) {
  const hasLiteLLM = !!model.litellm_info;
  const hasCatalog = !!model.catalog;

  if (hasLiteLLM && hasCatalog) {
    return (
      <div className="flex gap-1">
        <Badge variant="outline" className="text-[10px] px-1.5 py-0 gap-1 border-green-300 text-green-700 dark:border-green-700 dark:text-green-400">
          <Server className="size-2.5" />
          배포
        </Badge>
        <Badge variant="outline" className="text-[10px] px-1.5 py-0 gap-1 border-blue-300 text-blue-700 dark:border-blue-700 dark:text-blue-400">
          <BookOpen className="size-2.5" />
          카탈로그
        </Badge>
      </div>
    );
  }
  if (hasLiteLLM) {
    return (
      <Badge variant="outline" className="text-[10px] px-1.5 py-0 gap-1 border-green-300 text-green-700 dark:border-green-700 dark:text-green-400">
        <Server className="size-2.5" />
        배포만
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="text-[10px] px-1.5 py-0 gap-1 border-orange-300 text-orange-700 dark:border-orange-700 dark:text-orange-400">
      <BookOpen className="size-2.5" />
      카탈로그만
    </Badge>
  );
}

function TableSkeleton() {
  return (
    <div className="space-y-3">
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} className="flex gap-4">
          <div className="h-8 flex-[2] animate-pulse rounded bg-muted" />
          <div className="h-8 flex-1 animate-pulse rounded bg-muted" />
          <div className="h-8 flex-1 animate-pulse rounded bg-muted" />
          <div className="h-8 flex-1 animate-pulse rounded bg-muted" />
          <div className="h-8 flex-1 animate-pulse rounded bg-muted" />
          <div className="h-8 flex-1 animate-pulse rounded bg-muted" />
          <div className="h-8 w-20 animate-pulse rounded bg-muted" />
        </div>
      ))}
    </div>
  );
}

// ─── Form State ───────────────────────────────────────────────

interface ModelFormState {
  model_name: string;
  display_name: string;
  description: string;
  status: ModelStatus;
  status_schedule: Record<string, string>;
}

const INITIAL_FORM: ModelFormState = {
  model_name: "",
  display_name: "",
  description: "",
  status: "testing",
  status_schedule: {},
};

function catalogToForm(catalog: ModelCatalog): ModelFormState {
  return {
    model_name: catalog.model_name,
    display_name: catalog.display_name,
    description: catalog.description ?? "",
    status: catalog.status,
    status_schedule: catalog.status_schedule ? { ...catalog.status_schedule } : {},
  };
}

// ─── Main Component ───────────────────────────────────────────

export default function ModelManagementPage() {
  const { data: models, isLoading, isError } = useModels();
  const createEntry = useCreateCatalogEntry();
  const updateEntry = useUpdateCatalogEntry();
  const deleteEntry = useDeleteCatalogEntry();

  // Form dialog state
  const [formOpen, setFormOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<ModelFormState>(INITIAL_FORM);

  // Delete dialog state
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deletingModel, setDeletingModel] = useState<ModelWithCatalog | null>(
    null,
  );

  // History dialog state
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyCatalogId, setHistoryCatalogId] = useState<string | null>(null);
  const [detailModel, setDetailModel] = useState<ModelWithCatalog | null>(null);
  const { data: historyData, isLoading: historyLoading } = useModelStatusHistory(
    historyOpen ? (historyCatalogId ?? undefined) : undefined,
  );

  function openHistoryDialog(catalogId: string) {
    setHistoryCatalogId(catalogId);
    setHistoryOpen(true);
  }

  // ─── Form handlers ───────────────────────────────────────────

  function openCreateDialog(prefillModelName?: string) {
    setEditingId(null);
    setForm({
      ...INITIAL_FORM,
      model_name: prefillModelName ?? "",
      display_name: prefillModelName ?? "",
    });
    setFormOpen(true);
  }

  function openEditDialog(catalog: ModelCatalog) {
    setEditingId(catalog.id);
    setForm(catalogToForm(catalog));
    setFormOpen(true);
  }

  function openDeleteDialog(model: ModelWithCatalog) {
    setDeletingModel(model);
    setDeleteOpen(true);
  }

  function handleFormChange(field: keyof ModelFormState, value: string) {
    setForm((prev) => ({ ...prev, [field]: value }));
  }

  function handleStatusScheduleChange(status: ModelStatus, value: string) {
    setForm((prev) => ({
      ...prev,
      status_schedule: {
        ...prev.status_schedule,
        [status]: value,
      },
    }));
  }

  function buildStatusSchedule(schedule: Record<string, string>) {
    const cleaned: Partial<Record<ModelStatus, string>> = {};

    for (const { value } of STATUS_OPTIONS) {
      const date = schedule[value]?.trim();
      if (date) {
        cleaned[value] = date;
      }
    }

    return Object.keys(cleaned).length > 0 ? cleaned : undefined;
  }

  function handleFormSubmit() {
    if (!form.model_name.trim() || !form.display_name.trim()) {
      toast.error("모델명과 표시 이름은 필수입니다.");
      return;
    }

    if (editingId) {
      // Update
      const statusSchedule = buildStatusSchedule(form.status_schedule);
      updateEntry.mutate(
        {
          catalogId: editingId,
          body: {
            display_name: form.display_name.trim(),
            description: form.description.trim() || undefined,
            status: form.status,
            status_schedule: statusSchedule,
          },
        },
        {
          onSuccess: () => {
            toast.success("모델이 수정되었습니다");
            setFormOpen(false);
          },
          onError: (error) => {
            toast.error(
              error instanceof Error
                ? error.message
                : "수정 중 오류가 발생했습니다",
            );
          },
        },
      );
    } else {
      // Create
      const statusSchedule = buildStatusSchedule(form.status_schedule);
      createEntry.mutate(
        {
          model_name: form.model_name.trim(),
          display_name: form.display_name.trim(),
          description: form.description.trim() || undefined,
          status: form.status,
          status_schedule: statusSchedule,
        },
        {
          onSuccess: () => {
            toast.success("모델이 추가되었습니다");
            setFormOpen(false);
          },
          onError: (error) => {
            toast.error(
              error instanceof Error
                ? error.message
                : "추가 중 오류가 발생했습니다",
            );
          },
        },
      );
    }
  }

  function handleDelete() {
    if (!deletingModel?.catalog) return;

    deleteEntry.mutate(deletingModel.catalog.id, {
      onSuccess: () => {
        toast.success("카탈로그 항목이 삭제되었습니다");
        setDeleteOpen(false);
        setDeletingModel(null);
      },
      onError: (error) => {
        toast.error(
          error instanceof Error
            ? error.message
            : "삭제 중 오류가 발생했습니다",
        );
      },
    });
  }

  const isSubmitting = createEntry.isPending || updateEntry.isPending;

  // ─── Derived data ──────────────────────────────────────────────

  function getProvider(model: ModelWithCatalog): string {
    return (model.litellm_info?.model_info as Record<string, unknown>)?.litellm_provider as string ?? "-";
  }

  function getActualModel(model: ModelWithCatalog): string {
    return (model.litellm_info?.litellm_params as Record<string, unknown>)?.model as string ?? "-";
  }

  function getDisplayName(model: ModelWithCatalog): string {
    return model.catalog?.display_name ?? model.model_name;
  }

  function getInputCost(model: ModelWithCatalog): number | null | undefined {
    return (model.litellm_info?.model_info as Record<string, unknown>)?.input_cost_per_token as number | null | undefined;
  }

  function getOutputCost(model: ModelWithCatalog): number | null | undefined {
    return (model.litellm_info?.model_info as Record<string, unknown>)?.output_cost_per_token as number | null | undefined;
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

  return (
    <TooltipProvider>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold">모델 관리</h1>
            <p className="text-muted-foreground mt-1">
              LiteLLM 배포 모델과 커스텀 카탈로그를 통합 관리합니다
            </p>
          </div>
          <Button onClick={() => openCreateDialog()}>
            <Plus className="size-4" />
            카탈로그 등록
          </Button>
        </div>

        {/* Error state */}
        {isError && (
          <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
            모델 목록을 불러오는 중 오류가 발생했습니다.
          </div>
        )}

        {/* Table */}
        {isLoading ? (
          <TableSkeleton />
        ) : models && models.length > 0 ? (
          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>모델</TableHead>
                  <TableHead>소스</TableHead>
                  <TableHead>Provider</TableHead>
                  <TableHead>상태</TableHead>
                  <TableHead>노출</TableHead>
                  <TableHead>Input Cost</TableHead>
                  <TableHead>Output Cost</TableHead>
                  <TableHead>다음 전환</TableHead>
                  <TableHead>작업</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {models.map((model) => (
                  <TableRow key={model.model_name}>
                    {/* Model name column */}
                    <TableCell>
                      <button
                        type="button"
                        onClick={() => setDetailModel(model)}
                        className="text-left hover:underline cursor-pointer"
                      >
                        <div className="min-w-0">
                          <span className="font-medium">
                            {getDisplayName(model)}
                          </span>
                          {getDisplayName(model) !== model.model_name && (
                            <p className="text-xs text-muted-foreground font-mono truncate">
                              {model.model_name}
                            </p>
                          )}
                          {model.litellm_info && getActualModel(model) !== model.model_name && (
                            <p className="text-[10px] text-muted-foreground/60 font-mono truncate">
                              → {getActualModel(model)}
                            </p>
                          )}
                        </div>
                      </button>
                    </TableCell>

                    {/* Source badge */}
                    <TableCell>
                      <SourceBadge model={model} />
                    </TableCell>

                    {/* Provider */}
                    <TableCell className="text-sm">
                      {getProvider(model) !== "-" ? (
                        <Badge variant="secondary" className="text-xs font-mono">
                          {getProvider(model)}
                        </Badge>
                      ) : (
                        <span className="text-muted-foreground">-</span>
                      )}
                    </TableCell>

                    {/* Status */}
                    <TableCell>
                      {model.catalog ? (
                        <StatusBadge status={model.catalog.status} />
                      ) : (
                        <span className="text-muted-foreground text-sm">-</span>
                      )}
                    </TableCell>

                    {/* Visible toggle */}
                    <TableCell>
                      {model.catalog ? (
                        <Button
                          variant="ghost"
                          size="sm"
                          className={`h-6 px-2 text-xs ${model.catalog.visible !== false ? "text-green-600" : "text-muted-foreground"}`}
                          onClick={() => {
                            updateEntry.mutate({
                              catalogId: model.catalog!.id,
                              body: { visible: model.catalog!.visible === false },
                            });
                          }}
                        >
                          {model.catalog.visible !== false ? "ON" : "OFF"}
                        </Button>
                      ) : (
                        <span className="text-muted-foreground text-sm">-</span>
                      )}
                    </TableCell>

                    {/* Costs */}
                    <TableCell className="text-sm">
                      {formatCost(getInputCost(model))}
                    </TableCell>
                    <TableCell className="text-sm">
                      {formatCost(getOutputCost(model))}
                    </TableCell>

                    <TableCell className="text-sm">
                      {formatDate(getNextTransitionDate(model.catalog))}
                    </TableCell>

                    {/* Actions */}
                    <TableCell>
                      <div className="flex gap-1">
                        {model.catalog ? (
                          <>
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <Button
                                  variant="ghost"
                                  size="icon-xs"
                                  onClick={() => openEditDialog(model.catalog!)}
                                >
                                  <Pencil className="size-3.5" />
                                </Button>
                              </TooltipTrigger>
                              <TooltipContent>카탈로그 수정</TooltipContent>
                            </Tooltip>
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <Button
                                  variant="ghost"
                                  size="icon-xs"
                                  onClick={() => openDeleteDialog(model)}
                                  className="text-destructive hover:text-destructive"
                                >
                                  <Trash2 className="size-3.5" />
                                </Button>
                              </TooltipTrigger>
                              <TooltipContent>카탈로그 삭제</TooltipContent>
                            </Tooltip>
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <Button
                                  variant="ghost"
                                  size="icon-xs"
                                  onClick={() => openHistoryDialog(model.catalog!.id)}
                                >
                                  <History className="size-3.5" />
                                </Button>
                              </TooltipTrigger>
                              <TooltipContent>상태 변경 이력</TooltipContent>
                            </Tooltip>
                          </>
                        ) : (
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Button
                                variant="outline"
                                size="sm"
                                className="text-xs h-7"
                                onClick={() => openCreateDialog(model.model_name)}
                              >
                                <BookOpen className="size-3 mr-1" />
                                카탈로그 등록
                              </Button>
                            </TooltipTrigger>
                            <TooltipContent>이 LiteLLM 모델의 카탈로그 항목을 생성합니다</TooltipContent>
                          </Tooltip>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center rounded-lg border border-dashed py-12 text-center">
            <Package className="size-10 text-muted-foreground mb-3" />
            <p className="text-muted-foreground">등록된 모델이 없습니다.</p>
          </div>
        )}

        {/* Create / Edit Dialog */}
        <Dialog open={formOpen} onOpenChange={setFormOpen}>
          <DialogContent className="sm:max-w-lg">
            <DialogHeader>
              <DialogTitle>
                {editingId ? "카탈로그 수정" : "카탈로그 등록"}
              </DialogTitle>
              <DialogDescription>
                {editingId
                  ? "모델 카탈로그 정보를 수정합니다."
                  : "새로운 모델을 카탈로그에 등록합니다."}
              </DialogDescription>
            </DialogHeader>

            <div className="grid gap-4 py-2">
              {/* model_name */}
              <div className="grid gap-2">
                <Label htmlFor="model-name">
                  모델명 <span className="text-destructive">*</span>
                </Label>
                {editingId ? (
                  <Input
                    id="model-name"
                    value={form.model_name}
                    disabled
                  />
                ) : (
                  <Input
                    id="model-name"
                    value={form.model_name}
                    onChange={(e) => {
                      const value = e.target.value;
                      handleFormChange("model_name", value);
                      if (!form.display_name || form.display_name === form.model_name) {
                        handleFormChange("display_name", value);
                      }
                    }}
                    placeholder="모델명을 입력하세요"
                    list="model-name-suggestions"
                  />
                )}
                {!editingId && (
                  <datalist id="model-name-suggestions">
                    {models
                      ?.filter((m) => m.litellm_info && !m.catalog)
                      .map((m) => (
                        <option key={m.model_name} value={m.model_name} />
                      ))}
                  </datalist>
                )}
                {!editingId && (
                  <p className="text-xs text-muted-foreground">
                    직접 입력하거나, LiteLLM 배포 모델 중 선택할 수 있습니다.
                  </p>
                )}
              </div>

              {/* display_name */}
              <div className="grid gap-2">
                <Label htmlFor="display-name">
                  표시 이름 <span className="text-destructive">*</span>
                </Label>
                <Input
                  id="display-name"
                  value={form.display_name}
                  onChange={(e) =>
                    handleFormChange("display_name", e.target.value)
                  }
                  placeholder="GPT-4o Mini"
                />
              </div>

              {/* description */}
              <div className="grid gap-2">
                <Label htmlFor="model-desc">설명</Label>
                <textarea
                  id="model-desc"
                  rows={2}
                  value={form.description}
                  onChange={(e) =>
                    handleFormChange("description", e.target.value)
                  }
                  placeholder="모델 설명을 입력하세요..."
                  className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
                />
              </div>

              {/* status */}
              <div className="grid gap-2">
                <Label>상태</Label>
                <Select
                  value={form.status}
                  onValueChange={(v) =>
                    handleFormChange("status", v)
                  }
                >
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {STATUS_OPTIONS.map((opt) => (
                      <SelectItem key={opt.value} value={opt.value}>
                        {opt.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="grid gap-3">
                <Label>상태별 일정</Label>
                <div className="grid gap-2">
                  {STATUS_OPTIONS.map((option) => (
                    <div key={option.value} className="grid grid-cols-[130px_1fr] items-center gap-3">
                      <Label htmlFor={`status-schedule-${option.value}`}>{option.label}</Label>
                      <Input
                        id={`status-schedule-${option.value}`}
                        type="date"
                        value={form.status_schedule[option.value] ?? ""}
                        onChange={(e) =>
                          handleStatusScheduleChange(option.value, e.target.value)
                        }
                      />
                    </div>
                  ))}
                </div>
              </div>
            </div>

            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => setFormOpen(false)}
                disabled={isSubmitting}
              >
                취소
              </Button>
              <Button onClick={handleFormSubmit} disabled={isSubmitting}>
                {isSubmitting
                  ? "저장 중..."
                  : editingId
                    ? "수정"
                    : "등록"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* Delete Confirmation Dialog */}
        <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>카탈로그 삭제</DialogTitle>
              <DialogDescription>
                <span className="font-semibold text-foreground">
                  {deletingModel?.catalog?.display_name ?? deletingModel?.model_name}
                </span>{" "}
                의 카탈로그 항목을 삭제하시겠습니까?
                {deletingModel?.litellm_info && (
                  <span className="block mt-1 text-xs">
                    LiteLLM에 배포된 모델은 유지되며, 카탈로그 메타데이터만 삭제됩니다.
                  </span>
                )}
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => setDeleteOpen(false)}
                disabled={deleteEntry.isPending}
              >
                취소
              </Button>
              <Button
                variant="destructive"
                onClick={handleDelete}
                disabled={deleteEntry.isPending}
              >
                {deleteEntry.isPending ? "삭제 중..." : "삭제"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* Status History Dialog */}
        <Dialog open={historyOpen} onOpenChange={setHistoryOpen}>
          <DialogContent className="sm:max-w-lg">
            <DialogHeader>
              <DialogTitle>상태 변경 이력</DialogTitle>
              <DialogDescription>
                모델 카탈로그의 상태 변경 기록입니다.
              </DialogDescription>
            </DialogHeader>

            <div className="max-h-80 overflow-y-auto">
              {historyLoading ? (
                <div className="flex items-center justify-center py-8">
                  <Loader2 className="size-5 animate-spin text-muted-foreground" />
                </div>
              ) : historyData && historyData.length > 0 ? (
                <div className="space-y-3">
                  {historyData.map((h) => (
                    <div
                      key={h.id}
                      className="flex items-start gap-3 rounded-lg border p-3 text-sm"
                    >
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-1.5 flex-wrap">
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
                        <div className="mt-1.5 flex items-center gap-2 text-xs text-muted-foreground">
                          <span>{h.changed_by}</span>
                          <span>·</span>
                          <span>{formatDateTime(h.changed_at)}</span>
                        </div>
                        {h.comment && (
                          <p className="mt-1 text-xs text-muted-foreground">{h.comment}</p>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="flex flex-col items-center gap-2 py-8 text-center">
                  <History className="size-8 text-muted-foreground" />
                  <p className="text-sm text-muted-foreground">변경 이력이 없습니다.</p>
                </div>
              )}
            </div>
          </DialogContent>
        </Dialog>

        <ModelDetailSheet
          model={detailModel}
          open={!!detailModel}
          onOpenChange={(o) => {
            if (!o) setDetailModel(null);
          }}
        />
      </div>
    </TooltipProvider>
  );
}
