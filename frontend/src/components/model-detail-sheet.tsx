"use client";

import { Loader2 } from "lucide-react";

import { useModelStatusHistory } from "@/hooks/use-api";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Card, CardContent } from "@/components/ui/card";
import type { ModelStatus, ModelWithCatalog } from "@/types";

interface ModelDetailSheetProps {
  model: ModelWithCatalog | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const STATUS_ORDER: ModelStatus[] = [
  "testing",
  "prerelease",
  "lts",
  "deprecating",
  "deprecated",
];

const STATUS_LABELS: Record<ModelStatus, string> = {
  testing: "Testing",
  prerelease: "Prerelease",
  lts: "LTS",
  deprecating: "Deprecating",
  deprecated: "Deprecated",
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
  return new Date(dateStr).toLocaleString("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatCost(cost: number | null | undefined): string {
  if (cost == null) return "-";
  return `$ ${(cost * 1000).toFixed(3)} / 1K tokens`;
}

function renderBoolean(value: boolean | null | undefined): string {
  if (value == null) return "-";
  return value ? "✓" : "✗";
}

export function ModelDetailSheet({ model, open, onOpenChange }: ModelDetailSheetProps) {
  const catalog = model?.catalog ?? null;
  const litellmInfo = model?.litellm_info ?? null;
  const provider = litellmInfo?.model_info?.litellm_provider ?? "-";

  const { data: statusHistory, isLoading: statusHistoryLoading } = useModelStatusHistory(
    catalog?.id,
  );

  const source = litellmInfo && catalog ? "배포 + 카탈로그" : litellmInfo ? "배포" : "카탈로그";

  const limitRows: { label: string; value: string | number }[] = [];
  if (litellmInfo?.model_info.max_tokens != null) {
    limitRows.push({ label: "Max Tokens", value: litellmInfo.model_info.max_tokens });
  }
  if (litellmInfo?.model_info.max_input_tokens != null) {
    limitRows.push({ label: "Max Input Tokens", value: litellmInfo.model_info.max_input_tokens });
  }
  if (litellmInfo?.model_info.max_output_tokens != null) {
    limitRows.push({ label: "Max Output Tokens", value: litellmInfo.model_info.max_output_tokens });
  }
  if (litellmInfo?.litellm_params.rpm != null) {
    limitRows.push({ label: "RPM", value: litellmInfo.litellm_params.rpm });
  }
  if (litellmInfo?.litellm_params.tpm != null) {
    limitRows.push({ label: "TPM", value: litellmInfo.litellm_params.tpm });
  }

  const featureRows: { label: string; value: string }[] = [];
  if (litellmInfo?.model_info.supports_vision != null) {
    featureRows.push({
      label: "Vision 지원",
      value: renderBoolean(litellmInfo.model_info.supports_vision),
    });
  }
  if (litellmInfo?.model_info.supports_function_calling != null) {
    featureRows.push({
      label: "Function Calling 지원",
      value: renderBoolean(litellmInfo.model_info.supports_function_calling),
    });
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="sm:max-w-lg w-[520px]">
        <SheetHeader className="px-6 pt-6 pb-4">
          <div className="space-y-2">
            <div className="flex items-center gap-2 flex-wrap">
              <SheetTitle>{catalog?.display_name ?? model?.model_name ?? "모델 상세"}</SheetTitle>
              {catalog && <Badge className={STATUS_STYLES[catalog.status]}>{catalog.status}</Badge>}
            </div>
            <SheetDescription>Provider: {provider}</SheetDescription>
          </div>
        </SheetHeader>

        <div className="flex-1 overflow-y-auto px-6 pb-6 space-y-5">
          <section>
            <h4 className="text-sm font-semibold mb-2">기본 정보</h4>
            <Card>
              <CardContent className="pt-4 space-y-2">
                <div className="flex justify-between text-sm gap-3">
                  <span className="text-muted-foreground">모델 ID</span>
                  <span className="text-right font-mono break-all">{model?.model_name ?? "-"}</span>
                </div>
                <div className="flex justify-between text-sm gap-3">
                  <span className="text-muted-foreground">실제 모델</span>
                  <span className="text-right font-mono break-all">
                    {litellmInfo?.litellm_params.model ?? "-"}
                  </span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground">Provider</span>
                  <span>{provider}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground">소스</span>
                  <span>{source}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground">모드</span>
                  <span>{litellmInfo?.model_info.mode ?? "-"}</span>
                </div>
              </CardContent>
            </Card>
          </section>

          {litellmInfo && (
            <>
              <Separator />
              <section>
                <h4 className="text-sm font-semibold mb-2">비용 정보</h4>
                <Card>
                  <CardContent className="pt-4 space-y-2">
                    <div className="flex justify-between text-sm">
                      <span className="text-muted-foreground">Input Cost</span>
                      <span>{formatCost(litellmInfo.model_info.input_cost_per_token)}</span>
                    </div>
                    <div className="flex justify-between text-sm">
                      <span className="text-muted-foreground">Output Cost</span>
                      <span>{formatCost(litellmInfo.model_info.output_cost_per_token)}</span>
                    </div>
                  </CardContent>
                </Card>
              </section>

              {limitRows.length > 0 && (
                <>
                  <Separator />
                  <section>
                    <h4 className="text-sm font-semibold mb-2">모델 제한</h4>
                    <Card>
                      <CardContent className="pt-4 space-y-2">
                        {limitRows.map((item) => (
                          <div key={item.label} className="flex justify-between text-sm">
                            <span className="text-muted-foreground">{item.label}</span>
                            <span>{item.value}</span>
                          </div>
                        ))}
                      </CardContent>
                    </Card>
                  </section>
                </>
              )}

              {featureRows.length > 0 && (
                <>
                  <Separator />
                  <section>
                    <h4 className="text-sm font-semibold mb-2">기능</h4>
                    <Card>
                      <CardContent className="pt-4 space-y-2">
                        {featureRows.map((item) => (
                          <div key={item.label} className="flex justify-between text-sm">
                            <span className="text-muted-foreground">{item.label}</span>
                            <span>{item.value}</span>
                          </div>
                        ))}
                      </CardContent>
                    </Card>
                  </section>
                </>
              )}
            </>
          )}

          {catalog && (
            <>
              <Separator />
              <section>
                <h4 className="text-sm font-semibold mb-2">카탈로그 정보</h4>
                <Card>
                  <CardContent className="pt-4 space-y-2">
                    <div className="flex justify-between text-sm gap-3">
                      <span className="text-muted-foreground">설명</span>
                      <span className="text-right break-words">{catalog.description ?? "-"}</span>
                    </div>
                    <div className="flex justify-between text-sm items-center">
                      <span className="text-muted-foreground">현재 상태</span>
                      <Badge className={STATUS_STYLES[catalog.status]}>{catalog.status}</Badge>
                    </div>
                    <div className="space-y-1.5 pt-1">
                      <div className="text-sm text-muted-foreground">상태별 일정</div>
                      {STATUS_ORDER.some((status) => catalog.status_schedule?.[status]) ? (
                        STATUS_ORDER.filter((status) => catalog.status_schedule?.[status]).map((status) => {
                          const currentIndex = STATUS_ORDER.indexOf(catalog.status);
                          const itemIndex = STATUS_ORDER.indexOf(status);
                          const isPassed = itemIndex <= currentIndex;
                          return (
                            <div
                              key={status}
                              className={`rounded-md px-2 py-1 text-xs flex items-center justify-between ${
                                isPassed
                                  ? "bg-muted text-foreground"
                                  : "border border-dashed border-border text-muted-foreground"
                              }`}
                            >
                              <span>{STATUS_LABELS[status]}</span>
                              <span>{catalog.status_schedule?.[status]}</span>
                            </div>
                          );
                        })
                      ) : (
                        <div className="text-sm">-</div>
                      )}
                    </div>
                    <div className="flex justify-between text-sm">
                      <span className="text-muted-foreground">등록자</span>
                      <span>{catalog.created_by ?? "-"}</span>
                    </div>
                    <div className="flex justify-between text-sm">
                      <span className="text-muted-foreground">등록일</span>
                      <span>{formatDateTime(catalog.created_at)}</span>
                    </div>
                    <div className="flex justify-between text-sm">
                      <span className="text-muted-foreground">수정일</span>
                      <span>{formatDateTime(catalog.updated_at)}</span>
                    </div>
                  </CardContent>
                </Card>
              </section>

              <Separator />
              <section>
                <h4 className="text-sm font-semibold mb-2">상태 변경 이력</h4>
                <Card>
                  <CardContent className="pt-4">
                    {statusHistoryLoading ? (
                      <div className="flex items-center justify-center py-3">
                        <Loader2 className="size-4 animate-spin text-muted-foreground" />
                      </div>
                    ) : statusHistory && statusHistory.length > 0 ? (
                      <div className="space-y-2">
                        {statusHistory.slice(0, 5).map((entry) => (
                          <div key={entry.id} className="rounded-md border px-2.5 py-2 text-xs space-y-1">
                            <div className="flex items-center justify-between gap-2">
                              <span className="text-muted-foreground">{formatDateTime(entry.changed_at)}</span>
                              <span className="font-mono text-muted-foreground">{entry.changed_by}</span>
                            </div>
                            <div className="flex items-center gap-1.5 flex-wrap">
                              {entry.previous_status ? (
                                <>
                                  <Badge className={STATUS_STYLES[entry.previous_status]}>
                                    {entry.previous_status}
                                  </Badge>
                                  <span className="text-muted-foreground">→</span>
                                </>
                              ) : (
                                <span className="text-muted-foreground">생성 →</span>
                              )}
                              <Badge className={STATUS_STYLES[entry.new_status]}>{entry.new_status}</Badge>
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="text-sm text-muted-foreground">변경 이력이 없습니다.</div>
                    )}
                  </CardContent>
                </Card>
              </section>
            </>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
