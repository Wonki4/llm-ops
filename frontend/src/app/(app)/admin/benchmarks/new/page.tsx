"use client";
import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft, Loader2, Play } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";
import { useCreateBenchmark, useK8sClusters, useBenchmark, useBenchmarkPresetSettings } from "@/hooks/use-api";
import type { BenchmarkRun, BenchmarkPresetSettings, CreateBenchmarkRequest } from "@/types";
import { apiFetch } from "@/lib/api";
import { DEFAULT_PERF_PARAMS, applyPreset, restoreWorkload, endpointWorkloadParams, requestCount, matchedPresetKey } from "@/lib/benchmark-workload";
import { PerfWorkloadFields, RequestRateField } from "@/components/benchmark-workload-fields";
import { BenchmarkPresetManager } from "@/components/benchmark-preset-manager";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { FormSelect, FormSelectItem } from "@/components/ui/form-select";

export default function NewBenchmarkPage() { return <Suspense><Load /></Suspense>; }
function Load() {
  const params = useSearchParams(), source = useBenchmark(params.get("from") ?? ""), presets = useBenchmarkPresetSettings();
  const t = useTranslations("benchmarkSimple");
  if (presets.error || (params.has("from") && source.error)) return <p role="alert">{presets.error?.message ?? source.error?.message}</p>;
  if (!presets.data || (params.has("from") && source.isPending)) return <Loader2 className="animate-spin" />;
  if (source.data?.kind === "accuracy") return <p>{t("accuracyHistory")} <Link className="underline" href={`/admin/benchmarks/${source.data.id}`}>{t("back")}</Link></p>;
  return <RunForm key={source.data?.id ?? "new"} presets={presets.data} source={source.data} initialCluster={params.get("cluster") ?? ""} initialNamespace={params.get("ns") ?? ""} />;
}
function RunForm({ presets, source, initialCluster, initialNamespace }: { presets: BenchmarkPresetSettings; source?: BenchmarkRun; initialCluster: string; initialNamespace: string }) {
  const t = useTranslations("benchmarkForm"), s = useTranslations("benchmarkSimple"), router = useRouter();
  const clusters = useK8sClusters(), create = useCreateBenchmark();
  const oldEndpoint = source?.serving_snapshot as { source?: string; base_url?: string } | undefined;
  const [cluster, setCluster] = useState(source?.cluster_id ?? initialCluster), [namespace, setNamespace] = useState(source?.k8s_namespace ?? initialNamespace);
  const [perf, setPerf] = useState(() => source ? restoreWorkload(source.params) : applyPreset(DEFAULT_PERF_PARAMS, presets.presets[presets.default_preset]));
  const [model, setModel] = useState(source?.model_name ?? ""), [url, setUrl] = useState(oldEndpoint?.base_url ?? ""), [apiKey, setApiKey] = useState("");
  const [models, setModels] = useState<string[]>([]), [manualModel, setManualModel] = useState(!!source);
  const [discovering, setDiscovering] = useState(false), [discoveryError, setDiscoveryError] = useState("");
  const [label, setLabel] = useState(source?.label ?? ""), [note, setNote] = useState(source?.note ?? "");
  const matching = matchedPresetKey(presets.presets, perf);
  const busy = discovering || create.isPending;
  const validUrl = /^https?:\/\/\S+$/i.test(url.trim());

  function resetDiscovery() { setModels([]); setModel(""); setDiscoveryError(""); setManualModel(false); }
  function requestBody(selectedModel = model): CreateBenchmarkRequest {
    return {
      tool: "vllm_serving", base_url: url.trim(), model_name: selectedModel.trim(),
      params: { ...endpointWorkloadParams(perf), ...(matching ? { preset: matching, preset_name: presets.presets[matching].name ?? matching } : {}) },
      cluster_id: cluster || undefined, namespace: namespace.trim() || undefined,
      api_key: apiKey.trim() || undefined,
      label: label.trim() || undefined, note: note.trim() || undefined,
    };
  }
  async function discover(): Promise<string | null> {
    setDiscovering(true); setDiscoveryError("");
    try {
      const result = await apiFetch<{ models: string[] }>("/api/benchmarks/endpoint-models", {
        method: "POST", body: JSON.stringify({ base_url: url.trim(), api_key: apiKey.trim() || undefined }),
      });
      setModels(result.models); setManualModel(false);
      const selected = result.models.length === 1 ? result.models[0] : "";
      setModel(selected);
      return selected || null;
    } catch (e) {
      setDiscoveryError((e as Error).message); setManualModel(true);
      return null;
    } finally { setDiscovering(false); }
  }
  const clusterName = clusters.data?.find(c => c.id === cluster)?.name ?? clusters.data?.find(c => !cluster && c.is_default)?.name ?? t("clusterDefault");
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy || !validUrl) return;
    const selected = model.trim() || await discover();
    if (!selected) return;
    try { const run = await create.mutateAsync(requestBody(selected)); router.push(`/admin/benchmarks/${run.id}`); }
    catch (e) { toast.error(e instanceof Error ? e.message : String(e)); }
  }
  return <div className="mx-auto max-w-4xl space-y-5">
    <Link href="/admin/benchmarks" className="inline-flex items-center gap-1 text-sm text-muted-foreground"><ArrowLeft className="size-4" />{t("backToList")}</Link>
    <div className="flex items-center justify-between gap-4"><h1 className="text-2xl font-bold">{t("pageTitle")}</h1><BenchmarkPresetManager /></div>
    <form className="space-y-4" onSubmit={submit}>
      <Card><CardHeader><CardTitle className="text-base">1. {t("target")}</CardTitle></CardHeader><CardContent className="space-y-3">
        <p className="text-sm text-muted-foreground">{s("endpointHint")}</p>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="API Base URL" id="benchmark-url"><Input id="benchmark-url" required type="url" disabled={busy} placeholder="http://model.serving:8000/v1" value={url} onChange={e => { setUrl(e.target.value); resetDiscovery(); }} /></Field>
          <Field label={t("apiKeyLabel")} id="benchmark-key"><Input id="benchmark-key" type="password" autoComplete="off" disabled={busy} placeholder={s("apiKeyOptional")} value={apiKey} onChange={e => { setApiKey(e.target.value); resetDiscovery(); }} /></Field>
        </div>
        {source && !oldEndpoint?.base_url && <p className="text-sm text-muted-foreground">{s("rerunUrlRequired")}</p>}
        <div className="flex flex-wrap items-center gap-3">
          <Button type="button" size="sm" variant="outline" disabled={busy || !validUrl} onClick={() => void discover()}>{discovering && <Loader2 className="size-4 animate-spin" />}{s("discoverModels")}</Button>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" disabled={busy} checked={manualModel} onChange={e => { setManualModel(e.target.checked); setModel(models.length === 1 && !e.target.checked ? models[0] : ""); }} />{s("manualModel")}</label>
          {!manualModel && models.length === 1 && <span className="break-all text-sm">{s("detectedModel", { model })}</span>}
        </div>
        {discoveryError && <p role="alert" className="text-sm text-destructive">{discoveryError}</p>}
        {manualModel ? <Field label={t("endpointModelLabel")} id="benchmark-model"><Input id="benchmark-model" disabled={busy} value={model} onChange={e => setModel(e.target.value)} placeholder={s("manualModelHint")} /></Field> : models.length > 1 && <Field label={s("chooseModel")} id="benchmark-model"><FormSelect id="benchmark-model" className="w-full" disabled={busy} placeholder={s("chooseModel")} value={model} onValueChange={setModel}>{models.map(m => <FormSelectItem key={m} value={m}>{m}</FormSelectItem>)}</FormSelect></Field>}
      </CardContent></Card>
      <Card><CardHeader><CardTitle className="text-base">2. {s("workload")}</CardTitle></CardHeader><CardContent className="space-y-3"><PerfWorkloadFields presets={presets.presets} perf={perf} onChange={setPerf} estimatedInput />{perf.workload_type === "synthetic" && <p className="text-xs text-muted-foreground">{s("estimatedInputHint")}</p>}{source && <p className="text-xs text-muted-foreground">{s("rerunDefaults")}</p>}</CardContent></Card>
      <details className="rounded-lg border p-4"><summary className="cursor-pointer text-sm">{t("advanced")}</summary><div className="mt-4 space-y-4">
        <RequestRateField perf={perf} onChange={setPerf} disabled={busy} />
        <div className="space-y-3 border-t pt-4">
          <p className="text-xs text-muted-foreground">{s("runnerPlacement")}</p>
          <div className="grid gap-4 sm:grid-cols-2"><Field label={t("clusterLabel")}><FormSelect aria-label={t("clusterLabel")} className="w-full" disabled={busy} placeholder={t("clusterDefault")} value={cluster} onValueChange={v => { setCluster(v); setNamespace(""); }}><FormSelectItem value="">{t("clusterDefault")}</FormSelectItem>{clusters.data?.map(c => <FormSelectItem value={c.id} key={c.id}>{c.name}</FormSelectItem>)}</FormSelect></Field><Field label={t("namespaceLabel")} id="benchmark-namespace"><Input id="benchmark-namespace" disabled={busy} placeholder="default" value={namespace} onChange={e => setNamespace(e.target.value)} /></Field></div>
        </div>
      </div></details>
      <details className="rounded-lg border p-4"><summary className="cursor-pointer text-sm">{s("runNotes")}</summary><div className="mt-4 grid gap-4 sm:grid-cols-2"><Field label={s("runName")} id="benchmark-label"><Input id="benchmark-label" disabled={busy} maxLength={256} value={label} onChange={e => setLabel(e.target.value)} /></Field><Field label={t("noteLabel")} id="benchmark-note"><Input id="benchmark-note" disabled={busy} value={note} onChange={e => setNote(e.target.value)} /></Field></div></details>
      <Card><CardContent className="flex flex-wrap items-center justify-between gap-4 pt-6"><div><p className="font-medium">{s("runSummary", { count: requestCount(perf) })}</p><p className="mt-1 text-sm text-muted-foreground">{clusterName} / {namespace || "default"} · {s("noCloneGpu")}</p><p className="text-xs text-muted-foreground">{s("warmupExcluded")}</p></div><Button type="submit" disabled={busy || !validUrl || ((manualModel || models.length > 1) && !model.trim())}>{busy ? <Loader2 className="animate-spin" /> : <Play className="size-4" />}{t("submit")}</Button></CardContent></Card>
      {create.error && <p role="alert" className="text-sm text-destructive">{create.error.message}</p>}
    </form>
  </div>;
}
function Field({ label, id, children }: { label: string; id?: string; children: React.ReactNode }) { return <div className="space-y-1.5"><Label htmlFor={id}>{label}</Label>{children}</div>; }
