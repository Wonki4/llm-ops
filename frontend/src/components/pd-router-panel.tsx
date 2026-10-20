"use client";

import { RotateCcw, Wand2 } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import { useConvertRouterValues, useRouterDefaults } from "@/hooks/use-api";
import type { PdConfig, PdRouterConfig } from "@/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Area, Field } from "@/components/engine-args-fields";
import { FormCard, FormSection } from "@/components/form-section";
import { DEFAULT_NIXL_PORT } from "@/lib/pd-serving";

type RouterText = "epp_registry" | "epp_repository" | "epp_tag" | "ingress_class" | "chart_repo" | "chart_name" | "chart_version";

/**
 * The llm-d router a P/D recipe deploys with: the chart (pinned per recipe),
 * its values.yaml used as written, the router's ingress class, and the NIXL
 * plumbing pools without their own P/D settings share. Recipes from before the
 * values editor keep the structured EPP/scheduler fields until converted.
 * The host is instance-specific and stays in the deploy dialog.
 */
export function PdRouterPanel({
  value, onChange, kvExtraText, onKvExtraChange, poolsHaveSettings, decodeHasSidecar,
}: {
  value: PdConfig;
  onChange: (patch: Partial<PdConfig>) => void;
  kvExtraText: string;
  onKvExtraChange: (v: string) => void;
  /** Both pools carry P/D settings: their own --kv-transfer-config / NIXL port win over the shared ones here. */
  poolsHaveSettings?: boolean;
  /** The decode tab carries the sidecar settings (image included). */
  decodeHasSidecar?: boolean;
}) {
  const t = useTranslations("servingRecipes");
  const { data: defaults } = useRouterDefaults();
  const convert = useConvertRouterValues();
  const router: PdRouterConfig = value.router ?? {};
  const setRouter = (patch: Partial<PdRouterConfig>) => onChange({ router: { ...router, ...patch } });
  const text = (k: RouterText) => (e: React.ChangeEvent<HTMLInputElement>) => setRouter({ [k]: e.target.value || null });
  const num = (v: string) => (v === "" ? undefined : Number(v));
  const hasValues = router.values_yaml != null;

  function toValues() {
    convert.mutate(router, {
      onSuccess: (r) => setRouter({ values_yaml: r.values_yaml }),
      onError: (e) => toast.error(e instanceof Error ? e.message : String(e)),
    });
  }
  function resetValues() {
    if (!defaults || !confirm(t("pdRouterValuesResetConfirm"))) return;
    setRouter({ values_yaml: defaults.values_yaml });
  }

  return (
    <FormCard data-testid="pd-router-panel">
      <FormSection title={t("pdRouterChartSection")} hint={t("pdRouterTabHint", { model: "<model>" })}>
        <Field id="pd-chart-repo" label={t("pdChartRepo")}>
          <Input id="pd-chart-repo" className="font-mono" placeholder={defaults?.chart_repo} value={router.chart_repo ?? ""} onChange={text("chart_repo")} />
        </Field>
        <Field id="pd-chart-name" label={t("pdChartName")}>
          <Input id="pd-chart-name" className="font-mono" placeholder={defaults?.chart_name} value={router.chart_name ?? ""} onChange={text("chart_name")} />
        </Field>
        <Field id="pd-chart-version" label={t("pdChartVersion")} hint={t("pdChartHint")}>
          <Input id="pd-chart-version" className="w-40 font-mono" placeholder={defaults?.chart_version} value={router.chart_version ?? ""} onChange={text("chart_version")} />
        </Field>
        <Field id="pd-ingress-class" label={t("pdIngressClass")} hint={t("pdIngressClassHint")}>
          <Input id="pd-ingress-class" className="w-40 font-mono" placeholder="nginx" value={router.ingress_class ?? ""} onChange={text("ingress_class")} />
        </Field>
      </FormSection>

      {hasValues ? (
        <FormSection
          title="values.yaml"
          hint={t("pdRouterValuesHint")}
          action={
            <Button type="button" size="xs" variant="outline" onClick={resetValues} disabled={!defaults} data-testid="pd-router-values-reset">
              <RotateCcw className="size-3.5" />{t("pdRouterValuesReset")}
            </Button>
          }
        >
          <Area
            id="pd-router-values" rows={24} nowrap value={router.values_yaml ?? ""}
            onChange={(v) => setRouter({ values_yaml: v })}
          />
        </FormSection>
      ) : (
        <>
          <FormSection
            title={t("pdEppSection")}
            action={
              <Button type="button" size="xs" variant="outline" onClick={toValues} disabled={convert.isPending} data-testid="pd-router-to-values">
                <Wand2 className="size-3.5" />{t("pdRouterToValues")}
              </Button>
            }
          >
            <Field id="pd-epp-registry" label={t("pdEppRegistry")} hint={t("pdEppImageHint")}>
              <Input id="pd-epp-registry" className="font-mono" placeholder="ghcr.io" value={router.epp_registry ?? ""} onChange={text("epp_registry")} />
            </Field>
            <Field id="pd-epp-repository" label={t("pdEppRepository")}>
              <Input id="pd-epp-repository" className="font-mono" placeholder="llm-d/llm-d-router-endpoint-picker" value={router.epp_repository ?? ""} onChange={text("epp_repository")} />
            </Field>
            <Field id="pd-epp-tag" label={t("pdEppTag")}>
              <Input id="pd-epp-tag" className="font-mono" placeholder="v0.9.0" value={router.epp_tag ?? ""} onChange={text("epp_tag")} />
            </Field>
            <Field id="pd-epp-replicas" label={t("pdEppReplicas")}>
              <Input
                id="pd-epp-replicas" type="number" min={1} step={1} placeholder="1" className="w-28"
                value={router.epp_replicas ?? ""}
                onChange={(e) => setRouter({ epp_replicas: e.target.value === "" ? null : Number(e.target.value) })}
              />
            </Field>
          </FormSection>
          <FormSection title={t("pdSchedulerSection")} hint={t("pdRouterHint")}>
            <Field id="pd-peak-prefill" label={t("pdPeakPrefillThroughput")}>
              <Input
                id="pd-peak-prefill" type="number" min={1} step={1} placeholder="33821" className="w-40"
                value={router.peak_prefill_throughput ?? ""}
                onChange={(e) => setRouter({ peak_prefill_throughput: num(e.target.value) })}
              />
            </Field>
            <Field id="pd-prefix-tokens" label={t("pdPrefixTokensToMatch")}>
              <Input
                id="pd-prefix-tokens" type="number" min={1} step={1} placeholder="131072" className="w-40"
                value={router.prefix_tokens_to_match ?? ""}
                onChange={(e) => setRouter({ prefix_tokens_to_match: num(e.target.value) })}
              />
            </Field>
          </FormSection>
        </>
      )}

      {!(poolsHaveSettings && decodeHasSidecar) && (
        <FormSection title={t("pdKvSection")} hint={poolsHaveSettings ? t("pdKvFromPoolSettings") : undefined}>
          {!poolsHaveSettings && (
            <Field id="pd-nixl-port" label={t("pdNixlPort")}>
              <Input
                id="pd-nixl-port" type="number" min={1024} max={65535} step={1} className="w-28"
                value={value.nixl_port ?? DEFAULT_NIXL_PORT}
                onChange={(e) => onChange({ nixl_port: e.target.value === "" ? DEFAULT_NIXL_PORT : Number(e.target.value) })}
              />
            </Field>
          )}
          {decodeHasSidecar ? (
            <p className="text-[11px] text-muted-foreground">{t("pdSidecarInDecode")}</p>
          ) : (
            <Field id="pd-sidecar-image" label={t("pdSidecarImage")} hint={t("pdSidecarImageHint")}>
              <Input
                id="pd-sidecar-image" className="font-mono" placeholder="ghcr.io/llm-d/llm-d-router-disagg-sidecar:main"
                value={value.sidecar_image ?? ""} onChange={(e) => onChange({ sidecar_image: e.target.value || null })}
              />
            </Field>
          )}
          {!poolsHaveSettings && (
            <Field id="pd-kv-extra" label={t("pdKvExtra")} hint={t("pdKvExtraHint")}>
              <Area id="pd-kv-extra" rows={3} value={kvExtraText} onChange={onKvExtraChange} placeholder={'{"kv_buffer_device": "cuda"}'} />
            </Field>
          )}
        </FormSection>
      )}
    </FormCard>
  );
}
