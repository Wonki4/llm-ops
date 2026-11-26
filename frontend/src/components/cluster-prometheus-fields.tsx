"use client";

import { Loader2, Plug } from "lucide-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { FormSelect, FormSelectItem } from "@/components/ui/form-select";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useTestClusterPrometheus } from "@/hooks/use-api";
import { prometheusBody, type PrometheusForm } from "@/lib/cluster-prometheus";
import type { K8sClusterSummary } from "@/types";

interface Props {
  form: PrometheusForm;
  cluster: K8sClusterSummary | null;
  onChange: (form: PrometheusForm) => void;
}

export function ClusterPrometheusFields({ form, cluster, onChange }: Props) {
  const t = useTranslations("settings.clusters.prometheus");
  const test = useTestClusterPrometheus();
  const changingTokenDestination = cluster?.prometheus_has_token && form.url.trim().replace(/\/+$/, "") !== cluster.prometheus_url;
  function update(patch: Partial<PrometheusForm>) {
    test.reset();
    onChange({ ...form, ...patch });
  }
  return (
    <fieldset className="space-y-3 rounded-lg border p-3">
      <legend className="px-1 text-sm font-semibold">{t("title")}</legend>
      <div className="space-y-1">
        <Label htmlFor="prometheus-url">{t("url")}</Label>
        <Input id="prometheus-url" type="url" value={form.url} placeholder="https://prometheus.example.com"
          onChange={(e) => update({ url: e.target.value })} />
        <p className="text-xs text-muted-foreground">{t("urlHint")}</p>
      </div>
      {form.url.trim() && (
        <>
          <div className="space-y-1">
            <Label htmlFor="prometheus-scope">{t("scope")}</Label>
            <FormSelect id="prometheus-scope" className="w-full" value={form.shared ? "shared" : "dedicated"}
              onValueChange={(value) => update({ shared: value === "shared" })}>
              <FormSelectItem value="dedicated">{t("dedicated")}</FormSelectItem>
              <FormSelectItem value="shared">{t("central")}</FormSelectItem>
            </FormSelect>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1">
              <Label htmlFor="prometheus-label">{t("label")}</Label>
              <Input id="prometheus-label" value={form.label} required={form.shared} placeholder="cluster"
                onChange={(e) => update({ label: e.target.value })} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="prometheus-value">{t("value")}</Label>
              <Input id="prometheus-value" value={form.value} required={form.shared} placeholder="gpu-a"
                onChange={(e) => update({ value: e.target.value })} />
            </div>
          </div>
          <p className="text-xs text-muted-foreground">{form.shared ? t("sharedHint") : t("dedicatedHint")}</p>
          <div className="space-y-1">
            <Label htmlFor="prometheus-token">{t("token")}</Label>
            <Input id="prometheus-token" type="password" autoComplete="new-password" value={form.token}
              disabled={form.removeToken} placeholder={cluster?.prometheus_has_token && !changingTokenDestination ? t("tokenSaved") : undefined}
              onChange={(e) => update({ token: e.target.value })} />
            {changingTokenDestination && <p className="text-xs text-muted-foreground">{t("tokenDestination")}</p>}
            {cluster?.prometheus_has_token && (
              <label className="flex items-center gap-2 text-xs">
                <input type="checkbox" checked={form.removeToken}
                  onChange={(e) => update({ removeToken: e.target.checked, token: "" })} />
                {t("removeToken")}
              </label>
            )}
          </div>
          <Button type="button" variant="outline" size="sm" disabled={test.isPending}
            onClick={() => test.mutate({ ...prometheusBody(form), cluster_id: cluster?.id })}>
            {test.isPending ? <Loader2 className="size-4 animate-spin" /> : <Plug className="size-4" />}
            {t("test")}
          </Button>
          {(test.data || test.error) && (
            <p role="status" className={test.data?.ok ? "text-xs" : "text-xs text-destructive"}>
              {test.error?.message ?? test.data?.message}
            </p>
          )}
        </>
      )}
    </fieldset>
  );
}
