"use client";

import { useState, useMemo } from "react";
import { Plus, Pencil, Trash2, Package, Server, BookOpen, History, ArrowRight, Loader2, Search, X } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

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

const STATUS_OPTIONS: { value: ModelStatus }[] = [
  { value: "testing" },
  { value: "prerelease" },
  { value: "lts" },
  { value: "deprecating" },
  { value: "deprecated" },
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

function formatContextLength(tokens: number | null | undefined): string {
  if (tokens == null) return "-";
  if (tokens >= 1_000_000) {
    const m = tokens / 1_000_000;
    return `${m % 1 === 0 ? m.toFixed(0) : m.toFixed(1)}M`;
  }
  if (tokens >= 1_000) {
    const k = tokens / 1_000;
    return `${k % 1 === 0 ? k.toFixed(0) : k.toFixed(1)}K`;
  }
  return tokens.toLocaleString();
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
  const tms = useTranslations("modelStatus");
  return <Badge className={STATUS_STYLES[status]}>{tms(status)}</Badge>;
}

/** Badge showing whether a model is LiteLLM-deployed, catalog-only, or both */
function SourceBadge({ model }: { model: ModelWithCatalog }) {
  const t = useTranslations("adminModels");
  if (model.litellm_info) {
    return (
      <Badge variant="outline" className="text-[10px] px-1.5 py-0 gap-1 border-green-300 text-green-700 dark:border-green-700 dark:text-green-400">
        <Server className="size-2.5" />
        LiteLLM
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="text-[10px] px-1.5 py-0 gap-1 border-orange-300 text-orange-700 dark:border-orange-700 dark:text-orange-400">
      <BookOpen className="size-2.5" />
      {t("source.external")}
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
  is_external: boolean;
}

const INITIAL_FORM: ModelFormState = {
  model_name: "",
  display_name: "",
  description: "",
  status: "testing",
  status_schedule: {},
  is_external: false,
};

function catalogToForm(catalog: ModelCatalog): ModelFormState {
  return {
    model_name: catalog.model_name,
    display_name: catalog.display_name,
    description: catalog.description ?? "",
    status: catalog.status,
    status_schedule: catalog.status_schedule ? { ...catalog.status_schedule } : {},
    is_external: false,
  };
}

// ─── Main Component ───────────────────────────────────────────

export default function ModelManagementPage() {
  const t = useTranslations("adminModels");
  const tc = useTranslations("common");
  const tms = useTranslations("modelStatus");

  const { data: models, isLoading, isError } = useModels();
  const createEntry = useCreateCatalogEntry();
  const updateEntry = useUpdateCatalogEntry();
  const deleteEntry = useDeleteCatalogEntry();

  // Filter state
  const [nameFilter, setNameFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [sourceFilter, setSourceFilter] = useState<string>("all");

  const filteredModels = useMemo(() => {
    if (!models) return [];
    return models.filter((m) => {
      if (nameFilter) {
        const q = nameFilter.toLowerCase();
        const displayName = m.catalog?.display_name?.toLowerCase() ?? "";
        const modelName = m.model_name.toLowerCase();
        if (!displayName.includes(q) && !modelName.includes(q)) return false;
      }
      if (statusFilter !== "all") {
        if (!m.catalog || m.catalog.status !== statusFilter) return false;
      }
      if (sourceFilter === "litellm" && !m.litellm_info) return false;
      if (sourceFilter === "external" && m.litellm_info) return false;
      return true;
    });
  }, [models, nameFilter, statusFilter, sourceFilter]);

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
    const trimmedName = form.model_name.trim();
    if (!trimmedName) {
      toast.error(t("form.modelNameRequired"));
      return;
    }

    if (editingId) {
      // Update
      const statusSchedule = buildStatusSchedule(form.status_schedule);
      updateEntry.mutate(
        {
          catalogId: editingId,
          body: {
            display_name: trimmedName,
            description: form.description.trim() || undefined,
            status: form.status,
            status_schedule: statusSchedule,
          },
        },
        {
          onSuccess: () => {
            toast.success(t("toast.updateSuccess"));
            setFormOpen(false);
          },
          onError: (error) => {
            toast.error(
              error instanceof Error
                ? error.message
                : t("toast.updateError"),
            );
          },
        },
      );
    } else {
      // Create
      const statusSchedule = buildStatusSchedule(form.status_schedule);
      createEntry.mutate(
        {
          model_name: trimmedName,
          display_name: trimmedName,
          description: form.description.trim() || undefined,
          status: form.status,
          status_schedule: statusSchedule,
          is_external: form.is_external,
        },
        {
          onSuccess: () => {
            toast.success(t("toast.createSuccess"));
            setFormOpen(false);
          },
          onError: (error) => {
            toast.error(
              error instanceof Error
                ? error.message
                : t("toast.createError"),
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
        toast.success(t("toast.deleteSuccess"));
        setDeleteOpen(false);
        setDeletingModel(null);
      },
      onError: (error) => {
        toast.error(
          error instanceof Error
            ? error.message
            : t("toast.deleteError"),
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
            <h1 className="text-2xl font-bold">{t("title")}</h1>
            <p className="text-muted-foreground mt-1">
              {t("description")}
            </p>
          </div>
          <Button onClick={() => openCreateDialog()}>
            <Plus className="size-4" />
            {t("registerCatalog")}
          </Button>
        </div>

        {/* Error state */}
        {isError && (
          <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
            {t("error.loadFailed")}
          </div>
        )}

        {/* Filters */}
        <div className="flex items-center gap-3 flex-wrap">
          <div className="relative flex-1 max-w-xs">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" />
            <Input
              placeholder={t("filters.modelNamePlaceholder")}
              value={nameFilter}
              onChange={(e) => setNameFilter(e.target.value)}
              className="pl-8 h-9"
            />
          </div>
          <Select value={statusFilter} onValueChange={setStatusFilter}>
            <SelectTrigger className="w-[140px] h-9">
              <SelectValue placeholder={t("filters.status")} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t("filters.allStatuses")}</SelectItem>
              {STATUS_OPTIONS.map((s) => (
                <SelectItem key={s.value} value={s.value}>{tms(s.value)}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={sourceFilter} onValueChange={setSourceFilter}>
            <SelectTrigger className="w-[140px] h-9">
              <SelectValue placeholder={t("filters.source")} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t("filters.allSources")}</SelectItem>
              <SelectItem value="litellm">LiteLLM</SelectItem>
              <SelectItem value="external">{t("source.external")}</SelectItem>
            </SelectContent>
          </Select>
          {(nameFilter || statusFilter !== "all" || sourceFilter !== "all") && (
            <Button variant="ghost" size="sm" onClick={() => { setNameFilter(""); setStatusFilter("all"); setSourceFilter("all"); }}>
              <X className="size-3.5 mr-1" />
              {t("filters.reset")}
            </Button>
          )}
          {models && (
            <span className="text-sm text-muted-foreground ml-auto">
              {t("filters.countSummary", { filtered: filteredModels.length, total: models.length })}
            </span>
          )}
        </div>

        {/* Table */}
        {isLoading ? (
          <TableSkeleton />
        ) : filteredModels.length > 0 ? (
          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("table.model")}</TableHead>
                  <TableHead>{t("table.source")}</TableHead>
                  <TableHead>Provider</TableHead>
                  <TableHead>{t("table.status")}</TableHead>
                  <TableHead>{t("table.visible")}</TableHead>
                  <TableHead>{t("table.context")}</TableHead>
                  <TableHead>Input Cost</TableHead>
                  <TableHead>Output Cost</TableHead>
                  <TableHead>{t("table.nextTransition")}</TableHead>
                  <TableHead>{t("table.actions")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredModels.map((model) => (
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

                    {/* Context length */}
                    <TableCell className="text-sm tabular-nums">
                      {formatContextLength(
                        model.litellm_info?.model_info.max_input_tokens ??
                          model.litellm_info?.model_info.max_tokens,
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
                              <TooltipContent>{t("actions.editCatalog")}</TooltipContent>
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
                              <TooltipContent>{t("actions.deleteCatalog")}</TooltipContent>
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
                              <TooltipContent>{t("actions.statusHistory")}</TooltipContent>
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
                                {t("registerCatalog")}
                              </Button>
                            </TooltipTrigger>
                            <TooltipContent>{t("actions.registerTooltip")}</TooltipContent>
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
            <p className="text-muted-foreground">{t("empty.noModels")}</p>
          </div>
        )}

        {/* Create / Edit Dialog */}
        <Dialog open={formOpen} onOpenChange={setFormOpen}>
          <DialogContent className="sm:max-w-lg max-h-[85vh] overflow-y-auto">
            <DialogHeader>
              <DialogTitle>
                {editingId ? t("form.editTitle") : t("form.createTitle")}
              </DialogTitle>
              <DialogDescription>
                {editingId
                  ? t("form.editDescription")
                  : t("form.createDescription")}
              </DialogDescription>
            </DialogHeader>

            <div className="grid gap-4 py-2">
              {/* type selector (create only) */}
              {!editingId && (
                <div className="grid gap-2">
                  <Label>{t("form.registrationType")} <span className="text-destructive">*</span></Label>
                  <div className="flex gap-2">
                    <Button
                      type="button"
                      variant={form.is_external ? "outline" : "default"}
                      size="sm"
                      onClick={() => {
                        setForm((prev) => ({ ...prev, is_external: false, model_name: "", display_name: "" }));
                      }}
                    >
                      <Server className="size-3.5 mr-1" />
                      {t("form.litellmModel")}
                    </Button>
                    <Button
                      type="button"
                      variant={form.is_external ? "default" : "outline"}
                      size="sm"
                      onClick={() => {
                        setForm((prev) => ({ ...prev, is_external: true, model_name: "", display_name: "" }));
                      }}
                    >
                      <BookOpen className="size-3.5 mr-1" />
                      {t("form.externalModel")}
                    </Button>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {form.is_external
                      ? t("form.externalModelHint")
                      : t("form.litellmModelHint")}
                  </p>
                </div>
              )}

              {/* model_name */}
              <div className="grid gap-2">
                <Label htmlFor="model-name">
                  {t("form.modelName")} <span className="text-destructive">*</span>
                </Label>
                {editingId ? (
                  <Input
                    id="model-name"
                    value={form.model_name}
                    disabled
                  />
                ) : form.is_external ? (
                  <Input
                    id="model-name"
                    value={form.model_name}
                    onChange={(e) => handleFormChange("model_name", e.target.value)}
                    placeholder={t("form.externalModelNamePlaceholder")}
                  />
                ) : (
                  <Select
                    value={form.model_name}
                    onValueChange={(value) => handleFormChange("model_name", value)}
                  >
                    <SelectTrigger id="model-name">
                      <SelectValue placeholder={t("form.litellmModelSelectPlaceholder")} />
                    </SelectTrigger>
                    <SelectContent>
                      {(() => {
                        const candidates = models?.filter((m) => m.litellm_info && !m.catalog) ?? [];
                        if (candidates.length === 0) {
                          return (
                            <div className="px-2 py-1.5 text-xs text-muted-foreground">
                              {t("form.noLitellmCandidates")}
                            </div>
                          );
                        }
                        return candidates.map((m) => (
                          <SelectItem key={m.model_name} value={m.model_name}>
                            {m.model_name}
                          </SelectItem>
                        ));
                      })()}
                    </SelectContent>
                  </Select>
                )}
              </div>

              {/* description */}
              <div className="grid gap-2">
                <Label htmlFor="model-desc">{t("form.description")}</Label>
                <textarea
                  id="model-desc"
                  rows={2}
                  value={form.description}
                  onChange={(e) =>
                    handleFormChange("description", e.target.value)
                  }
                  placeholder={t("form.descriptionPlaceholder")}
                  className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
                />
              </div>

              {/* status */}
              <div className="grid gap-2">
                <Label>{t("form.status")}</Label>
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
                        {tms(opt.value)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="grid gap-3">
                <Label>{t("form.statusSchedule")}</Label>
                <div className="grid gap-2">
                  {STATUS_OPTIONS.map((option) => (
                    <div key={option.value} className="grid grid-cols-[130px_1fr] items-center gap-3">
                      <Label htmlFor={`status-schedule-${option.value}`}>{tms(option.value)}</Label>
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
                {tc("cancel")}
              </Button>
              <Button onClick={handleFormSubmit} disabled={isSubmitting}>
                {isSubmitting
                  ? t("form.saving")
                  : editingId
                    ? t("form.update")
                    : t("form.register")}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* Delete Confirmation Dialog */}
        <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{t("deleteDialog.title")}</DialogTitle>
              <DialogDescription>
                <span className="font-semibold text-foreground">
                  {deletingModel?.catalog?.display_name ?? deletingModel?.model_name}
                </span>{" "}
                {t("deleteDialog.confirmMessage")}
                {deletingModel?.litellm_info && (
                  <span className="block mt-1 text-xs">
                    {t("deleteDialog.litellmNote")}
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
                {tc("cancel")}
              </Button>
              <Button
                variant="destructive"
                onClick={handleDelete}
                disabled={deleteEntry.isPending}
              >
                {deleteEntry.isPending ? t("form.deleting") : tc("delete")}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* Status History Dialog */}
        <Dialog open={historyOpen} onOpenChange={setHistoryOpen}>
          <DialogContent className="sm:max-w-lg">
            <DialogHeader>
              <DialogTitle>{t("historyDialog.title")}</DialogTitle>
              <DialogDescription>
                {t("historyDialog.description")}
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
                              <span className="text-xs text-muted-foreground">{t("historyDialog.created")}</span>
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
                  <p className="text-sm text-muted-foreground">{t("historyDialog.empty")}</p>
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
