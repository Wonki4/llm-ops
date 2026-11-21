"use client";
import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { ArrowUp, ArrowDown, X, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { useServingRecipes, useK8sClusterSettings, useBenchmarkPresets, useDefaultNamespace, useGpuProfiles } from "@/hooks/use-api";
import { useCreateValidation, useValidationPreview } from "@/hooks/use-recipe-validations";
import { scopeFromParams, PORTAL_CLUSTER, type ServingScope } from "@/lib/serving-scope";
import { VALIDATIONS, type ValidationConditions, type ValidationCreate } from "@/lib/recipe-validation";
import { createRequestKey } from "@/lib/request-key";
import { DeployRecipeBrowser } from "@/components/deploy-recipe-browser";
import { DeploymentScopeFields } from "@/components/deployment-scope-fields";
import { useClusterName } from "@/components/serving-scope-filter";
import { StorageResolutionPreview, recipeStorageIds } from "@/components/storage-resolution-preview";
import { DEFAULT_PERF_PARAMS, DEFAULT_ACCURACY_PARAMS, PerfWorkloadFields, AccuracyParamsFields } from "@/components/benchmark-workload-fields";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";

export default function NewValidationPage() { return <Suspense><Load /></Suspense>; }
function Load() {
  const settings = useK8sClusterSettings(), params = useSearchParams();
  if (settings.error) return <p role="alert">{settings.error.message}</p>;
  if (!settings.data) return <Loader2 className="animate-spin" />;
  return <Form initialScope={scopeFromParams(params, settings.data)} initialIds={(params.get("recipes") ?? "").split(",").filter(Boolean)} />;
}
function Form({ initialScope, initialIds }: { initialScope: ServingScope; initialIds: string[] }) {
  const t = useTranslations("recipeValidation"), router = useRouter(), clusterName = useClusterName();
  const recipes = useServingRecipes(), presets = useBenchmarkPresets();
  const [automatic, setAutomatic] = useState(initialIds.length === 0);
  const [targetModel, setTargetModel] = useState(""), [alternatives, setAlternatives] = useState("");
  const [searchLimits, setSearchLimits] = useState({ max_candidates: 12, max_search_seconds: 21600, patience: 3, min_improvement_percent: 1, min_context_length: 4096 });
  const [ids, setIds] = useState(initialIds), [step, setStep] = useState(0);
  const [cluster, setCluster] = useState(initialScope.cluster === PORTAL_CLUSTER ? "" : initialScope.cluster), [namespace, setNamespace] = useState(initialScope.ns);
  const defaultNs = useDefaultNamespace(cluster), gpu = useGpuProfiles(cluster || null);
  const [gpuType, setGpuType] = useState(""), [gpuLimit, setGpuLimit] = useState(8), [name, setName] = useState(""), [alias, setAlias] = useState("");
  const [host, setHost] = useState(""), [airgap, setAirgap] = useState(false), [storageBlocked, setStorageBlocked] = useState(false);
  const [perf, setPerf] = useState({ ...DEFAULT_PERF_PARAMS, max_concurrency: "32" });
  const [repeats, setRepeats] = useState(3), [cache, setCache] = useState(false), [prefix, setPrefix] = useState(768);
  const [accuracy, setAccuracy] = useState(false), [acc, setAcc] = useState(DEFAULT_ACCURACY_PARAMS), [accuracyImage, setAccuracyImage] = useState("");
  const [gates, setGates] = useState([{ task: "mmlu", metric: "acc,none", minimum: 0.5 }]);
  const [metric, setMetric] = useState("output_throughput"), [ttft, setTtft] = useState(""), [throughput, setThroughput] = useState("");
  const [limits, setLimits] = useState({ assets: 3600, scheduling: 1800, startup: 7200, measurement: 3600, total: 86400 });
  const [requestKey] = useState(createRequestKey);
  const preview = useValidationPreview(), create = useCreateValidation();
  const selected = ids.map(id => recipes.data?.find(r => r.id === id)).filter(r => r !== undefined);
  const storageIds = automatic ? [] : [...new Set(selected.flatMap(recipeStorageIds))];
  const conditions: ValidationConditions = {
    search: automatic ? { ...searchLimits, target_model: targetModel.trim(), alternative_models: alternatives.split(",").map(x => x.trim()).filter(Boolean) } : null,
    ingress_host: host, ingress_class: "nginx", gpu_type: gpuType || null, gpu_limit: gpuLimit, time_limits: limits,
    performance: { random_input_len: perf.random_input_len, random_output_len: perf.random_output_len, num_prompts: perf.num_prompts,
      max_concurrency: Number(perf.max_concurrency) || 32, seed: perf.seed, ignore_eos: perf.ignore_eos,
      ...(perf.request_rate !== "" ? { request_rate: Number(perf.request_rate) } : {}) },
    repeats, prefix_len: cache ? prefix : null, tokenizer: perf.tokenizer || null, airgap,
    accuracy: accuracy ? { tasks: acc.tasks, batch_size: acc.batch_size, num_concurrent: acc.num_concurrent, apply_chat_template: acc.apply_chat_template,
      ...(acc.limit ? { limit: Number(acc.limit) } : {}), ...(acc.num_fewshot ? { num_fewshot: Number(acc.num_fewshot) } : {}), ...(acc.gen_kwargs ? { gen_kwargs: acc.gen_kwargs } : {}) } : null,
    accuracy_image: accuracyImage || null, quality_gates: accuracy ? gates : [],
    max_ttft_p99_ms: ttft ? Number(ttft) : null, min_output_throughput: throughput ? Number(throughput) : null, selection_metric: metric,
  };
  const body: ValidationCreate = { request_key: requestKey, name, model_name: alias, cluster_id: cluster || null,
    namespace: namespace.trim() || defaultNs, candidates: automatic ? [] : ids.map(recipe_id => ({ recipe_id })), conditions };
  function move(index: number, direction: number) { const next = [...ids]; [next[index], next[index + direction]] = [next[index + direction], next[index]]; setIds(next); }
  async function next(e: React.FormEvent) {
    e.preventDefault();
    if (step === 0) { setStep(1); return; }
    try {
      if (step === 1) { await preview.mutateAsync(body); setStep(2); }
      else { const job = await create.mutateAsync(body); router.push(`${VALIDATIONS}/${job.id}`); }
    } catch (error) { toast.error(error instanceof Error ? error.message : String(error)); }
  }
  return <form className="mx-auto max-w-5xl space-y-5" onSubmit={next}>
    <Link className="text-sm text-muted-foreground hover:underline" href={VALIDATIONS}>← {t("title")}</Link>
    <h1 className="text-2xl font-bold">{t("new")}</h1>
    <ol className="grid grid-cols-3 gap-2">{["choose", "conditions", "confirm"].map((s, i) => <li key={s} aria-current={step === i ? "step" : undefined} className={`rounded-md border px-3 py-3 text-sm ${step === i ? "border-primary bg-primary/5 font-semibold" : "text-muted-foreground"}`}>{i + 1}. {t(s)}</li>)}</ol>
    {step === 0 && <>
      <div className="grid gap-3 sm:grid-cols-2">{[true, false].map(mode => <button key={String(mode)} type="button" aria-pressed={automatic === mode} className={`rounded-lg border p-4 text-left ${automatic === mode ? "border-primary bg-primary/5" : ""}`} onClick={() => setAutomatic(mode)}><span className="font-semibold">{t(mode ? "searchMode" : "compareMode")}</span><span className="mt-2 block text-sm text-muted-foreground">{t(mode ? "searchModeHint" : "chooseHint")}</span></button>)}</div>
      {automatic ? <Card><CardHeader><CardTitle>{t("searchTarget")}</CardTitle></CardHeader><CardContent className="space-y-4">
        <Field label={t("targetModel")}><Input value={targetModel} onChange={e => setTargetModel(e.target.value)} placeholder="Qwen/Qwen3.5-32B" required /></Field>
        <Field label={t("alternativeModels")}><Input value={alternatives} onChange={e => setAlternatives(e.target.value)} /></Field>
        <p className="text-sm text-muted-foreground">{t("searchClientHint")}</p>
      </CardContent></Card> : <>
      <p className="text-sm text-muted-foreground">{t("chooseHint")}</p>
      {recipes.error && <p role="alert">{recipes.error.message}</p>}
      {recipes.isPending ? <Loader2 className="animate-spin" /> : <DeployRecipeBrowser recipes={recipes.data ?? []} selectedIds={new Set(ids)} onSelect={r => setIds(ids.includes(r.id) ? ids.filter(id => id !== r.id) : [...ids, r.id])} />}
      <Card><CardHeader><CardTitle>{t("selectedCount", { count: selected.length })}</CardTitle></CardHeader><CardContent>
        <ol className="divide-y">{selected.map((r, i) => <li key={r.id} className="flex items-center gap-3 py-3"><span className="text-muted-foreground">{i + 1}</span><div className="min-w-0 flex-1"><p className="font-medium">{r.name}</p><p className="break-all text-xs text-muted-foreground">{r.hf_repo_id || r.model_path} · {r.serving_mode === "pd" ? `${(r.pd_config?.prefill?.gpu_count ?? r.gpu_count) * (r.pd_config?.prefill?.replicas ?? 1) + (r.pd_config?.decode?.gpu_count ?? r.gpu_count) * (r.pd_config?.decode?.replicas ?? 1)}` : r.gpu_count} GPU</p></div>
          <Button type="button" variant="ghost" size="icon" aria-label={t("moveUp")} disabled={i === 0} onClick={() => move(i, -1)}><ArrowUp /></Button><Button type="button" variant="ghost" size="icon" aria-label={t("moveDown")} disabled={i === ids.length - 1} onClick={() => move(i, 1)}><ArrowDown /></Button><Button type="button" variant="ghost" size="icon" aria-label={t("remove")} onClick={() => setIds(ids.filter(id => id !== r.id))}><X /></Button>
        </li>)}</ol>
      </CardContent></Card></>}
    </>}
    {step === 1 && <>
      {automatic && <Card><CardHeader><CardTitle>{t("searchBounds")}</CardTitle></CardHeader><CardContent className="grid gap-4 sm:grid-cols-2">{(Object.keys(searchLimits) as (keyof typeof searchLimits)[]).map(key => <Field key={key} label={t(`search_${key}`)}><Input type="number" min={key === "min_improvement_percent" ? 0 : key === "max_search_seconds" ? 60 : 1} step={key === "min_improvement_percent" ? "any" : 1} max={key === "max_search_seconds" ? limits.total - 1 : undefined} required value={searchLimits[key]} onChange={e => setSearchLimits({ ...searchLimits, [key]: Number(e.target.value) })} /></Field>)}</CardContent></Card>}
      <Card><CardHeader><CardTitle>{t("target")}</CardTitle></CardHeader><CardContent className="space-y-4">
        <DeploymentScopeFields clusterId={cluster} namespace={namespace} onClusterChange={setCluster} onNamespaceChange={setNamespace} />
        <div className="grid gap-4 sm:grid-cols-2"><Field label={t("gpuLimit")}><Input type="number" min={1} max={1024} value={gpuLimit} onChange={e => setGpuLimit(Number(e.target.value))} required /></Field>
          <Field label={t("gpuType")}><select className="h-9 w-full rounded-md border bg-background px-2 text-sm" value={gpuType} onChange={e => setGpuType(e.target.value)}><option value="">{t("recipeGpu")}</option>{gpu.data?.profiles.filter(p => p.enabled).map(p => <option key={p.name}>{p.name}</option>)}</select></Field></div>
        <StorageResolutionPreview storageIds={storageIds} clusterId={cluster || null} namespace={body.namespace} onBlockedChange={setStorageBlocked} />
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={airgap} onChange={e => setAirgap(e.target.checked)} />{t("airgap")}</label>
        <p className="text-xs text-muted-foreground">{t("assetsHint")}</p>
      </CardContent></Card>
      <Card><CardHeader><CardTitle>{t("workload")}</CardTitle></CardHeader><CardContent className="space-y-4">
        <PerfWorkloadFields presets={presets.data} perf={perf} onChange={p => setPerf({ ...p, max_concurrency: String(p.max_concurrency) })} />
        <Field label={t("repeats")}><Input type="number" min={1} max={10} value={repeats} onChange={e => setRepeats(Number(e.target.value))} required /></Field>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={cache} onChange={e => setCache(e.target.checked)} />{t("cache")}</label>
        {cache && <Field label={t("prefix")}><Input type="number" min={1} max={perf.random_input_len - 1} value={prefix} onChange={e => setPrefix(Number(e.target.value))} /></Field>}
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={accuracy} onChange={e => setAccuracy(e.target.checked)} />{t("accuracy")}</label>
        {accuracy && <div className="space-y-4 rounded-md border p-4"><AccuracyParamsFields params={acc} onChange={setAcc} />
          <Field label={t("accuracyImage")}><Input value={accuracyImage} onChange={e => setAccuracyImage(e.target.value)} placeholder="registry/portal-accuracy@sha256:…" /></Field>
          {gates.map((gate, i) => <div key={i} className="grid gap-2 sm:grid-cols-3"><Field label={t("qualityTask")}><Input value={gate.task} onChange={e => setGates(gates.map((g, j) => i === j ? { ...g, task: e.target.value } : g))} required /></Field><Field label={t("qualityMetric")}><Input value={gate.metric} onChange={e => setGates(gates.map((g, j) => i === j ? { ...g, metric: e.target.value } : g))} required /></Field><Field label={t("qualityFloor")}><Input type="number" min={0} step="any" value={gate.minimum} onChange={e => setGates(gates.map((g, j) => i === j ? { ...g, minimum: Number(e.target.value) } : g))} required /></Field></div>)}
          <Button variant="outline" type="button" onClick={() => setGates([...gates, { task: "", metric: "acc,none", minimum: .5 }])}>{t("addGate")}</Button>
        </div>}
      </CardContent></Card>
      <Card><CardHeader><CardTitle>{t("requirements")}</CardTitle></CardHeader><CardContent className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-3"><Field label={t("selectionMetric")}><select className="h-9 w-full rounded-md border bg-background px-2 text-sm" value={metric} onChange={e => setMetric(e.target.value)}>{["output_throughput", "p99_ttft_ms", "request_throughput", "throughput_per_gpu"].map(m => <option key={m} value={m}>{t(`metric_${m}`)}</option>)}</select></Field><Field label={t("ttftCeiling")}><Input type="number" min={0.01} value={ttft} onChange={e => setTtft(e.target.value)} /></Field><Field label={t("throughputFloor")}><Input type="number" min={0.01} value={throughput} onChange={e => setThroughput(e.target.value)} /></Field></div>
        <p className="text-xs text-muted-foreground">{t("alternativeHint")}</p>
        <div className="grid gap-4 sm:grid-cols-3">{(Object.keys(limits) as (keyof typeof limits)[]).map(k => <Field key={k} label={t(`limit_${k}`)}><Input type="number" min={60} value={limits[k]} onChange={e => setLimits({ ...limits, [k]: Number(e.target.value) })} required /></Field>)}</div>
        <div className="grid gap-4 sm:grid-cols-2"><Field label={t("jobName")}><Input value={name} onChange={e => setName(e.target.value)} required maxLength={256} /></Field><Field label={t("alias")}><Input value={alias} onChange={e => setAlias(e.target.value)} required maxLength={256} /></Field><Field label={t("ingress")}><Input value={host} onChange={e => setHost(e.target.value)} required placeholder="model.internal.example.com" /></Field></div>
        <p className="text-xs text-muted-foreground">{t("permissionHint")}</p>
      </CardContent></Card>
    </>}
    {step === 2 && preview.data && <Card><CardHeader><CardTitle>{t("confirm")}</CardTitle></CardHeader><CardContent className="space-y-4">
      <dl className="grid gap-4 sm:grid-cols-2"><div><dt className="text-muted-foreground">{t("job")}</dt><dd>{name} / {alias}</dd></div><div><dt className="text-muted-foreground">{t("target")}</dt><dd>{clusterName(cluster || null)} / {preview.data.namespace}</dd></div><div><dt className="text-muted-foreground">{t("maxGpu")}</dt><dd>{preview.data.max_gpu_usage} GPU</dd></div><div><dt className="text-muted-foreground">{t("selectionMetric")}</dt><dd>{t(`metric_${metric}`)} · {repeats} {t("repetitions")}</dd></div></dl>
      <p className="text-sm">{t("requestSummary", { perAttempt: perf.num_prompts * (repeats + (cache ? 2 : 0)), total: perf.num_prompts * (repeats + (cache ? 2 : 0)) * (automatic ? searchLimits.max_candidates : selected.length) * 2 })}</p>
      {accuracy && <p className="text-xs text-muted-foreground">{t("accuracyRequestHint")}</p>}
      {automatic && <p className="rounded-md border p-3 text-sm">{targetModel} · {t("searchConfirm", { count: searchLimits.max_candidates, seconds: searchLimits.max_search_seconds })}</p>}
      <ol className="list-inside list-decimal space-y-2">{preview.data.candidates.map((c, i) => <li key={i}>{c.recipe.name} · {c.recipe.hf_repo_id || c.recipe.model_path} · {c.gpu_count} GPU</li>)}</ol>
      <p className="rounded-md bg-muted p-3 text-sm">{t("confirmHint")}</p>
      {preview.data.candidates.map((c, i) => <details key={i} className="rounded-md border p-3"><summary className="cursor-pointer text-sm">{c.recipe.name} — {t("assetsAndConfig")}</summary><ul className="my-3 space-y-2 text-xs">{c.checks.map(check => <li key={check.kind}>{check.status}: {check.message}</li>)}</ul><pre className="max-h-80 overflow-auto text-xs">{JSON.stringify(c.manifests, null, 2)}</pre></details>)}
      <details><summary className="cursor-pointer text-sm">{t("requirements")}</summary><pre className="max-h-80 overflow-auto text-xs">{JSON.stringify(conditions, null, 2)}</pre></details>
    </CardContent></Card>}
    {(preview.error || create.error) && <p role="alert" className="text-sm text-destructive">{preview.error?.message || create.error?.message}</p>}
    <footer className="sticky bottom-0 flex justify-between border-t bg-background/95 py-4"><Button type="button" variant="outline" disabled={!step || create.isPending} onClick={() => setStep(step - 1)}>{t("previous")}</Button>
      <Button type="submit" disabled={(automatic ? !targetModel.trim() : !ids.length || selected.length !== ids.length) || preview.isPending || create.isPending || (step > 0 && storageBlocked)}>{(preview.isPending || create.isPending) && <Loader2 className="animate-spin" />}{t(step === 2 ? "start" : "next")}</Button></footer>
  </form>;
}
function Field({ label, children }: { label: string; children: React.ReactNode }) { return <label className="block space-y-1.5"><span className="text-sm font-medium">{label}</span>{children}</label>; }
