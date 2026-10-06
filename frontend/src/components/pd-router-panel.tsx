"use client";

import { useTranslations } from "next-intl";

import type { PdConfig, PdRouterConfig } from "@/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Area, Field } from "@/components/engine-args-fields";
import { DEFAULT_NIXL_PORT } from "@/lib/pd-serving";

type RouterText = "epp_registry" | "epp_repository" | "epp_tag" | "ingress_class";

/**
 * The llm-d router a P/D recipe deploys with: EPP image/replicas and
 * scheduler tuning, the routing sidecar and the NIXL plumbing between the
 * pools, and the router's ingress class. The host is instance-specific and
 * stays in the deploy dialog.
 */
export function PdRouterPanel({
  value, onChange, kvExtraText, onKvExtraChange,
}: {
  value: PdConfig;
  onChange: (patch: Partial<PdConfig>) => void;
  kvExtraText: string;
  onKvExtraChange: (v: string) => void;
}) {
  const t = useTranslations("servingRecipes");
  const router: PdRouterConfig = value.router ?? {};
  const setRouter = (patch: Partial<PdRouterConfig>) => onChange({ router: { ...router, ...patch } });
  const text = (k: RouterText) => (e: React.ChangeEvent<HTMLInputElement>) => setRouter({ [k]: e.target.value || null });
  const num = (v: string) => (v === "" ? undefined : Number(v));

  return (
    <div className="space-y-6" data-testid="pd-router-panel">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("pdEppSection")}</CardTitle>
          <p className="text-xs text-muted-foreground">{t("pdRouterTabHint", { model: "<model>" })}</p>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
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
                id="pd-epp-replicas" type="number" min={1} step={1} placeholder="1"
                value={router.epp_replicas ?? ""}
                onChange={(e) => setRouter({ epp_replicas: e.target.value === "" ? null : Number(e.target.value) })}
              />
            </Field>
          </div>
          <Field id="pd-ingress-class" label={t("pdIngressClass")} hint={t("pdIngressClassHint")}>
            <Input id="pd-ingress-class" className="font-mono" placeholder="nginx" value={router.ingress_class ?? ""} onChange={text("ingress_class")} />
          </Field>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("pdSchedulerSection")}</CardTitle>
          <p className="text-xs text-muted-foreground">{t("pdRouterHint")}</p>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field id="pd-peak-prefill" label={t("pdPeakPrefillThroughput")}>
            <Input
              id="pd-peak-prefill" type="number" min={1} step={1} placeholder="33821"
              value={router.peak_prefill_throughput ?? ""}
              onChange={(e) => setRouter({ peak_prefill_throughput: num(e.target.value) })}
            />
          </Field>
          <Field id="pd-prefix-tokens" label={t("pdPrefixTokensToMatch")}>
            <Input
              id="pd-prefix-tokens" type="number" min={1} step={1} placeholder="131072"
              value={router.prefix_tokens_to_match ?? ""}
              onChange={(e) => setRouter({ prefix_tokens_to_match: num(e.target.value) })}
            />
          </Field>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">{t("pdKvSection")}</CardTitle></CardHeader>
        <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field id="pd-nixl-port" label={t("pdNixlPort")}>
            <Input
              id="pd-nixl-port" type="number" min={1024} max={65535} step={1}
              value={value.nixl_port ?? DEFAULT_NIXL_PORT}
              onChange={(e) => onChange({ nixl_port: e.target.value === "" ? DEFAULT_NIXL_PORT : Number(e.target.value) })}
            />
          </Field>
          <Field id="pd-sidecar-image" label={t("pdSidecarImage")} hint={t("pdSidecarImageHint")}>
            <Input
              id="pd-sidecar-image" className="font-mono" placeholder="ghcr.io/llm-d/llm-d-router-disagg-sidecar:main"
              value={value.sidecar_image ?? ""} onChange={(e) => onChange({ sidecar_image: e.target.value || null })}
            />
          </Field>
          <Field id="pd-kv-extra" label={t("pdKvExtra")} span2 hint={t("pdKvExtraHint")}>
            <Area id="pd-kv-extra" rows={3} value={kvExtraText} onChange={onKvExtraChange} placeholder={'{"kv_buffer_device": "cuda"}'} />
          </Field>
        </CardContent>
      </Card>
    </div>
  );
}
