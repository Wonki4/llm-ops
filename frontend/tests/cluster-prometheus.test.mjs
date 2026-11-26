import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../src/lib/cluster-prometheus.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } });
const { changedPrometheusSettings, prometheusBody, prometheusForm } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
const central = {
  prometheus_url: "https://prom.example/prometheus",
  prometheus_shared: true,
  prometheus_cluster_label: "kube_cluster",
  prometheus_cluster_value: "gpu-a",
  prometheus_has_token: true,
};

test("editing an unrelated cluster field leaves the entire Prometheus connection untouched", () => {
  assert.deepEqual(changedPrometheusSettings(prometheusForm(central), central), {});
  assert.deepEqual(changedPrometheusSettings(prometheusForm()), {});
});

test("testing the edited form uses the exact selected scope and preserves the stored credential", () => {
  const form = { ...prometheusForm(central), value: "gpu-b" };
  assert.deepEqual(prometheusBody(form), {
    prometheus_url: central.prometheus_url, prometheus_shared: true,
    prometheus_cluster_label: "kube_cluster", prometheus_cluster_value: "gpu-b",
  });
  assert.deepEqual(changedPrometheusSettings(form, central), { prometheus_cluster_value: "gpu-b" });
});

test("empty input preserves credentials until the administrator explicitly replaces or removes them", () => {
  const form = prometheusForm(central);
  assert.equal(form.token, "");
  assert.deepEqual(changedPrometheusSettings({ ...form, token: " new-value " }, central), {
    prometheus_bearer_token: "new-value",
  });
  assert.deepEqual(changedPrometheusSettings({ ...form, token: "discarded", removeToken: true }, central), {
    prometheus_bearer_token: null,
  });
});

test("clearing the URL clears central scope and credentials via the server's disconnect operation", () => {
  const form = { ...prometheusForm(central), url: "  ", token: "discarded" };
  assert.deepEqual(prometheusBody(form), {
    prometheus_url: null, prometheus_shared: false,
    prometheus_cluster_label: null, prometheus_cluster_value: null,
  });
});
