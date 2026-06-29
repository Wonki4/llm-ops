import Link from "next/link";
import { useTranslations } from "next-intl";

import { useLocaleTag } from "@/lib/locale";
import { ModelIcon } from "@/components/model-icon";
import { ModalityValue } from "@/components/model-modality";
import { ModelStatusBadge } from "@/components/model-status-badge";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { ModelStatus, ModelWithCatalog, ModelCatalog } from "@/types";

// A row pairs the model name (as the team/source lists it) with its merged
// catalog/litellm record, which is null when the name has no deployed/catalog match.
export type ModelTableRow = { name: string; model: ModelWithCatalog | null };

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

function formatCost(cost: number | null | undefined): string {
  if (cost == null) return "-";
  if (cost === 0) return "$ 0";
  return `$ ${(cost * 1_000_000).toFixed(2)} / 1M`;
}

/** Context window = max input tokens (falling back to max tokens), as "N tok". */
function formatContext(model: ModelWithCatalog | null): string {
  const info = model?.litellm_info?.model_info;
  const ctx = info?.max_input_tokens ?? info?.max_tokens ?? null;
  return ctx != null ? `${ctx.toLocaleString()} tok` : "-";
}

function formatDate(dateStr: string | null | undefined, localeTag: string): string {
  if (!dateStr) return "-";
  return new Date(dateStr).toLocaleDateString(localeTag, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
}

function getNextTransition(catalog: ModelCatalog | null): { date: string; status: ModelStatus } | null {
  if (!catalog?.status_schedule) return null;

  const today = new Date();
  today.setHours(0, 0, 0, 0);

  const currentStatusIndex = STATUS_INDEX[catalog.status];
  let nextDate: string | null = null;
  let nextStatus: ModelStatus | null = null;
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
      nextStatus = value;
    }
  }

  return nextDate && nextStatus ? { date: nextDate, status: nextStatus } : null;
}

/** Shared model table: name (icon + link), status, modality, costs, context, next transition. */
export function ModelTable({ rows }: { rows: ModelTableRow[] }) {
  const t = useTranslations("modelsDashboard");
  const localeTag = useLocaleTag();

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="min-w-[200px]">{t("table.modelName")}</TableHead>
          <TableHead className="w-[100px]">{t("table.status")}</TableHead>
          <TableHead className="whitespace-nowrap">{t("table.modality")}</TableHead>
          <TableHead>{t("table.inputCost")}</TableHead>
          <TableHead>{t("table.outputCost")}</TableHead>
          <TableHead>{t("table.cacheReadCost")}</TableHead>
          <TableHead className="w-[120px]">{t("table.context")}</TableHead>
          <TableHead className="w-[170px]">{t("table.nextTransition")}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map(({ name, model }) => (
          <TableRow key={name}>
            <TableCell>
              {model ? (
                <Link
                  href={`/models/${model.model_name.split("/").map(encodeURIComponent).join("/")}`}
                  className="flex items-center gap-2 text-left hover:underline"
                >
                  <ModelIcon
                    iconUrl={model.catalog?.icon_url}
                    provider={model.litellm_info?.model_info?.litellm_provider}
                    modelName={model.model_name}
                  />
                  <div className="max-w-[280px] truncate text-sm font-medium">
                    {model.catalog?.display_name ?? model.model_name}
                  </div>
                </Link>
              ) : (
                <div className="flex items-center gap-2">
                  <ModelIcon modelName={name} />
                  <div className="max-w-[280px] truncate text-sm font-medium text-muted-foreground">
                    {name}
                  </div>
                  <Badge variant="outline" className="text-[10px] px-1.5 py-0 text-muted-foreground">
                    {t("byTeam.notDeployed")}
                  </Badge>
                </div>
              )}
            </TableCell>
            <TableCell>
              {model?.catalog ? (
                <ModelStatusBadge status={model.catalog.status} />
              ) : (
                <span className="text-xs text-muted-foreground">-</span>
              )}
            </TableCell>
            <TableCell className="whitespace-nowrap">
              {model?.litellm_info ? (
                <ModalityValue info={model.litellm_info.model_info} size="size-4" />
              ) : (
                <span className="text-xs text-muted-foreground">-</span>
              )}
            </TableCell>
            <TableCell className="font-mono text-xs text-muted-foreground">
              {formatCost(model?.litellm_info?.model_info?.input_cost_per_token)}
            </TableCell>
            <TableCell className="font-mono text-xs text-muted-foreground">
              {formatCost(model?.litellm_info?.model_info?.output_cost_per_token)}
            </TableCell>
            <TableCell className="font-mono text-xs text-muted-foreground">
              {formatCost(model?.litellm_info?.model_info?.cache_read_input_token_cost)}
            </TableCell>
            <TableCell className="font-mono text-xs text-muted-foreground">
              {formatContext(model)}
            </TableCell>
            <TableCell>
              {(() => {
                const next = getNextTransition(model?.catalog ?? null);
                if (!next) return <span className="text-sm">-</span>;
                return (
                  <div className="flex items-center gap-1.5 whitespace-nowrap">
                    <span className="text-sm">{formatDate(next.date, localeTag)}</span>
                    <ModelStatusBadge status={next.status} />
                  </div>
                );
              })()}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
