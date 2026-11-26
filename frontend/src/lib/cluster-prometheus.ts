import type { PrometheusSettingsBody } from "@/hooks/use-api";
import type { K8sClusterSummary } from "@/types";

export interface PrometheusForm {
  url: string;
  shared: boolean;
  label: string;
  value: string;
  token: string;
  removeToken: boolean;
}

export function prometheusForm(cluster?: K8sClusterSummary | null): PrometheusForm {
  return {
    url: cluster?.prometheus_url ?? "",
    shared: cluster?.prometheus_shared ?? false,
    label: cluster?.prometheus_cluster_label ?? "",
    value: cluster?.prometheus_cluster_value ?? "",
    token: "",
    removeToken: false,
  };
}

export function prometheusBody(form: PrometheusForm): PrometheusSettingsBody {
  const url = form.url.trim();
  return {
    prometheus_url: url || null,
    prometheus_shared: Boolean(url) && form.shared,
    prometheus_cluster_label: (url && form.label.trim()) || null,
    prometheus_cluster_value: (url && form.value.trim()) || null,
    ...(url && form.removeToken ? { prometheus_bearer_token: null } : {}),
    ...(url && !form.removeToken && form.token.trim()
      ? { prometheus_bearer_token: form.token.trim() }
      : {}),
  };
}

export function changedPrometheusSettings(form: PrometheusForm, cluster?: K8sClusterSummary | null): PrometheusSettingsBody {
  const original = prometheusBody(prometheusForm(cluster));
  const fields = prometheusBody(form);
  const keys = Object.keys(fields) as (keyof PrometheusSettingsBody)[];
  return Object.fromEntries(keys.filter((key) => fields[key] !== original[key]).map((key) => [key, fields[key]]));
}
