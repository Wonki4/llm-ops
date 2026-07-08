"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, FileCode2, Loader2, Play } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import { useCreateBenchmark, useModels, useModelDeployments, useK8sClusters, useBenchmarkPreview, useBenchmarks, useExternalServings } from "@/hooks/use-api";
import type { ExternalServing } from "@/hooks/use-api";
import type { BenchmarkTool, CreateBenchmarkRequest } from "@/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { JsonEditor } from "@/components/json-editor";
import { Label } from "@/components/ui/label";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";

const TOOL_OPTIONS: BenchmarkTool[] = [
  "vllm_serving",
  "sglang_serving",
  "lm_eval",
];

const TOOL_TO_KIND: Record<BenchmarkTool, "performance" | "accuracy"> = {
  vllm_serving: "performance",
  sglang_serving: "performance",
  lm_eval: "accuracy",
};

// Encodes a discovered external serving into a single <select> option value.
function externalKey(s: ExternalServing): string {
  return `ext::${s.cluster_id ?? ""}::${s.namespace}::${s.deployment_name}`;
}

// Maps to `vllm bench serve` flags (--num-prompts, --random-input-len, etc.).
const DEFAULT_PERF_PARAMS = {
  num_prompts: 200,
  random_input_len: 1024,
  random_output_len: 128,
  max_concurrency: "",
  request_rate: "",
  ignore_eos: true,
  seed: 0,
  random_range_ratio: "",
  goodput: "",
  tokenizer: "",
  // NFS override for a raw model_name target (deployment targets carry their own PVC).
  nfs_server: "",
  nfs_path: "",
  nfs_mount_path: "",
};

const DEFAULT_ACCURACY_PARAMS = {
  tasks: "mmlu",
  num_fewshot: "",
  limit: "",
  batch_size: 8,
  num_concurrent: 4,
  apply_chat_template: false,
  gen_kwargs: "",
};

// The active tab IS the start mode — no inference from which fields are set.
type BenchMode = "clone" | "direct" | "model" | "fromRun";

export default function NewBenchmarkPage() {
  const t = useTranslations("benchmarkForm");
  const tc = useTranslations("common");
  const router = useRouter();
  const { data: models, isLoading: modelsLoading } = useModels();
  const { data: deployments } = useModelDeployments();
  const { data: clusters } = useK8sClusters();
  const { data: external } = useExternalServings();
  const servings = external?.servings ?? [];
  const createMutation = useCreateBenchmark();
  const previewMutation = useBenchmarkPreview();

  const [deploymentId, setDeploymentId] = useState("");
  const [externalTarget, setExternalTarget] = useState<ExternalServing | null>(null);
  const [clusterId, setClusterId] = useState("");
  const [mode, setMode] = useState<BenchMode>("clone");
  const [servingOverridesText, setServingOverridesText] = useState("");
  const [modelName, setModelName] = useState("");
  const [tool, setTool] = useState<BenchmarkTool>("vllm_serving");
  const [perfParams, setPerfParams] = useState(DEFAULT_PERF_PARAMS);
  const [accParams, setAccParams] = useState(DEFAULT_ACCURACY_PARAMS);
  const [extraParamsText, setExtraParamsText] = useState("");
  const [extraArgsText, setExtraArgsText] = useState("");
  const [namespace, setNamespace] = useState("");
  const [image, setImage] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [loadFromId, setLoadFromId] = useState("");

  const { data: pastRuns } = useBenchmarks({ limit: 50 });

  const kind = TOOL_TO_KIND[tool];

  // Prefill the form from a previously-run benchmark. The API key is a secret
  // and is intentionally NOT restored — re-enter it if the target needs one.
  const loadFromRun = (runId: string) => {
    setLoadFromId(runId);
    const run = (pastRuns ?? []).find((r) => r.id === runId);
    if (!run) return;
    setExternalTarget(null);
    setTool(run.tool);
    if (run.deployment_id) {
      setDeploymentId(run.deployment_id);
      setModelName("");
      // Restore the run's mode, but land on the clone tab when the
      // deployment is no longer Ready — the direct tab won't list it.
      const dep = (deployments ?? []).find((d) => d.id === run.deployment_id);
      const forceClone = dep ? dep.ready_replicas === 0 : false;
      setMode(run.ephemeral || forceClone ? "clone" : "direct");
    } else {
      setDeploymentId("");
      setModelName(run.model_name);
      setMode("model");
    }
    setClusterId(run.cluster_id ?? "");
    setNamespace(run.k8s_namespace ?? "");
    setImage(run.bench_image ?? "");
    setServingOverridesText("");

    const params = run.params ?? {};
    const num = (v: unknown, d: number) => (typeof v === "number" ? v : d);
    const str = (v: unknown) => (v === undefined || v === null ? "" : String(v));
    if (TOOL_TO_KIND[run.tool] === "performance") {
      const known = new Set([
        "num_prompts", "random_input_len", "random_output_len", "max_concurrency",
        "request_rate", "ignore_eos", "seed", "random_range_ratio", "goodput",
        "tokenizer", "nfs_server", "nfs_path", "nfs_mount_path",
        "extra_args",
      ]);
      setPerfParams({
        num_prompts: num(params.num_prompts, DEFAULT_PERF_PARAMS.num_prompts),
        random_input_len: num(params.random_input_len, DEFAULT_PERF_PARAMS.random_input_len),
        random_output_len: num(params.random_output_len, DEFAULT_PERF_PARAMS.random_output_len),
        max_concurrency: str(params.max_concurrency),
        request_rate: str(params.request_rate),
        ignore_eos: params.ignore_eos !== false,
        seed: num(params.seed, DEFAULT_PERF_PARAMS.seed),
        random_range_ratio: str(params.random_range_ratio),
        goodput: str(params.goodput),
        tokenizer: str(params.tokenizer),
        nfs_server: str(params.nfs_server),
        nfs_path: str(params.nfs_path),
        nfs_mount_path: str(params.nfs_mount_path),
      });
      setExtraArgsText(str(params.extra_args));
      const extras = Object.fromEntries(Object.entries(params).filter(([k]) => !known.has(k)));
      setExtraParamsText(Object.keys(extras).length ? JSON.stringify(extras, null, 2) : "");
    } else {
      const known = new Set([
        "tasks", "batch_size", "num_concurrent", "num_fewshot", "limit",
        "apply_chat_template", "gen_kwargs",
      ]);
      setAccParams({
        tasks: Array.isArray(params.tasks)
          ? (params.tasks as string[]).join(", ")
          : str(params.tasks) || DEFAULT_ACCURACY_PARAMS.tasks,
        num_fewshot: str(params.num_fewshot),
        limit: str(params.limit),
        batch_size: num(params.batch_size, DEFAULT_ACCURACY_PARAMS.batch_size),
        num_concurrent: num(params.num_concurrent, DEFAULT_ACCURACY_PARAMS.num_concurrent),
        apply_chat_template: params.apply_chat_template === true,
        gen_kwargs: str(params.gen_kwargs),
      });
      setExtraArgsText("");
      const extras = Object.fromEntries(Object.entries(params).filter(([k]) => !known.has(k)));
      setExtraParamsText(Object.keys(extras).length ? JSON.stringify(extras, null, 2) : "");
    }
  };

  // Deduplicated model_name list for the dropdown.
  const modelOptions = useMemo(() => {
    if (!models) return [] as string[];
    return Array.from(new Set(models.map((m) => m.model_name))).sort();
  }, [models]);

  const allDeployments = deployments ?? [];
  const readyDeployments = allDeployments.filter((d) => d.ready_replicas > 0);
  const selectedDeployment = allDeployments.find((d) => d.id === deploymentId) ?? null;

  // The external target only participates while the clone tab is active;
  // leftover selections on inactive tabs never leak into cluster/namespace
  // autofill or the outgoing body.
  const activeExternal = mode === "clone" ? externalTarget : null;

  // deploymentId is shared between the clone and direct tabs (same target,
  // different mode); the direct tab simply won't resolve a not-Ready id.
  const directDeploymentId = readyDeployments.some((d) => d.id === deploymentId)
    ? deploymentId
    : "";

  const handleCloneTargetChange = (value: string) => {
    if (value.startsWith("ext::")) {
      const serving = servings.find((s) => externalKey(s) === value) ?? null;
      setExternalTarget(serving);
      setDeploymentId("");
    } else {
      setExternalTarget(null);
      setDeploymentId(value);
    }
  };

  // external_target is performance-only; drop it if the user switches to an
  // accuracy tool while one is selected.
  useEffect(() => {
    if (kind === "accuracy" && externalTarget) {
      setExternalTarget(null);
    }
  }, [kind, externalTarget]);

  const buildNamedParams = (): Record<string, unknown> => {
    if (kind === "performance") {
      const params: Record<string, unknown> = {
        num_prompts: perfParams.num_prompts,
        random_input_len: perfParams.random_input_len,
        random_output_len: perfParams.random_output_len,
      };
      if (perfParams.max_concurrency !== "") {
        params.max_concurrency = Number(perfParams.max_concurrency);
      }
      if (perfParams.request_rate !== "") {
        params.request_rate = Number(perfParams.request_rate);
      }
      if (perfParams.ignore_eos) {
        params.ignore_eos = true;
      }
      params.seed = perfParams.seed;
      if (perfParams.random_range_ratio !== "") {
        params.random_range_ratio = Number(perfParams.random_range_ratio);
      }
      if (perfParams.goodput.trim() !== "") {
        params.goodput = perfParams.goodput.trim();
      }
      if (perfParams.tokenizer.trim() !== "") {
        params.tokenizer = perfParams.tokenizer.trim();
      }
      // NFS override only applies to a raw model_name target; deployment and
      // external-clone targets mount their own PVC.
      const usesOwnPvc = mode !== "model";
      if (!usesOwnPvc && perfParams.nfs_server.trim() !== "") {
        params.nfs_server = perfParams.nfs_server.trim();
      }
      if (!usesOwnPvc && perfParams.nfs_path.trim() !== "") {
        params.nfs_path = perfParams.nfs_path.trim();
      }
      if (!usesOwnPvc && perfParams.nfs_mount_path.trim() !== "") {
        params.nfs_mount_path = perfParams.nfs_mount_path.trim();
      }
      return params;
    }
    const tasks = accParams.tasks
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    const params: Record<string, unknown> = {
      tasks,
      batch_size: accParams.batch_size,
      num_concurrent: accParams.num_concurrent,
    };
    if (accParams.num_fewshot !== "") {
      params.num_fewshot = Number(accParams.num_fewshot);
    }
    if (accParams.limit !== "") {
      params.limit = Number(accParams.limit);
    }
    if (accParams.apply_chat_template) {
      params.apply_chat_template = true;
    }
    if (accParams.gen_kwargs.trim() !== "") {
      params.gen_kwargs = accParams.gen_kwargs.trim();
    }
    return params;
  };

  const parseExtras = ():
    | { ok: true; value: Record<string, unknown> }
    | { ok: false; error: string } => {
    const text = extraParamsText.trim();
    if (!text) return { ok: true, value: {} };
    try {
      const parsed = JSON.parse(text);
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        return { ok: false, error: t("errorExtrasNotObject") };
      }
      return { ok: true, value: parsed as Record<string, unknown> };
    } catch (e) {
      return {
        ok: false,
        error: t("errorExtrasJsonInvalid", {
          message: e instanceof Error ? e.message : String(e),
        }),
      };
    }
  };

  // Shared by the deployment-clone and external-clone branches below.
  const parseServingOverrides = ():
    | { ok: true; value: Record<string, unknown> | undefined }
    | { ok: false; error: string } => {
    const text = servingOverridesText.trim();
    if (!text) return { ok: true, value: undefined };
    try {
      const parsed = JSON.parse(text);
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        return { ok: false, error: t("errorExtrasNotObject") };
      }
      return { ok: true, value: parsed as Record<string, unknown> };
    } catch (e) {
      return {
        ok: false,
        error: t("errorExtrasJsonInvalid", {
          message: e instanceof Error ? e.message : String(e),
        }),
      };
    }
  };

  // Best-effort body for the live YAML preview (never throws on bad JSON).
  // Built from the ACTIVE tab's fields only — leftovers on other tabs are ignored.
  const previewBody = useMemo((): CreateBenchmarkRequest | null => {
    const hasTarget =
      mode === "clone"
        ? !!(deploymentId || externalTarget)
        : mode === "direct"
          ? !!directDeploymentId
          : mode === "model"
            ? !!modelName.trim()
            : false;
    if (!hasTarget) return null;
    const extras = parseExtras();
    const body: CreateBenchmarkRequest = {
      tool,
      params: {
        ...buildNamedParams(),
        ...(extras.ok ? extras.value : {}),
        ...(kind === "performance" && extraArgsText.trim() ? { extra_args: extraArgsText.trim() } : {}),
      },
    };
    if (mode === "clone" && externalTarget) {
      // Perf-only clone of a discovered serving; the backend derives
      // placement (cluster/namespace) from external_target itself.
      body.external_target = {
        cluster_id: externalTarget.cluster_id,
        namespace: externalTarget.namespace,
        deployment_name: externalTarget.deployment_name,
      };
      const overrides = parseServingOverrides();
      if (overrides.ok && overrides.value) body.serving_overrides = overrides.value;
    } else if (mode === "clone") {
      body.deployment_id = deploymentId;
      body.ephemeral = true;
      const overrides = parseServingOverrides();
      if (overrides.ok && overrides.value) body.serving_overrides = overrides.value;
    } else if (mode === "direct") {
      body.deployment_id = directDeploymentId;
    } else {
      body.model_name = modelName.trim();
    }
    if (!(mode === "clone" && externalTarget)) {
      if (clusterId) body.cluster_id = clusterId;
      if (namespace.trim()) body.namespace = namespace.trim();
    }
    if (image.trim()) body.image = image.trim();
    if (apiKey.trim()) body.api_key = apiKey.trim();
    return body;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    mode, deploymentId, directDeploymentId, externalTarget, modelName, tool, perfParams,
    accParams, extraParamsText, extraArgsText, servingOverridesText, clusterId,
    namespace, image, apiKey,
  ]);

  const previewKey = previewBody ? JSON.stringify(previewBody) : "";
  const runPreview = previewMutation.mutate;
  useEffect(() => {
    if (!previewKey) return;
    const id = setTimeout(() => runPreview(JSON.parse(previewKey)), 400);
    return () => clearTimeout(id);
  }, [previewKey, runPreview]);

  const previewManifests = previewMutation.data?.manifests ?? [];

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (mode === "fromRun") return; // submit is disabled on this tab
    if (mode === "clone" && !deploymentId && !externalTarget) {
      toast.error(t("errorTargetRequired"));
      return;
    }
    if (mode === "direct" && !directDeploymentId) {
      toast.error(t("errorTargetRequired"));
      return;
    }
    if (mode === "model" && !modelName.trim()) {
      toast.error(t("errorModelRequired"));
      return;
    }
    if (kind === "accuracy") {
      const tasks = accParams.tasks
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      if (tasks.length === 0) {
        toast.error(t("errorTasksRequired"));
        return;
      }
    }
    const extras = parseExtras();
    if (!extras.ok) {
      toast.error(extras.error);
      return;
    }

    const body: CreateBenchmarkRequest = {
      tool,
      // Extras override named so users can correct any field via JSON.
      params: {
        ...buildNamedParams(),
        ...extras.value,
        ...(kind === "performance" && extraArgsText.trim() ? { extra_args: extraArgsText.trim() } : {}),
      },
    };
    if (mode === "clone" && externalTarget) {
      body.external_target = {
        cluster_id: externalTarget.cluster_id,
        namespace: externalTarget.namespace,
        deployment_name: externalTarget.deployment_name,
      };
      const overrides = parseServingOverrides();
      if (!overrides.ok) {
        toast.error(overrides.error);
        return;
      }
      if (overrides.value) body.serving_overrides = overrides.value;
    } else if (mode === "clone") {
      body.deployment_id = deploymentId;
      body.ephemeral = true;
      const overrides = parseServingOverrides();
      if (!overrides.ok) {
        toast.error(overrides.error);
        return;
      }
      if (overrides.value) body.serving_overrides = overrides.value;
    } else if (mode === "direct") {
      body.deployment_id = directDeploymentId;
    } else {
      body.model_name = modelName.trim();
    }
    // The backend derives placement from external_target itself; keep the
    // outgoing body honest and skip these for external runs.
    if (!(mode === "clone" && externalTarget)) {
      if (clusterId) body.cluster_id = clusterId;
      if (namespace.trim()) body.namespace = namespace.trim();
    }
    if (image.trim()) body.image = image.trim();
    if (apiKey.trim()) body.api_key = apiKey.trim();

    createMutation.mutate(body, {
      onSuccess: (run) => {
        toast.success(t("submitSuccess"));
        router.push(`/admin/benchmarks/${run.id}`);
      },
      onError: (e) =>
        toast.error(e instanceof Error ? e.message : t("submitFail")),
    });
  };

  return (
    <div className="space-y-6 max-w-6xl">
      <div>
        <Link
          href="/admin/benchmarks"
          className="text-sm text-muted-foreground hover:text-foreground inline-flex items-center gap-1"
        >
          <ArrowLeft className="size-3.5" />
          {t("backToList")}
        </Link>
        <h1 className="text-2xl font-bold mt-2">{t("pageTitle")}</h1>
        <p className="text-muted-foreground mt-1">{t("pageDescription")}</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-6 items-start">
      <form onSubmit={handleSubmit} className="space-y-5 lg:col-span-3">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t("target")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="cluster">{t("clusterLabel")}</Label>
              <select
                id="cluster"
                className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm disabled:opacity-50"
                value={activeExternal ? activeExternal.cluster_id ?? "" : clusterId}
                onChange={(e) => setClusterId(e.target.value)}
                disabled={!!activeExternal}
              >
                <option value="">{t("clusterDefault")}</option>
                {(clusters ?? []).map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                    {c.is_default ? " ★" : ""}
                    {c.api_server ? ` — ${c.api_server}` : ""}
                  </option>
                ))}
                {activeExternal &&
                  activeExternal.cluster_id &&
                  !(clusters ?? []).some((c) => c.id === activeExternal.cluster_id) && (
                    <option value={activeExternal.cluster_id}>{activeExternal.cluster_name}</option>
                  )}
              </select>
              <p className="text-xs text-muted-foreground">{t("clusterHint")}</p>
            </div>

            <Tabs value={mode} onValueChange={(v) => setMode(v as BenchMode)}>
              <TabsList className="w-full">
                <TabsTrigger value="clone">{t("tabClone")}</TabsTrigger>
                <TabsTrigger value="direct">{t("tabDirect")}</TabsTrigger>
                <TabsTrigger value="model">{t("tabModel")}</TabsTrigger>
                <TabsTrigger value="fromRun">{t("tabFromRun")}</TabsTrigger>
              </TabsList>

              <TabsContent value="clone" className="space-y-4 pt-2">
                <p className="text-xs text-muted-foreground">{t("tabCloneHint")}</p>
                <div className="space-y-1.5">
                  <Label htmlFor="clone_target">{t("deploymentLabel")}</Label>
                  <select
                    id="clone_target"
                    className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                    value={externalTarget ? externalKey(externalTarget) : deploymentId}
                    onChange={(e) => handleCloneTargetChange(e.target.value)}
                  >
                    <option value="">{t("deploymentNone")}</option>
                    <optgroup label={t("targetGroupPortal")}>
                      {allDeployments.map((d) => {
                        const gpu = d.node_selector?.["gpu-type"] ?? d.gpu_resource_key;
                        return (
                          <option key={d.id} value={d.id}>
                            {d.model_name} — {d.gpu_count}×{gpu}
                            {d.memory_limit ? ` · ${d.memory_limit}` : ""}
                            {d.ready_replicas > 0 ? "" : ` · ${t("statusNotReady")}`}
                          </option>
                        );
                      })}
                    </optgroup>
                    {kind !== "accuracy" && servings.length > 0 && (
                      <optgroup label={t("targetGroupExternal")}>
                        {servings.map((s) => (
                          <option key={externalKey(s)} value={externalKey(s)}>
                            {s.deployment_name} ({s.engine} · {s.namespace})
                          </option>
                        ))}
                      </optgroup>
                    )}
                  </select>
                  <p className="text-xs text-muted-foreground">{t("deploymentHint")}</p>
                  {!externalTarget && selectedDeployment && (
                    <p className="font-mono text-xs text-muted-foreground">
                      {selectedDeployment.model_path}
                    </p>
                  )}
                  {externalTarget && (
                    <p className="font-mono text-xs text-muted-foreground">
                      {externalTarget.model_path ?? externalTarget.deployment_name}
                    </p>
                  )}
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="serving_overrides">{t("servingOverridesLabel")}</Label>
                  <JsonEditor
                    id="serving_overrides"
                    value={servingOverridesText}
                    onChange={setServingOverridesText}
                    placeholder='{"gpu_count": 2, "gpu_type": "NVIDIA-H100"}'
                    minHeight="min-h-20"
                  />
                  <p className="text-xs text-muted-foreground">{t("servingOverridesHint")}</p>
                </div>
              </TabsContent>

              <TabsContent value="direct" className="space-y-4 pt-2">
                <p className="text-xs text-muted-foreground">{t("tabDirectHint")}</p>
                <div className="space-y-1.5">
                  <Label htmlFor="direct_target">{t("deploymentLabel")}</Label>
                  <select
                    id="direct_target"
                    className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                    value={directDeploymentId}
                    onChange={(e) => {
                      setExternalTarget(null);
                      setDeploymentId(e.target.value);
                    }}
                  >
                    <option value="">{t("deploymentNone")}</option>
                    {readyDeployments.map((d) => {
                      const gpu = d.node_selector?.["gpu-type"] ?? d.gpu_resource_key;
                      return (
                        <option key={d.id} value={d.id}>
                          {d.model_name} — {d.gpu_count}×{gpu}
                          {d.memory_limit ? ` · ${d.memory_limit}` : ""}
                        </option>
                      );
                    })}
                  </select>
                  <p className="text-xs text-muted-foreground">{t("deploymentHint")}</p>
                  {directDeploymentId && selectedDeployment && (
                    <p className="font-mono text-xs text-muted-foreground">
                      {selectedDeployment.model_path}
                    </p>
                  )}
                </div>
              </TabsContent>

              <TabsContent value="model" className="space-y-4 pt-2">
                <div className="space-y-1.5">
                  <Label htmlFor="model_name">{t("modelLabel")}</Label>
                  <select
                    id="model_name"
                    className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm disabled:opacity-50"
                    value={modelName}
                    onChange={(e) => setModelName(e.target.value)}
                    disabled={modelsLoading}
                  >
                    <option value="">{t("modelPlaceholder")}</option>
                    {modelOptions.map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                  </select>
                  <p className="text-xs text-muted-foreground">{t("modelHint")}</p>
                </div>
              </TabsContent>

              <TabsContent value="fromRun" className="space-y-4 pt-2">
                {(pastRuns?.length ?? 0) === 0 ? (
                  <p className="text-sm text-muted-foreground py-4 text-center">
                    {t("fromRunEmpty")}
                  </p>
                ) : (
                  <div className="space-y-1.5">
                    <Label htmlFor="load_from">{t("loadFromLabel")}</Label>
                    <select
                      id="load_from"
                      className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                      value={loadFromId}
                      onChange={(e) => loadFromRun(e.target.value)}
                    >
                      <option value="">{t("loadFromNone")}</option>
                      {(pastRuns ?? []).map((r) => (
                        <option key={r.id} value={r.id}>
                          {r.model_name} · {r.tool} · {r.status}
                          {r.created_at ? ` · ${new Date(r.created_at).toLocaleDateString()}` : ""}
                        </option>
                      ))}
                    </select>
                    <p className="text-xs text-muted-foreground">{t("loadFromHint")}</p>
                  </div>
                )}
              </TabsContent>
            </Tabs>

            <div className="space-y-1.5">
              <Label htmlFor="tool">{t("toolLabel")}</Label>
              <select
                id="tool"
                className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                value={tool}
                onChange={(e) => setTool(e.target.value as BenchmarkTool)}
              >
                {TOOL_OPTIONS.map((value) => (
                  <option key={value} value={value}>
                    {value} ({TOOL_TO_KIND[value]})
                  </option>
                ))}
              </select>
              <p className="text-xs text-muted-foreground">{t(`toolHint_${tool}`)}</p>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t("params")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {kind === "performance" ? (
              <PerfParamsFields
                params={perfParams}
                onChange={setPerfParams}
                showNfsOverride={mode === "model"}
              />
            ) : (
              <AccuracyParamsFields
                params={accParams}
                onChange={setAccParams}
              />
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t("extraParams")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <Label htmlFor="extra_params">{t("extraParamsLabel")}</Label>
            <JsonEditor
              id="extra_params"
              value={extraParamsText}
              onChange={setExtraParamsText}
              placeholder={'{\n  "request_rate": 4,\n  "ignore_eos": true\n}'}
            />
            <p className="text-xs text-muted-foreground">{t("extraParamsHint")}</p>
            {kind === "performance" && (
              <div className="space-y-1.5 pt-2">
                <Label htmlFor="extra_args">{t("extraArgsLabel")}</Label>
                <Input
                  id="extra_args"
                  value={extraArgsText}
                  onChange={(e) => setExtraArgsText(e.target.value)}
                  placeholder="--disable-tqdm --burstiness 0.5"
                  className="font-mono"
                />
                <p className="text-xs text-muted-foreground">{t("extraArgsHint")}</p>
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t("advanced")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="namespace">{t("namespaceLabel")}</Label>
              <Input
                id="namespace"
                placeholder="default"
                value={activeExternal ? activeExternal.namespace : namespace}
                onChange={(e) => setNamespace(e.target.value)}
                disabled={!!activeExternal}
              />
              <p className="text-xs text-muted-foreground">{t("namespaceHint")}</p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="image">{t("imageLabel")}</Label>
              <Input
                id="image"
                placeholder="llmops-benchmark:latest"
                value={image}
                onChange={(e) => setImage(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">{t("imageHint")}</p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="api_key">{t("apiKeyLabel")}</Label>
              <Input
                id="api_key"
                type="password"
                autoComplete="off"
                placeholder={t("apiKeyPlaceholder")}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">{t("apiKeyHint")}</p>
            </div>
          </CardContent>
        </Card>

        <div className="flex items-center justify-end gap-3">
          <Link href="/admin/benchmarks">
            <Button type="button" variant="outline">
              {tc("cancel")}
            </Button>
          </Link>
          <Button type="submit" disabled={createMutation.isPending || mode === "fromRun"}>
            {createMutation.isPending ? (
              <Loader2 className="size-4 mr-1 animate-spin" />
            ) : (
              <Play className="size-4 mr-1" />
            )}
            {t("submit")}
          </Button>
        </div>
      </form>

      <aside className="lg:col-span-2 lg:sticky lg:top-6">
        <Card className="overflow-hidden">
          <CardHeader className="pb-3">
            <CardTitle className="text-base flex items-center gap-2">
              <FileCode2 className="size-4" />
              {t("previewTitle")}
              {previewMutation.isPending && (
                <Loader2 className="size-3.5 animate-spin text-muted-foreground" />
              )}
            </CardTitle>
            <p className="text-xs text-muted-foreground">{t("previewHint")}</p>
          </CardHeader>
          <CardContent className="space-y-3">
            {previewManifests.length === 0 ? (
              <p className="text-sm text-muted-foreground py-6 text-center">
                {t("previewEmpty")}
              </p>
            ) : (
              previewManifests.map((m, i) => (
                <div key={i} className="rounded-md border overflow-hidden">
                  <div className="flex items-center gap-2 border-b bg-muted/40 px-3 py-1.5">
                    <span className="text-xs font-semibold">{m.kind}</span>
                    <span className="text-xs font-mono text-muted-foreground truncate">
                      {m.name}
                    </span>
                  </div>
                  <pre className="max-h-[28rem] overflow-auto bg-muted/20 p-3 text-xs leading-relaxed">
                    <code className="font-mono">{m.yaml}</code>
                  </pre>
                </div>
              ))
            )}
          </CardContent>
        </Card>
      </aside>
      </div>
    </div>
  );
}

function PerfParamsFields({
  params,
  onChange,
  showNfsOverride,
}: {
  params: typeof DEFAULT_PERF_PARAMS;
  onChange: (next: typeof DEFAULT_PERF_PARAMS) => void;
  showNfsOverride: boolean;
}) {
  const t = useTranslations("benchmarkForm");
  return (
    <>
      <div className="grid grid-cols-2 gap-4">
        <NumberField
          id="num_prompts"
          label={t("numPromptsLabel")}
          hint={t("numPromptsHint")}
          value={params.num_prompts}
          onChange={(v) => onChange({ ...params, num_prompts: v })}
          min={1}
        />
        <OptionalNumberField
          id="max_concurrency"
          label={t("maxConcurrencyLabel")}
          hint={t("maxConcurrencyHint")}
          value={params.max_concurrency}
          onChange={(v) => onChange({ ...params, max_concurrency: v })}
        />
        <NumberField
          id="random_input_len"
          label={t("randomInputLenLabel")}
          hint={t("randomInputLenHint")}
          value={params.random_input_len}
          onChange={(v) => onChange({ ...params, random_input_len: v })}
          min={1}
        />
        <NumberField
          id="random_output_len"
          label={t("randomOutputLenLabel")}
          hint={t("randomOutputLenHint")}
          value={params.random_output_len}
          onChange={(v) => onChange({ ...params, random_output_len: v })}
          min={1}
        />
        <OptionalNumberField
          id="request_rate"
          label={t("requestRateLabel")}
          hint={t("requestRateHint")}
          value={params.request_rate}
          onChange={(v) => onChange({ ...params, request_rate: v })}
        />
        <NumberField
          id="seed"
          label={t("seedLabel")}
          hint={t("seedHint")}
          value={params.seed}
          onChange={(v) => onChange({ ...params, seed: v })}
          min={0}
        />
        <OptionalNumberField
          id="random_range_ratio"
          label={t("randomRangeRatioLabel")}
          hint={t("randomRangeRatioHint")}
          value={params.random_range_ratio}
          onChange={(v) => onChange({ ...params, random_range_ratio: v })}
          step="0.1"
        />
        <div className="flex items-center gap-2 mt-6">
          <input
            id="ignore_eos"
            type="checkbox"
            className="size-4 rounded border-input"
            checked={params.ignore_eos}
            onChange={(e) => onChange({ ...params, ignore_eos: e.target.checked })}
          />
          <Label htmlFor="ignore_eos" className="cursor-pointer">
            {t("ignoreEosLabel")}
          </Label>
        </div>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="goodput">{t("goodputLabel")}</Label>
        <Input
          id="goodput"
          placeholder={t("goodputPlaceholder")}
          value={params.goodput}
          onChange={(e) => onChange({ ...params, goodput: e.target.value })}
        />
        <p className="text-xs text-muted-foreground">{t("goodputHint")}</p>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="tokenizer">{t("tokenizerLabel")}</Label>
        <Input
          id="tokenizer"
          placeholder={t("tokenizerPlaceholder")}
          value={params.tokenizer}
          onChange={(e) => onChange({ ...params, tokenizer: e.target.value })}
        />
        <p className="text-xs text-muted-foreground">{t("tokenizerHint")}</p>
      </div>
      {showNfsOverride && (
        <div className="space-y-1.5">
          <Label>{t("nfsOverrideLabel")}</Label>
          <Input
            id="nfs_server"
            placeholder={t("nfsServerPlaceholder")}
            value={params.nfs_server}
            onChange={(e) => onChange({ ...params, nfs_server: e.target.value })}
          />
          <div className="grid grid-cols-2 gap-3">
            <Input
              id="nfs_path"
              placeholder={t("nfsPathPlaceholder")}
              value={params.nfs_path}
              onChange={(e) => onChange({ ...params, nfs_path: e.target.value })}
            />
            <Input
              id="nfs_mount_path"
              placeholder={t("nfsMountPlaceholder")}
              value={params.nfs_mount_path}
              onChange={(e) => onChange({ ...params, nfs_mount_path: e.target.value })}
            />
          </div>
          <p className="text-xs text-muted-foreground">{t("nfsOverrideHint")}</p>
        </div>
      )}
    </>
  );
}

function AccuracyParamsFields({
  params,
  onChange,
}: {
  params: typeof DEFAULT_ACCURACY_PARAMS;
  onChange: (next: typeof DEFAULT_ACCURACY_PARAMS) => void;
}) {
  const t = useTranslations("benchmarkForm");
  return (
    <>
      <div className="space-y-1.5">
        <Label htmlFor="tasks">{t("tasksLabel")}</Label>
        <Input
          id="tasks"
          placeholder="mmlu, hellaswag"
          value={params.tasks}
          onChange={(e) => onChange({ ...params, tasks: e.target.value })}
        />
        <p className="text-xs text-muted-foreground">{t("tasksHint")}</p>
      </div>
      <div className="grid grid-cols-2 gap-4">
        <NumberField
          id="batch_size"
          label={t("batchSizeLabel")}
          hint={t("batchSizeHint")}
          value={params.batch_size}
          onChange={(v) => onChange({ ...params, batch_size: v })}
          min={1}
        />
        <NumberField
          id="num_concurrent"
          label={t("numConcurrentLabel")}
          hint={t("numConcurrentHint")}
          value={params.num_concurrent}
          onChange={(v) => onChange({ ...params, num_concurrent: v })}
          min={1}
        />
        <OptionalNumberField
          id="num_fewshot"
          label={t("numFewshotLabel")}
          hint={t("numFewshotHint")}
          value={params.num_fewshot}
          onChange={(v) => onChange({ ...params, num_fewshot: v })}
        />
        <OptionalNumberField
          id="limit"
          label={t("limitLabel")}
          hint={t("limitHint")}
          value={params.limit}
          onChange={(v) => onChange({ ...params, limit: v })}
        />
      </div>
      <div className="flex items-center gap-2">
        <input
          id="apply_chat_template"
          type="checkbox"
          className="size-4 rounded border-input"
          checked={params.apply_chat_template}
          onChange={(e) => onChange({ ...params, apply_chat_template: e.target.checked })}
        />
        <Label htmlFor="apply_chat_template" className="cursor-pointer">
          {t("applyChatTemplateLabel")}
        </Label>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="gen_kwargs">{t("genKwargsLabel")}</Label>
        <Input
          id="gen_kwargs"
          placeholder={t("genKwargsPlaceholder")}
          value={params.gen_kwargs}
          onChange={(e) => onChange({ ...params, gen_kwargs: e.target.value })}
        />
        <p className="text-xs text-muted-foreground">{t("genKwargsHint")}</p>
      </div>
    </>
  );
}

function NumberField({
  id,
  label,
  hint,
  value,
  onChange,
  min,
  step,
}: {
  id: string;
  label: string;
  hint: string;
  value: number;
  onChange: (v: number) => void;
  min?: number;
  step?: string;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        type="number"
        value={value}
        min={min}
        step={step}
        onChange={(e) => onChange(Number(e.target.value))}
      />
      <p className="text-xs text-muted-foreground">{hint}</p>
    </div>
  );
}

function OptionalNumberField({
  id,
  label,
  hint,
  value,
  onChange,
  step,
}: {
  id: string;
  label: string;
  hint: string;
  value: string;
  onChange: (v: string) => void;
  step?: string;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        type="number"
        value={value}
        step={step}
        onChange={(e) => onChange(e.target.value)}
      />
      <p className="text-xs text-muted-foreground">{hint}</p>
    </div>
  );
}
