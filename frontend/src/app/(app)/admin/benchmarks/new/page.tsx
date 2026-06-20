"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, FileCode2, Loader2, Play } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import { useCreateBenchmark, useModels, useModelDeployments, useK8sClusters, useBenchmarkPreview } from "@/hooks/use-api";
import type { BenchmarkTool, CreateBenchmarkRequest } from "@/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

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

// Maps to `vllm bench serve` flags (--num-prompts, --random-input-len, etc.).
const DEFAULT_PERF_PARAMS = {
  num_prompts: 200,
  random_input_len: 1024,
  random_output_len: 128,
  max_concurrency: "",
  request_rate: "",
  ignore_eos: true,
  tokenizer: "",
  // PVC override for a raw model_name target (deployment targets carry their own).
  pvc_name: "",
  pvc_mount_path: "",
};

const DEFAULT_ACCURACY_PARAMS = {
  tasks: "mmlu",
  num_fewshot: "",
  limit: "",
  batch_size: 8,
  num_concurrent: 4,
};

export default function NewBenchmarkPage() {
  const t = useTranslations("benchmarkForm");
  const tc = useTranslations("common");
  const router = useRouter();
  const { data: models, isLoading: modelsLoading } = useModels();
  const { data: deployments } = useModelDeployments();
  const { data: clusters } = useK8sClusters();
  const createMutation = useCreateBenchmark();
  const previewMutation = useBenchmarkPreview();

  const [deploymentId, setDeploymentId] = useState("");
  const [clusterId, setClusterId] = useState("");
  const [ephemeral, setEphemeral] = useState(false);
  const [servingOverridesText, setServingOverridesText] = useState("");
  const [modelName, setModelName] = useState("");
  const [tool, setTool] = useState<BenchmarkTool>("vllm_serving");
  const [perfParams, setPerfParams] = useState(DEFAULT_PERF_PARAMS);
  const [accParams, setAccParams] = useState(DEFAULT_ACCURACY_PARAMS);
  const [extraParamsText, setExtraParamsText] = useState("");
  const [namespace, setNamespace] = useState("");
  const [image, setImage] = useState("");

  const kind = TOOL_TO_KIND[tool];

  // Deduplicated model_name list for the dropdown.
  const modelOptions = useMemo(() => {
    if (!models) return [] as string[];
    return Array.from(new Set(models.map((m) => m.model_name))).sort();
  }, [models]);

  // Only Ready serving deployments can be benchmarked directly.
  const readyDeployments = useMemo(
    () => (deployments ?? []).filter((d) => d.ready_replicas > 0),
    [deployments],
  );
  const selectedDeployment = readyDeployments.find((d) => d.id === deploymentId) ?? null;

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
      if (perfParams.tokenizer.trim() !== "") {
        params.tokenizer = perfParams.tokenizer.trim();
      }
      // PVC override only applies to a raw model_name target; deployment targets
      // mount their own PVC.
      if (!deploymentId && perfParams.pvc_name.trim() !== "") {
        params.pvc_name = perfParams.pvc_name.trim();
      }
      if (!deploymentId && perfParams.pvc_mount_path.trim() !== "") {
        params.pvc_mount_path = perfParams.pvc_mount_path.trim();
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

  // Best-effort body for the live YAML preview (never throws on bad JSON).
  const previewBody = useMemo((): CreateBenchmarkRequest | null => {
    if (!deploymentId && !modelName.trim()) return null;
    const extras = parseExtras();
    const body: CreateBenchmarkRequest = {
      tool,
      params: { ...buildNamedParams(), ...(extras.ok ? extras.value : {}) },
    };
    if (deploymentId) {
      body.deployment_id = deploymentId;
      if (ephemeral) {
        body.ephemeral = true;
        const text = servingOverridesText.trim();
        if (text) {
          try {
            const parsed = JSON.parse(text);
            if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
              body.serving_overrides = parsed as Record<string, unknown>;
            }
          } catch {
            /* ignore — preview stays on last valid input */
          }
        }
      }
    } else {
      body.model_name = modelName.trim();
    }
    if (clusterId) body.cluster_id = clusterId;
    if (namespace.trim()) body.namespace = namespace.trim();
    if (image.trim()) body.image = image.trim();
    return body;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    deploymentId, modelName, tool, perfParams, accParams, extraParamsText,
    ephemeral, servingOverridesText, clusterId, namespace, image,
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
    if (!deploymentId && !modelName.trim()) {
      toast.error(t("errorTargetRequired"));
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
      params: { ...buildNamedParams(), ...extras.value },
    };
    // Prefer a portal-managed serving deployment (hit directly); else a LiteLLM alias.
    if (deploymentId) {
      body.deployment_id = deploymentId;
      if (ephemeral) {
        body.ephemeral = true;
        const text = servingOverridesText.trim();
        if (text) {
          try {
            const parsed = JSON.parse(text);
            if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
              toast.error(t("errorExtrasNotObject"));
              return;
            }
            body.serving_overrides = parsed as Record<string, unknown>;
          } catch (err) {
            toast.error(
              t("errorExtrasJsonInvalid", {
                message: err instanceof Error ? err.message : String(err),
              }),
            );
            return;
          }
        }
      }
    } else {
      body.model_name = modelName.trim();
    }
    if (clusterId) body.cluster_id = clusterId;
    if (namespace.trim()) body.namespace = namespace.trim();
    if (image.trim()) body.image = image.trim();

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
                className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                value={clusterId}
                onChange={(e) => setClusterId(e.target.value)}
              >
                <option value="">{t("clusterDefault")}</option>
                {(clusters ?? []).map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                    {c.is_default ? " ★" : ""}
                    {c.api_server ? ` — ${c.api_server}` : ""}
                  </option>
                ))}
              </select>
              <p className="text-xs text-muted-foreground">{t("clusterHint")}</p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="deployment">{t("deploymentLabel")}</Label>
              <select
                id="deployment"
                className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                value={deploymentId}
                onChange={(e) => setDeploymentId(e.target.value)}
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
              {selectedDeployment && (
                <p className="font-mono text-xs text-muted-foreground">
                  {selectedDeployment.model_path}
                </p>
              )}
            </div>

            {deploymentId && (
              <div className="space-y-2 rounded-md border border-dashed p-3">
                <label className="flex items-start gap-2 text-sm">
                  <input
                    type="checkbox"
                    className="mt-0.5 size-4 rounded border-input"
                    checked={ephemeral}
                    onChange={(e) => setEphemeral(e.target.checked)}
                  />
                  <span>
                    {t("ephemeralLabel")}
                    <span className="mt-0.5 block text-xs font-normal text-muted-foreground">
                      {t("ephemeralHint")}
                    </span>
                  </span>
                </label>
                {ephemeral && (
                  <div className="space-y-1.5">
                    <Label htmlFor="serving_overrides">{t("servingOverridesLabel")}</Label>
                    <textarea
                      id="serving_overrides"
                      rows={3}
                      className="flex w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-xs shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                      placeholder='{"gpu_count": 2, "gpu_type": "NVIDIA-H100"}'
                      value={servingOverridesText}
                      onChange={(e) => setServingOverridesText(e.target.value)}
                    />
                    <p className="text-xs text-muted-foreground">{t("servingOverridesHint")}</p>
                  </div>
                )}
              </div>
            )}

            <div className="space-y-1.5">
              <Label htmlFor="model_name">{t("modelLabel")}</Label>
              <select
                id="model_name"
                className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm disabled:opacity-50"
                value={modelName}
                onChange={(e) => setModelName(e.target.value)}
                disabled={modelsLoading || !!deploymentId}
              >
                <option value="">{t("modelPlaceholder")}</option>
                {modelOptions.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
              <p className="text-xs text-muted-foreground">
                {deploymentId ? t("modelHintDisabled") : t("modelHint")}
              </p>
            </div>

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
                showPvcOverride={!deploymentId}
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
            <textarea
              id="extra_params"
              rows={5}
              spellCheck={false}
              placeholder={'{\n  "request_rate": 4,\n  "ignore_eos": true\n}'}
              className="flex min-h-[100px] w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-xs shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
              value={extraParamsText}
              onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) =>
                setExtraParamsText(e.target.value)
              }
            />
            <p className="text-xs text-muted-foreground">{t("extraParamsHint")}</p>
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
                value={namespace}
                onChange={(e) => setNamespace(e.target.value)}
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
          </CardContent>
        </Card>

        <div className="flex items-center justify-end gap-3">
          <Link href="/admin/benchmarks">
            <Button type="button" variant="outline">
              {tc("cancel")}
            </Button>
          </Link>
          <Button type="submit" disabled={createMutation.isPending}>
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
  showPvcOverride,
}: {
  params: typeof DEFAULT_PERF_PARAMS;
  onChange: (next: typeof DEFAULT_PERF_PARAMS) => void;
  showPvcOverride: boolean;
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
        <Label htmlFor="tokenizer">{t("tokenizerLabel")}</Label>
        <Input
          id="tokenizer"
          placeholder={t("tokenizerPlaceholder")}
          value={params.tokenizer}
          onChange={(e) => onChange({ ...params, tokenizer: e.target.value })}
        />
        <p className="text-xs text-muted-foreground">{t("tokenizerHint")}</p>
      </div>
      {showPvcOverride && (
        <div className="space-y-1.5">
          <Label>{t("pvcOverrideLabel")}</Label>
          <div className="grid grid-cols-2 gap-3">
            <Input
              id="pvc_name"
              placeholder={t("pvcNamePlaceholder")}
              value={params.pvc_name}
              onChange={(e) => onChange({ ...params, pvc_name: e.target.value })}
            />
            <Input
              id="pvc_mount_path"
              placeholder={t("pvcMountPlaceholder")}
              value={params.pvc_mount_path}
              onChange={(e) => onChange({ ...params, pvc_mount_path: e.target.value })}
            />
          </div>
          <p className="text-xs text-muted-foreground">{t("pvcOverrideHint")}</p>
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
}: {
  id: string;
  label: string;
  hint: string;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        type="number"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      <p className="text-xs text-muted-foreground">{hint}</p>
    </div>
  );
}
