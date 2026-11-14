"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useBenchmarkPresets, useCreateBenchmark, useK8sClusters, useModelDeployments } from "@/hooks/use-api";
import { apiFetch } from "@/lib/api";
import type { BenchmarkPreviewResponse } from "@/hooks/use-api";
import type { BenchmarkRun, CreateBenchmarkRequest, LoadPreset, ModelDeployment } from "@/types";

function supportsClone(deployment: ModelDeployment) {
  const flags = deployment.vllm_extra_args ?? [];
  const setting = (name: string) => {
    const index = flags.findIndex(arg => arg === "--" + name || arg.startsWith("--" + name + "="));
    if (index < 0) return deployment.engine_args?.[name];
    return flags[index].includes("=") ? flags[index].split("=")[1] : flags[index + 1];
  };
  return deployment.engine === "vllm" && deployment.serving_mode !== "pd"
    && Number(setting("nnodes") ?? 1) === 1 && setting("distributed-executor-backend") !== "external_launcher";
}

export function CacheForm({ source }: { source?: BenchmarkRun }) {
  const t = useTranslations("benchExperiment");
  const f = useTranslations("benchmarkForm");
  const router = useRouter();
  const { data: deployments } = useModelDeployments();
  const { data: clusters } = useK8sClusters();
  const { data: presets } = useBenchmarkPresets();
  const create = useCreateBenchmark();
  const [deploymentId, setDeploymentId] = useState(source?.deployment_id ?? "");
  const [preset, setPreset] = useState(String(source ? source.params.preset ?? "" : "long_input"));
  const [overrides, setOverrides] = useState<Partial<LoadPreset>>(source ? {
    random_input_len: Number(source.params.random_input_len), random_output_len: Number(source.params.random_output_len),
    max_concurrency: Number(source.params.max_concurrency), num_prompts: Number(source.params.num_prompts),
  } : {});
  const [prefixLen, setPrefixLen] = useState<number | null>(source ? Number(source.params.prefix_len) : null);
  const [label, setLabel] = useState(source?.label ?? "");
  const [note, setNote] = useState(source?.note ?? "");
  const [yamlOpen, setYamlOpen] = useState(false);
  const selected = deployments?.find(d => d.id === deploymentId);
  const cluster = clusters?.find(c => c.id === selected?.cluster_id);
  const load = { ...presets?.[preset], ...overrides };
  const prefix = prefixLen ?? Math.floor((load.random_input_len ?? 0) * .75);
  const eligible = selected && supportsClone(selected);
  const dimensions = ["random_input_len", "random_output_len", "num_prompts", "max_concurrency"] as const;
  const valid = eligible && dimensions.every(key => Number.isInteger(load[key]) && Number(load[key]) > 0)
    && Number.isInteger(prefix) && prefix > 0 && prefix < Number(load.random_input_len);
  const matchingPreset = Object.entries(presets ?? {}).find(
    ([, candidate]) => dimensions.every(key => load[key] === candidate[key])
  )?.[0];
  const params = {
    ...load, experiment: "prefix_cache", prefix_len: prefix, seed: source?.params.seed ?? 0,
    ...(matchingPreset ? { preset: matchingPreset } : {}),
  };
  const body: CreateBenchmarkRequest = { tool: "vllm_serving", deployment_id: deploymentId, ephemeral: true, params };
  const preview = useQuery({
    queryKey: ["prefix-cache-preview", body], enabled: !!valid && yamlOpen,
    queryFn: () => apiFetch<BenchmarkPreviewResponse>("/api/benchmarks/preview", { method: "POST", body: JSON.stringify(body) }),
  });

  return <form className="space-y-4" onSubmit={e => {
    e.preventDefault();
    if (!valid) return;
    create.mutate({ ...body, label, note }, {
      onSuccess: r => router.push("/admin/benchmarks/" + r.id),
      onError: err => toast.error(err.message),
    });
  }}>
    <Card><CardHeader><CardTitle>{f("deploymentLabel")}</CardTitle></CardHeader>
      <CardContent className="space-y-2">
        <select aria-label={f("deploymentLabel")} className="h-10 w-full rounded-md border bg-background px-3 text-sm"
          value={deploymentId} onChange={e => setDeploymentId(e.target.value)} required>
          <option value="">{t("chooseDeployment")}</option>
          {deployments?.map(d => <option key={d.id} value={d.id}>{d.model_name} · {clusters?.find(c => c.id === d.cluster_id)?.name ?? f("clusterDefault")} / {d.namespace}</option>)}
        </select>
        <p className="text-sm text-muted-foreground">{selected && !eligible ? t("unsupported") : t("cloneHint")}</p>
        {source && deployments && !selected && <p className="text-sm text-destructive">{t("missingTarget")}</p>}
      </CardContent>
    </Card>
    <Card><CardHeader><CardTitle>{f("presetLabel")}</CardTitle></CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-2 sm:grid-cols-3">{Object.entries(presets ?? {}).map(([key, p]) =>
          <button type="button" key={key} aria-pressed={key === matchingPreset} className="rounded-md border p-3 text-left aria-pressed:border-primary aria-pressed:bg-muted/50"
            onClick={() => { setPreset(key); setOverrides({}); setPrefixLen(null); }}>
            <span className="block text-sm font-medium">{t(key)}</span>
            <span className="text-xs text-muted-foreground">{p.random_input_len} → {p.random_output_len} tok</span>
          </button>)}</div>
        {load.random_input_len != null && <p className="text-sm">{t("prefixSummary", { prefix, total: load.random_input_len, percent: load.random_input_len ? Math.round(100 * prefix / load.random_input_len) : 0 })}</p>}
        <details><summary className="cursor-pointer text-sm">{t("editWorkload")}</summary>
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            {([["random_input_len","randomInputLenLabel"],["random_output_len","randomOutputLenLabel"],["num_prompts","numPromptsLabel"],["max_concurrency","maxConcurrencyLabel"]] as const).map(([key, title]) =>
              <label key={key} className="space-y-1 text-sm">{f(title)}
                <Input type="number" min={key === "random_input_len" ? 2 : 1} required value={load[key] ?? ""} onChange={e => setOverrides({ ...overrides, [key]: Number(e.target.value) })}/>
              </label>)}
            <label className="space-y-1 text-sm">{t("prefixTokens")}<Input type="number" min={1} max={(load.random_input_len ?? 1) - 1} value={prefix} onChange={e => setPrefixLen(Number(e.target.value))}/></label>
          </div>
        </details>
      </CardContent>
    </Card>
    <details className="rounded-lg border p-4"><summary className="cursor-pointer text-sm">{f("advanced")}</summary>
      <div className="mt-3 space-y-2"><Label htmlFor="cache-label">{f("labelLabel")}</Label><Input id="cache-label" value={label} onChange={e => setLabel(e.target.value)}/>
        <Label htmlFor="cache-note">{f("noteLabel")}</Label><Input id="cache-note" value={note} onChange={e => setNote(e.target.value)}/>
        <details onToggle={e => setYamlOpen(e.currentTarget.open)}><summary className="cursor-pointer text-sm">{f("previewTitle")}</summary>
          {preview.error ? <p>{preview.error.message}</p> : <pre className="max-h-80 overflow-auto text-xs">{preview.data?.manifests.map(m => m.yaml).join("\n") || f("previewEmpty")}</pre>}
        </details>
      </div>
    </details>
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border p-4">
      <div className="text-sm"><strong>{t("isolated")}</strong>{selected && <p>{cluster?.name ?? f("clusterDefault")} / {selected.namespace} · GPU × {selected.gpu_count}</p>}
        {load.num_prompts != null && <p className="text-muted-foreground">{t("requests", { n: load.num_prompts, total: load.num_prompts * 2 })}</p>}</div>
      <Button disabled={!valid || create.isPending}>{f("submit")}</Button>
    </div>
  </form>;
}
