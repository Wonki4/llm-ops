"use client";

import { useLocale, useTranslations } from "next-intl";
import type { KVObservation } from "@/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export function KVObservations({ rows }: { rows?: KVObservation[] }) {
  const t = useTranslations("kvObservations");
  const locale = useLocale();
  if (!rows) return null;
  const n = (v: number | null | undefined, digits = 0) =>
    v == null ? t("unavailable") : v.toLocaleString(locale, { maximumFractionDigits: digits });
  const signed = (v: number) => (v > 0 ? "+" : "") + n(v, 1);
  const stamp = (v: string) => new Date(v).toLocaleString(locale);
  const percent = (v: number | null) => v == null ? t("unavailable") : n(v * 100, 1) + "%";
  return <Card>
    <CardHeader><CardTitle className="text-base">{t("title")}</CardTitle></CardHeader>
    <CardContent className="space-y-2">
      {!rows.length && <p className="text-sm text-muted-foreground">{t("unavailable")}</p>}
      {rows.map(row => <details key={row.role + row.pod_uid + row.restart} className="rounded-md border px-3 py-2 text-sm">
        <summary className="cursor-pointer">
          <span className="font-medium">{t(row.role)} · {row.pod_name}</span>{" "}
          <span className="tabular-nums">{row.capacity_tokens == null ? t("unavailable") : t("tokens", { n: n(row.capacity_tokens) })}</span>
          {row.delta_tokens != null && <span className="ms-2 text-muted-foreground">
            {t("delta")} {signed(row.delta_tokens)}{row.delta_percent != null && ` (${signed(row.delta_percent)}%)`}
          </span>}
        </summary>
        <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
          <dt>{t("gpu")}</dt><dd>{row.gpu_type || "—"}</dd>
          <dt>{t("memory")}</dt><dd>{row.budget_bytes == null ? t("unavailable") : n(row.budget_bytes / 2**30, 2) + " GiB"}
            {row.budget_bytes_max != null && row.budget_bytes != null && row.budget_bytes_max > row.budget_bytes &&
              " – " + n(row.budget_bytes_max / 2**30, 2) + " GiB"}</dd>
          <dt>{t("source")}</dt><dd>{row.capacity_tokens_source ? t(row.capacity_tokens_source) : t("unavailable")}</dd>
          <dt>{t("estimate")}</dt><dd>{row.estimate?.capacity_tokens == null && row.estimate?.capacity_range
            ? row.estimate.capacity_range.map(v => n(v)).join(" – ") : n(row.estimate?.capacity_tokens)} {row.estimate?.rule_version && ` · ${row.estimate.rule_version}`}</dd>
          {row.estimate && <>
            <dt>{t("calculation")}</dt><dd>{[row.estimate.kv_dtype, row.estimate.tp && `TP ${row.estimate.tp}`,
              row.estimate.pp && `PP ${row.estimate.pp}`, row.estimate.dcp && `DCP ${row.estimate.dcp}`].filter(Boolean).join(" · ") || "—"}</dd>
            {!row.estimate.same_revision && <><dt>{t("delta")}</dt><dd>{t("revisionUnconfirmed")}</dd></>}
          </>}
          <dt>{t("utilization")}</dt><dd>{percent(row.utilization)}</dd>
          <dt>{t("hitRate")}</dt><dd>{percent(row.prefix_hit_rate)} {row.prefix_rate_scope && t(row.prefix_rate_scope)}</dd>
          <dt>{t("revision")}</dt><dd className="break-all">{row.revision || "—"}</dd>
          <dt>{t("engine")}</dt><dd className="break-all">{row.engine} · {row.image_id || row.image}</dd>
          <dt>{t("pod")}</dt><dd className="break-all">{row.pod_uid} · {t("restart", { n: row.restart })}</dd>
          <dt>{t("observed")}</dt><dd>{row.observed_at ? stamp(row.observed_at) : t("unavailable")}</dd>
          <dt>{t("lastSeen")}</dt><dd>{stamp(row.last_seen)}{!row.active && ` · ${t("retired")}`}</dd>
        </dl>
      </details>)}
    </CardContent>
  </Card>;
}
